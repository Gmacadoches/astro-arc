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
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import swisseph as swe
from timezonefinder import TimezoneFinder

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


def derive_arc(frequency, now_utc, natal, transits_now):
    natal_bodies = natal["planets"]
    transit_bodies = transits_now["planets"]
    houses = natal.get("houses")

    if frequency == "weekly":
        dominant = find_dominant_transit(
            transit_bodies, natal_bodies, [n for n, _ in BODIES], houses
        )
        return {
            "frequency": "weekly",
            "periodKey": now_utc.strftime("%G-W%V"),
            "dominantTransit": dominant,
        }

    if frequency == "monthly":
        sun = transit_bodies["sun"]
        house = house_of(sun["longitude"], houses["cusps"]) if houses else None
        dominant_outer = find_dominant_transit(
            transit_bodies, natal_bodies, sorted(OUTER_BODIES), houses
        )
        return {
            "frequency": "monthly",
            "periodKey": now_utc.strftime("%Y-%m"),
            "transitingSun": {"sign": sun["sign"], "house": house},
            "dominantOuterTransit": dominant_outer,
        }

    # daily (default)
    moon = transit_bodies["moon"]
    sun = transit_bodies["sun"]
    phase_name, phase_angle = moon_phase_name(sun["longitude"], moon["longitude"])
    dominant = find_dominant_transit(transit_bodies, natal_bodies, ["moon"], houses)
    return {
        "frequency": "daily",
        "periodKey": now_utc.strftime("%Y-%m-%d"),
        "moonSign": moon["sign"],
        "moonPhase": phase_name,
        "moonPhaseAngle": phase_angle,
        "dominantMoonTransit": dominant,
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
    arc = derive_arc(frequency, now_utc, natal, transits_now)

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
