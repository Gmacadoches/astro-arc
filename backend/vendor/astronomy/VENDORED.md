# Astronomy Engine (vendored)

`astronomy.py` is Astronomy Engine's Python implementation, copied verbatim and
unmodified. It is the whole library: one file, standard library only.

- Source: https://github.com/cosinekitty/astronomy
- File: `source/python/astronomy/astronomy.py` at tag **v2.1.19**
- sha256: `5f3a17c0b14290d084eeb0d29b00b3139cd1703a8d30fa5eb141398a24719261`
- Licence: MIT (`LICENSE`, beside this file), Copyright (c) 2019-2023 Don Cross

It is committed rather than installed so that nothing is fetched or built on a
user's machine: the code that runs is the code in this repository.

To update it:

1. Replace `astronomy.py` from a newer tag.
2. Update the tag and sha256 above, **and the release named in `NOTICE`** — the
   version is recorded in both places and nothing checks that they agree.
3. Check `placidus()`'s use of the private `Time._etilt().tobl` in
   `backend/bin/ephemeris.py`. There is no public obliquity accessor, so that is
   the one call a new version can break outright.
4. Re-run the ephemeris comparison described in `backend/bin/ephemeris.py`. It
   is described there in prose only — no script in this repository implements
   it, so this step means writing it again.

Nothing verifies the sha256 above at build, install or run time; it is a record
of what was vendored, not an enforced check.

Astro-Arc uses it through `backend/bin/ephemeris.py` only.
