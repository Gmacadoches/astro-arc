#!/usr/bin/env python3
"""Astro-Arc's astrology engine: natal chart + current transits, and the
derived "current arc" for whatever frequency the user has configured.

Phase 2 of the project plan — CLI-only, no image generation yet. Run
directly to inspect the symbolic brief this cycle would hand to the image
step in Phase 3:

    ~/.local/share/omarchy/astro-arc/venv/bin/python3 astro_engine.py

Reads birth data + location + frequency from
~/.local/state/omarchy/settings/astro-arc.json (the same file the widget's
Panel.qml writes to) and prints one JSON object to stdout.
"""

import datetime
import json
import math
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import swisseph as swe
from timezonefinder import TimezoneFinder

sys.path.insert(0, str(Path(__file__).resolve().parent))
from period_key import period_key  # noqa: E402  — see that file: one definition, called from everywhere

# Overridable via ASTRO_ARC_CONFIG for testing against a scratch file
# without ever touching the user's real saved birth data.
CONFIG_PATH = Path(os.environ.get(
    "ASTRO_ARC_CONFIG",
    str(Path.home() / ".local/state/omarchy/settings/astro-arc.json"),
))

# ---- Reference tables --------------------------------------------------

SIGNS = [
    ("Aries", "fire", "cardinal"),
    ("Taurus", "earth", "fixed"),
    ("Gemini", "air", "mutable"),
    ("Cancer", "water", "cardinal"),
    ("Leo", "fire", "fixed"),
    ("Virgo", "earth", "mutable"),
    ("Libra", "air", "cardinal"),
    ("Scorpio", "water", "fixed"),
    ("Sagittarius", "fire", "mutable"),
    ("Capricorn", "earth", "cardinal"),
    ("Aquarius", "air", "fixed"),
    ("Pisces", "water", "mutable"),
]

# Body id -> (name, swisseph constant). Classical + modern planets only for
# v1 — nodes/Chiron/asteroids can be added once the symbolic model needs them.
BODIES = [
    ("sun", swe.SUN),
    ("moon", swe.MOON),
    ("mercury", swe.MERCURY),
    ("venus", swe.VENUS),
    ("mars", swe.MARS),
    ("jupiter", swe.JUPITER),
    ("saturn", swe.SATURN),
    ("uranus", swe.URANUS),
    ("neptune", swe.NEPTUNE),
    ("pluto", swe.PLUTO),
]
OUTER_BODIES = {"mars", "jupiter", "saturn", "uranus", "neptune", "pluto"}

# aspect name -> (angle, max orb)
ASPECTS = {
    "conjunction": (0, 8),
    "sextile": (60, 4),
    "square": (90, 6),
    "trine": (120, 6),
    "opposition": (180, 8),
}

MOON_PHASES = [
    (0, "New Moon"),
    (45, "Waxing Crescent"),
    (90, "First Quarter"),
    (135, "Waxing Gibbous"),
    (180, "Full Moon"),
    (225, "Waning Gibbous"),
    (270, "Last Quarter"),
    (315, "Waning Crescent"),
]


class AstroError(Exception):
    pass


def load_config():
    if not CONFIG_PATH.exists():
        raise AstroError(f"No config at {CONFIG_PATH} yet — set birth data and location in the Astro-Arc widget first.")
    data = json.loads(CONFIG_PATH.read_text())
    if not data.get("birthDate"):
        raise AstroError("No birth date set yet — set it in the Astro-Arc widget first.")
    if data.get("latitude") is None or data.get("longitude") is None:
        raise AstroError("No location set yet — set one in the Astro-Arc widget first.")
    return data


