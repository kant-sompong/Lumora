"""Image URLs that every build can decode.

TMDB answers its sized poster paths (``/t/p/w500/<file>.jpg``) with WebP, and a
gdk-pixbuf or Qt install without a WebP decoder then paints no artwork at all.
The ``/t/p/original/<file>`` twin keeps the source JPEG, so a failed decode is
retried against it instead of leaving a blank poster or backdrop.
"""

from __future__ import annotations

import re

#: A TMDB sized-image path: ``https://image.tmdb.org/t/p/w500/<file>``.
_SIZED = re.compile(r"^(https?://image\.tmdb\.org/t/p/)(?:w\d+|h\d+)(/)")


def portable_image_url(url: str) -> str:
    """The JPEG twin of a TMDB sized image, or ``""`` when there is none."""
    text = str(url or "")
    match = _SIZED.match(text)
    if not match:
        return ""
    return f"{match.group(1)}original{match.group(2)}{text[match.end():]}"


__all__ = ["portable_image_url"]
