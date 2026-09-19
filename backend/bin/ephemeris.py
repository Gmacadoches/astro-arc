#!/usr/bin/env python3
"""Where the planets are: the only astronomy Astro-Arc does, on Astronomy Engine.

astro_engine.py needs three things from an ephemeris, and this module is all of
them: each body's tropical ecliptic longitude of date and whether it is
retrograde; the Ascendant, Midheaven and Placidus house cusps for a place and
time. Everything else (signs, aspects, the texture) is derived from those.

Astronomy Engine (backend/vendor/astronomy, MIT, standard library only) replaced
pyswisseph on 2026-09-19, so the plugin needs no Python packages at all. It is
accurate to about an arcminute; pyswisseph with the Moshier ephemeris was
accurate to better than that, and it does not matter here. The comparison that
justified the switch, 1900-2100 and four birth charts:
  - longitudes: mean 0.02'-0.17', worst 1.1' (the Moon); no sign and no
    retrograde flag ever differed
  - Placidus cusps, latitudes -60..60: worst 0.96'; defined/undefined agreed
    in every case
  - the pipeline's own outputs for every day of 2026: natal charts identical;
    the dial differed on 1-2 days a year per chart (an aspect within an
    arcminute of its orb limit), and weekly/monthly named a different dominant
    transit on at most 4 days (two aspects tied to within an arcminute)
Re-run that comparison before trusting a newer astronomy.py.

Astronomy Engine has no house systems, so Placidus is computed below from its
sidereal time and obliquity, by the standard semi-arc iteration.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "astronomy"))
import astronomy as ae  # noqa: E402

# Classical + modern planets, in the order every chart lists them.
BODIES = {
    "sun": ae.Body.Sun,
    "moon": ae.Body.Moon,
    "mercury": ae.Body.Mercury,
    "venus": ae.Body.Venus,
    "mars": ae.Body.Mars,
    "jupiter": ae.Body.Jupiter,
    "saturn": ae.Body.Saturn,
    "uranus": ae.Body.Uranus,
    "neptune": ae.Body.Neptune,
    "pluto": ae.Body.Pluto,
}


def _time(dt_utc):
    return ae.Time.Make(dt_utc.year, dt_utc.month, dt_utc.day, dt_utc.hour, dt_utc.minute,
                        dt_utc.second + dt_utc.microsecond / 1e6)


def _longitude(body, t):
    """Geocentric, aberration-corrected, true ecliptic of date — the same frame
    as pyswisseph's default, which is the tropical zodiac astrology uses."""
    return ae.Ecliptic(ae.GeoVector(body, t, True)).elon % 360.0


def positions(dt_utc):
    """{body: (longitude_degrees, retrograde)} at a timezone-aware UTC datetime.

    Retrograde means the longitude is decreasing, measured across two hours
    centred on the instant; the Moon, the fastest body, moves about a degree in
    that time, so the sign of the change is never ambiguous."""
    t = _time(dt_utc)
    out = {}
    for name, body in BODIES.items():
        before = _longitude(body, t.AddDays(-1 / 24))
        after = _longitude(body, t.AddDays(1 / 24))
        speed = ((after - before + 540.0) % 360.0) - 180.0
        out[name] = (_longitude(body, t), speed < 0)
    return out


def placidus(dt_utc, lat, lon):
    """(cusps, ascendant, midheaven) in degrees, cusps[0] being the 1st house,
    or None where Placidus is undefined (near the poles, where part of the
    ecliptic never rises or sets). `lon` is east-positive."""
    t = _time(dt_utc)
    # True obliquity of date. A private accessor, used deliberately: the file is
    # vendored at a pinned version, so it cannot change underneath this.
    eps = math.radians(t._etilt().tobl)
    ramc = (ae.SiderealTime(t) * 15.0 + lon) % 360.0
    phi = math.radians(lat)
    r = math.radians(ramc)

    mc = math.degrees(math.atan2(math.sin(r), math.cos(r) * math.cos(eps))) % 360.0
    asc = math.degrees(math.atan2(
        math.cos(r), -(math.sin(r) * math.cos(eps) + math.tan(phi) * math.sin(eps)))) % 360.0

    def longitude_of_ra(ra_deg):
        a = math.radians(ra_deg)
        return math.degrees(math.atan2(math.sin(a), math.cos(a) * math.cos(eps))) % 360.0

    def semi_diurnal_arc(longitude):
        dec = math.asin(math.sin(eps) * math.sin(math.radians(longitude)))
        x = -math.tan(phi) * math.tan(dec)
        if abs(x) > 1:
            raise ValueError("circumpolar")
        return math.degrees(math.acos(x))

    def cusp(fraction, below_horizon):
        # Placidus trisects each quadrant's semi-arc in time. Above the horizon
        # the cusp's right ascension is RAMC + f*SDA; below it is measured back
        # from the IC, RAMC + 180 - f*NSA with NSA = 180 - SDA. The semi-arc
        # depends on the cusp's own declination, hence the iteration, which
        # converges in a handful of steps everywhere Placidus is defined.
        ra = ramc + (180.0 - fraction * 90.0 if below_horizon else fraction * 90.0)
        for _ in range(50):
            sda = semi_diurnal_arc(longitude_of_ra(ra))
            new = ramc + (180.0 - fraction * (180.0 - sda) if below_horizon else fraction * sda)
            done = abs(((new - ra + 540.0) % 360.0) - 180.0) < 1e-7
            ra = new
            if done:
                break
        return longitude_of_ra(ra)

    try:
        c11, c12 = cusp(1 / 3, False), cusp(2 / 3, False)
        c2, c3 = cusp(2 / 3, True), cusp(1 / 3, True)
    except ValueError:
        return None

    cusps = [asc, c2, c3, (mc + 180) % 360, (c11 + 180) % 360, (c12 + 180) % 360,
             (asc + 180) % 360, (c2 + 180) % 360, (c3 + 180) % 360, mc, c11, c12]
    return cusps, asc, mc
