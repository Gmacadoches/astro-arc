# Astronomy Engine (vendored)

`astronomy.py` is Astronomy Engine's Python implementation, copied verbatim and
unmodified. It is the whole library: one file, standard library only.

- Source: https://github.com/cosinekitty/astronomy
- File: `source/python/astronomy/astronomy.py` at tag **v2.1.19**
- sha256: `5f3a17c0b14290d084eeb0d29b00b3139cd1703a8d30fa5eb141398a24719261`
- Licence: MIT (`LICENSE`, beside this file), Copyright (c) 2019-2023 Don Cross

It is committed rather than installed so that nothing is fetched or built on a
user's machine: the code that runs is the code in this repository. To update it,
replace `astronomy.py` from a newer tag, update the tag and sha256 above, and
re-run the ephemeris comparison described in `backend/bin/ephemeris.py`.

Astro-Arc uses it through `backend/bin/ephemeris.py` only.