def angle_diff(a, b):
    """Smallest angular distance between two ecliptic longitudes, 0-180."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def sign_for(longitude):
    index = int(longitude // 30) % 12
    name, element, modality = SIGNS[index]
    degree_in_sign = longitude - index * 30
    return {
        "sign": name,
        "signIndex": index,
        "element": element,
        "modality": modality,
        "degreeInSign": round(degree_in_sign, 4),
    }


def moon_phase_name(sun_lon, moon_lon):
    angle = (moon_lon - sun_lon) % 360
    best = min(MOON_PHASES, key=lambda p: min(abs(angle - p[0]), 360 - abs(angle - p[0])))
    return best[1], round(angle, 2)


def julian_day_ut(dt_utc):
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day,
                       dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600)


def compute_bodies(jd_ut):
    """Ecliptic longitude + retrograde flag for every tracked body at jd_ut."""
    out = {}
    for name, body_id in BODIES:
        (lon, _lat, _dist, _speed_lon, _speed_lat, _speed_dist), _flags = swe.calc_ut(
            jd_ut, body_id, swe.FLG_SPEED
        )
        entry = sign_for(lon)
        entry["longitude"] = round(lon, 4)
        entry["retrograde"] = _speed_lon < 0
        out[name] = entry
    return out


def compute_houses(jd_ut, lat, lon):
    try:
        cusps, ascmc = swe.houses(jd_ut, lat, lon, b"P")  # Placidus
    except swe.Error:
        return None  # e.g. undefined at extreme polar latitudes
    ascendant = sign_for(ascmc[0])
    ascendant["longitude"] = round(ascmc[0], 4)
    midheaven = sign_for(ascmc[1])
    midheaven["longitude"] = round(ascmc[1], 4)
    return {
        "ascendant": ascendant,
        "midheaven": midheaven,
        "cusps": [round(c, 4) for c in cusps],
    }


def house_of(longitude, cusps):
    if not cusps:
        return None
    for house_index in range(12):
        start = cusps[house_index]
        end = cusps[(house_index + 1) % 12]
        span = (end - start) % 360
        offset = (longitude - start) % 360
        if offset < span:
            return house_index + 1
    return None


def element_modality_balance(bodies):
    element_count = {"fire": 0, "earth": 0, "air": 0, "water": 0}
    modality_count = {"cardinal": 0, "fixed": 0, "mutable": 0}
    for entry in bodies.values():
        element_count[entry["element"]] += 1
        modality_count[entry["modality"]] += 1
    dominant_element = max(element_count, key=element_count.get)
    dominant_modality = max(modality_count, key=modality_count.get)
    return element_count, modality_count, dominant_element, dominant_modality


def best_aspect(angle):
    """Closest-matching aspect for an angular separation, or None if none
    of the tracked aspects are within their orb."""
    best = None
    for aspect_name, (target_angle, max_orb) in ASPECTS.items():
        orb = abs(angle - target_angle)
        if orb <= max_orb and (best is None or orb < best["orb"]):
            best = {"aspect": aspect_name, "orb": round(orb, 3)}
    return best


def find_dominant_transit(transit_bodies, natal_bodies, transit_names, houses):
    """Best (tightest-orb) aspect among transit_names -> any natal body."""
    best = None
    for t_name in transit_names:
        t_entry = transit_bodies[t_name]
        for n_name, n_entry in natal_bodies.items():
            angle = angle_diff(t_entry["longitude"], n_entry["longitude"])
            hit = best_aspect(angle)
            if hit and (best is None or hit["orb"] < best["orb"]):
                house = house_of(t_entry["longitude"], houses["cusps"]) if houses else None
                best = {
                    "transitingBody": t_name,
                    "natalBody": n_name,
                    "aspect": hit["aspect"],
                    "orb": hit["orb"],
                    "transitingSign": t_entry["sign"],
                    "transitingHouse": house,
                    "retrograde": t_entry["retrograde"],
                }
    return best


# ---- Texture: the four axes the image's SHAPE is driven by ---------------
#
# Added 2026-09-12. Everything above describes *what* the sky is doing; this
# describes how today should FEEL to compose, and it exists because the pipeline
# had exactly one image shape. Every generation got the same mandates — six to ten
# objects across three depths, two colliding registers, one unexplained intrusion
# — so every image carried the same implicit meaning (chaos, tension between many
# unrelated things) no matter what the chart said. The astrology modulated
# vocabulary and never shape. These axes are what the shape becomes a function of.
#
# Four INDEPENDENT axes, deliberately not one "tension" scalar. A single scalar was
# built and measured first: it is bimodal (the quantiles jump from 0.491 at p50 to
# 0.750 at p60, so it collapses into the hard/soft binary it contains) and it ranks
# a nearly-absent conjunction (orb 6.68 of an 8 allowance) ABOVE an essentially
# exact sextile (orb 0.16). Worse, one scalar makes "is today hard" and "how much
# is happening" the same question, so the system can only say anxious-and-crowded
# or serene-and-empty — which replaces one constant implicit meaning with a
# one-dimensional one.
#
# Measured over 365 days against this install's natal chart: 63 of the available
# cells actually get used, the most common holds 5.5% of days, and consecutive days
# land in the same cell only 8% of the time. The previous behavior was one cell,
# 100% of days.
#
# Only the Moon is sampled by default, and that is deliberate: the requirement is
# that DAILY variation dominate. The all-bodies aspect count was measured at 21-29
# hits with multi-week plateaus — that is a season, not a day. Moon-only gives 1-6
# with real day-to-day movement.
#
# Deliberately NOT used as daily knobs, all measured:
#   - retrograde: find_dominant_transit is called with ["moon"] and records the
#     *transiting* body's flag; the Moon is never retrograde, so it is always
#     False. "Is anything retrograde" is true on 318 of 365 days.
#   - element/modality balance: computed on natal bodies only, so fixed for life.
#     The transiting version is degenerate (dominant element is fire on 236 of
#     365 days, because it counts ten bodies including the clustered outers).

HARD_ASPECTS = {"conjunction", "square", "opposition"}

# Thresholds are QUANTILES of the real measured distribution, not even splits.
# This matters more than it looks: find_dominant_transit selects by *minimum* orb,
# so closeness is strongly skewed high (mean 0.76). Naive terciles at 0.33/0.67
# would put ~80% of days in "high" and rebuild the very constant this replaces.
# These cuts give 20% / 66% / 14% — most days normal, a fifth an excursion each way.
#
# They are calibrated to THIS natal chart. A chart with a stellium yields fewer
# aspect hits and wider orbs, so "high" might never fire. To recalibrate for a
# different chart, sweep compute_bodies over 365 days (0.35s) and take the
# quantiles of normalized closeness.
INTENSITY_CUTS = (0.46, 0.92)

# Exposure gets even terciles rather than excursion cuts, because unlike intensity
# there is no "normal" illumination to excurse from — all three states are equally
# ordinary and the axis is most informative when each is equally reachable.
# Measured terciles of illuminated fraction over 365 days: 0.244 / 0.738, giving
# 120 / 125 / 120 days. Naive cuts at 0.34/0.67 were tried first and gave
# 146/81/138 (40/22/38%) — illuminated fraction is cosine-shaped, so it clusters
# near dark and near full and thins out the middle.
EXPOSURE_CUTS = (0.24, 0.74)


def normalized_closeness(aspect_name, orb):
    """How exact an aspect is, 0..1, comparable ACROSS aspect types.

    Raw orb is not comparable: ASPECTS allows 8 degrees for conjunction and
    opposition, 6 for square and trine, but only 4 for sextile — so a 3-degree
    sextile is nearly out of orb while a 3-degree opposition is still tight.

    Note this is additive and is NOT used by find_dominant_transit, which keeps
    selecting on raw orb. Changing that selection would change which aspect is
    called dominant, and therefore change Stage 1's input, for every chart.
    """
    max_orb = ASPECTS[aspect_name][1]
    return round(1.0 - (orb / max_orb), 4)


def _band(value, cuts):
    low, high = cuts
    if value < low:
        return "lo"
    return "mid" if value < high else "hi"


def compute_texture(transit_bodies, natal_bodies, moon_phase_angle, transit_names=("moon",)):
    """The four axes, plus the full hit list find_dominant_transit throws away.

    Returns None only if there is nothing to measure at all. A day with no aspect
    in orb (1 day in 365) is a DEFINED cell — soft at minimum intensity — not an
    exception branch, because an exception branch is how a neutral day ends up
    looking like a crash.
    """
    hits = []
    for t_name in transit_names:
        t_entry = transit_bodies.get(t_name)
        if not t_entry:
            continue
        for n_name, n_entry in natal_bodies.items():
            hit = best_aspect(angle_diff(t_entry["longitude"], n_entry["longitude"]))
            if not hit:
                continue
            hits.append({
                "transitingBody": t_name,
                "natalBody": n_name,
                "aspect": hit["aspect"],
                "orb": hit["orb"],
                "closeness": normalized_closeness(hit["aspect"], hit["orb"]),
                "family": "hard" if hit["aspect"] in HARD_ASPECTS else "soft",
            })

    # Tightest by NORMALIZED closeness, which is not necessarily the same hit
    # find_dominant_transit picks by raw orb — that is the point of normalizing.
    tightest = max(hits, key=lambda h: h["closeness"]) if hits else None

    if tightest is None:
        polarity, intensity = "none", 0.0
    else:
        polarity, intensity = tightest["family"], tightest["closeness"]

    # Illuminated fraction of the Moon. Genuinely independent of the aspect axes:
    # natal positions are fixed, so this depends only on the transiting Sun.
    # Measured near-uniform over a year (116 dark / 124 half / 125 full), which is
    # what makes "calm but dark" and "intense but bright" reachable at all —
    # combinations a single tension scalar cannot express.
    exposure = (1.0 - math.cos(math.radians(moon_phase_angle))) / 2.0

    return {
        "polarity": polarity,
        "intensity": round(intensity, 4),
        "intensityBand": _band(intensity, INTENSITY_CUTS) if tightest else "lo",
        "multiplicity": len(hits),
        "exposure": round(exposure, 4),
        "exposureBand": _band(exposure, EXPOSURE_CUTS),
        "tightest": tightest,
        "hits": sorted(hits, key=lambda h: -h["closeness"]),
    }


def derive_arc(frequency, now_utc, natal, transits_now, now_local=None):
    """`now_local` is what period keys are derived from — the user's civil day,
    not the UTC day.

    These two used to be the same value, and that was a real bug: the period key
    is the cache key for Stage 1/Stage 1.5 (llm_pipeline.py keys
    READINGS_DIR/AMPLIFICATIONS_DIR on it), while astro-arc-generate named its
    output files from `date +%Y-%m-%d` — the *local* date. After ~18:00 local the
    two disagree, so an evening run wrote its reading into tomorrow's cache slot
    and the next day's run was served yesterday evening's astrology. That really
    happened: pipeline-meta-2026-09-10.json and -2026-09-11.json share a
    byte-identical reading, amplification and intrusion, and the only thing that
    differed between those two days' images was the register/style roulette.

    Local is the right choice rather than UTC because this generates a desktop
    background: "today" means the user's day. Note it is the *system* local zone,
    not the birth-location zone TimezoneFinder derives in main() — someone born
    in Tokyo and living in Denver gets Denver days.

    Positions are still computed from `now_utc`; only the key is civil-local.
    """
    if now_local is None:
        now_local = now_utc.astimezone()
    natal_bodies = natal["planets"]
    transit_bodies = transits_now["planets"]
    houses = natal.get("houses")

    # Hoisted above the frequency split because every branch now carries a
    # texture block, and exposure needs the phase angle.
    phase_name, phase_angle = moon_phase_name(
        transit_bodies["sun"]["longitude"], transit_bodies["moon"]["longitude"]
    )

    if frequency == "weekly":
        dominant = find_dominant_transit(
            transit_bodies, natal_bodies, [n for n, _ in BODIES], houses
        )
        return {
            "frequency": "weekly",
            "periodKey": period_key("weekly", now_local),
            "dominantTransit": dominant,
            # Sampled over the same body set this frequency's dominant transit
            # uses. Note multiplicity saturates here (all-bodies hit counts were
            # measured at 21-29 with multi-week plateaus), so on the weekly and
            # monthly paths treat multiplicity as a season, not a variable — only
            # the daily path gives it real day-to-day movement.
            "texture": compute_texture(
                transit_bodies, natal_bodies, phase_angle,
                transit_names=[n for n, _ in BODIES],
            ),
        }

    if frequency == "monthly":
        sun = transit_bodies["sun"]
        house = house_of(sun["longitude"], houses["cusps"]) if houses else None
        dominant_outer = find_dominant_transit(
            transit_bodies, natal_bodies, sorted(OUTER_BODIES), houses
        )
        return {
            "frequency": "monthly",
            "periodKey": period_key("monthly", now_local),
            "transitingSun": {"sign": sun["sign"], "house": house},
            "dominantOuterTransit": dominant_outer,
            # Outers only, matching dominantOuterTransit. See the weekly note on
            # multiplicity saturation.
            "texture": compute_texture(
                transit_bodies, natal_bodies, phase_angle,
                transit_names=sorted(OUTER_BODIES),
            ),
        }

    # daily (default) — and "hourly", deliberately. An hourly schedule is about
    # how often a new IMAGE appears, not about the sky: transits barely move in
    # an hour, so an hourly arc would be the daily one with a longer key and 23
    # redundant readings a day to pay for. Falling through to here keys the arc
    # (and so Stage 1, Stage 1.5 and the signature caches) by date, and each
    # hourly run re-rolls only Stage 2's imagery against the day's reading.
    moon = transit_bodies["moon"]
    dominant = find_dominant_transit(transit_bodies, natal_bodies, ["moon"], houses)
    return {
        "frequency": "daily",
        "periodKey": period_key("daily", now_local),
        "moonSign": moon["sign"],
        "moonPhase": phase_name,
        "moonPhaseAngle": phase_angle,
        "dominantMoonTransit": dominant,
        # Moon-only on purpose — the daily layer has to dominate. See
        # compute_texture's header.
        "texture": compute_texture(transit_bodies, natal_bodies, phase_angle),
    }


def main():
    swe.set_ephe_path(None)  # bundled Moshier ephemeris — plenty accurate for this use

    try:
        config = load_config()
    except AstroError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)

    lat = float(config["latitude"])
    lon = float(config["longitude"])
    birth_time_unknown = bool(config.get("birthTimeUnknown"))
    birth_date = config["birthDate"]
    birth_time = "12:00" if birth_time_unknown else config.get("birthTime") or "12:00"

    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lat=lat, lng=lon) or "UTC"
    tz = ZoneInfo(tz_name)

    birth_naive = datetime.datetime.strptime(f"{birth_date} {birth_time}", "%Y-%m-%d %H:%M")
    birth_local = birth_naive.replace(tzinfo=tz)
    birth_utc = birth_local.astimezone(datetime.timezone.utc)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    # The system-local civil clock, which is what the period key is keyed on —
    # see derive_arc()'s docstring for why that is not the same as now_utc.
    now_local = now_utc.astimezone()

    natal_jd = julian_day_ut(birth_utc)
    natal_planets = compute_bodies(natal_jd)
    natal_houses = None if birth_time_unknown else compute_houses(natal_jd, lat, lon)
    element_count, modality_count, dominant_element, dominant_modality = element_modality_balance(natal_planets)

    natal = {
        "planets": natal_planets,
        "houses": natal_houses,
        "elementBalance": element_count,
        "modalityBalance": modality_count,
        "dominantElement": dominant_element,
        "dominantModality": dominant_modality,
    }

    transit_jd = julian_day_ut(now_utc)
    transits_now = {"planets": compute_bodies(transit_jd)}

    frequency = config.get("frequency", "daily")
    arc = derive_arc(frequency, now_utc, natal, transits_now, now_local)

    result = {
        "generatedAt": now_utc.isoformat(),
        "input": {
            "birthDate": birth_date,
            "birthTime": None if birth_time_unknown else birth_time,
            "birthTimeUnknown": birth_time_unknown,
            "locationName": config.get("locationName", ""),
            "latitude": lat,
            "longitude": lon,
            "timezone": tz_name,
            "frequency": frequency,
        },
        "natal": natal,
        "transitsNow": transits_now,
        "arc": arc,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
