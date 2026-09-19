"""Bounded reads for every HTTP response this plugin parses.

`resp.read()` with no argument is unbounded: it keeps allocating until the
peer stops sending. A timeout does not help, because a peer that streams
steadily never hits one. So nothing here ever calls it — a response is read
through a cap and refused the moment it exceeds it, before anything is
decoded, parsed or written.

The caps are deliberately far above any real reply (a chat completion is
kilobytes; a 1536x1024 PNG is low single-digit megabytes) and far below
anything that would trouble a desktop: the point is to fail fast on a
response that has stopped making sense, not to second-guess the API.
"""

import json

# A chat completion, or an /images/generations envelope carrying one
# base64 PNG. The image envelope is the big one: base64 costs 4 bytes per
# 3, so 64 MiB leaves room for ~48 MiB of PNG, and OpenAI's largest render
# is nowhere near that.
MAX_JSON_BYTES = 64 * 1024 * 1024
# The decoded image that envelope is allowed to yield, before it is written.
MAX_IMAGE_BYTES = 48 * 1024 * 1024
# An error body exists to be quoted into a message, so it needs far less.
MAX_ERROR_BYTES = 64 * 1024


class ResponseTooLarge(RuntimeError):
    """A response exceeded its cap and was refused unparsed."""


def read_capped(stream, limit):
    """Read at most `limit` bytes from `stream`, raising if more are there.

    Reads limit + 1 so the overflow is detected from the read itself; a
    response exactly at the cap is still fine.
    """
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ResponseTooLarge(
            f"Response exceeded the {limit} byte limit and was not read."
        )
    return data


def read_json_capped(resp, limit=MAX_JSON_BYTES):
    """Parse a JSON response body read through a cap.

    json.loads rejects trailing bytes after the top-level value itself, so
    a truncated or padded body fails here rather than parsing to something
    half-right.
    """
    raw = read_capped(resp, limit)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ResponseTooLarge(f"Response was not valid JSON: {exc}") from None


def read_error_capped(exc, limit=MAX_ERROR_BYTES):
    """The body of an HTTPError, read through a cap, as text.

    Never includes headers: on these calls they carried the API key.
    """
    try:
        return read_capped(exc, limit).decode(errors="replace")
    except ResponseTooLarge:
        return "(error body too large to quote)"


def b64_image_bytes(b64_text, limit=MAX_IMAGE_BYTES):
    """Decode base64 image data, refusing anything over `limit` bytes.

    The encoded length bounds the decoded length (3 bytes out per 4 in), so
    the check happens before the decode allocates, not after.
    """
    import base64

    if not isinstance(b64_text, str):
        raise ResponseTooLarge("Image payload was not a base64 string.")
    if len(b64_text) // 4 * 3 > limit:
        raise ResponseTooLarge(
            f"Image payload exceeded the {limit} byte limit and was not decoded."
        )
    data = base64.b64decode(b64_text, validate=True)
    if len(data) > limit:
        raise ResponseTooLarge(
            f"Image payload exceeded the {limit} byte limit and was not written."
        )
    return data
