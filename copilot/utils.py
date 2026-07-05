"""Low-level protocol and I/O helpers shared by the drivers.

HTTP status checking, WebSocket frame reassembly, and image encoding — small,
stateless utilities with no Copilot-specific logic of their own.
"""

import json
from typing import List, Tuple

from .models import ImageType


def raise_for_status(response):
    """Raise if the response status code indicates an error."""
    if 400 <= response.status_code < 600:
        raise Exception(f"HTTP {response.status_code}: {response.text}")


_decoder = json.JSONDecoder()


def drain_json(buf: bytes) -> Tuple[List[dict], bytes]:
    """Pull all complete JSON objects out of ``buf``.

    WebSocket text frames can arrive fragmented or coalesced, so we parse
    greedily and keep any trailing partial object as leftover bytes.
    """
    out: List[dict] = []
    s = buf.decode("utf-8", errors="ignore")
    idx = 0
    while idx < len(s):
        rest = s[idx:].lstrip()
        if not rest:
            idx = len(s)
            break
        try:
            obj, end = _decoder.raw_decode(rest)
        except json.JSONDecodeError:
            break  # incomplete trailing fragment
        out.append(obj)
        idx = len(s) - len(rest) + end
    return out, s[idx:].encode("utf-8")


def to_bytes(image: ImageType) -> bytes:
    """Convert an image (file path or bytes) to bytes."""
    if isinstance(image, str):
        with open(image, 'rb') as f:
            return f.read()
    return image


def is_accepted_format(data: bytes) -> str:
    """Determine the MIME type of image data."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    elif data.startswith(b'\xff\xd8'):
        return 'image/jpeg'
    return 'application/octet-stream'
