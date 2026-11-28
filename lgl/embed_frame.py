"""The small HTML page that frames an anime embed.

anipm.pk's developer docs say an embed must always be framed: opened as a page it
redirects to the title page instead of playing.  The GTK and Qt players
therefore load this wrapper, with the embed in a full-screen ``<iframe>``,
instead of loading the embed URL as the top-level document.

Only the anime provider is framed: every other provider plays correctly when
loaded directly, and its navigation policy stays exactly as it was.  The
wrapper carries no ``sandbox`` attribute - anipm.pk documents that a sandboxed
frame breaks its player.

The document is given :data:`EMBED_FRAME_BASE` as its base URI so it has a
real HTTPS origin.  That origin is deliberately *not* the provider's: a
same-origin wrapper would change how the embed sees its parent.
"""

from __future__ import annotations

from . import streaming

#: Real-looking HTTPS origin for the wrapper document.  It matches the app's
#: brand and is never fetched: it only gives the document an origin, and it
#: must not be the provider's own origin (see the module docstring).
EMBED_FRAME_BASE = "https://player.lumora.app/"

#: What the framed player is allowed to do, as anipm.pk documents it.
EMBED_FRAME_ALLOW = (
    "autoplay; fullscreen; picture-in-picture; encrypted-media; "
    "screen-wake-lock"
)

_HTML_HEAD = (
    '<!doctype html><html><head>'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<style>html,body{margin:0;height:100%;background:#000}"
    "iframe{position:fixed;inset:0;width:100%;height:100%;border:0}"
    "</style></head><body>"
)


def escape_attribute(value: object) -> str:
    """Escape ``value`` so it cannot break out of a double-quoted attribute."""
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_embed_frame_html(embed_url: object) -> str:
    """The wrapper document: a full-screen iframe of one escaped embed URL."""
    src = escape_attribute(embed_url)
    return (
        f'{_HTML_HEAD}<iframe src="{src}" allow="{EMBED_FRAME_ALLOW}"'
        " allowfullscreen></iframe></body></html>"
    )


def frame_html_for(url: object, provider: str | None = None) -> str | None:
    """The wrapper for ``url``, or ``None`` when it must load directly.

    Framing is for the anime provider only, and only for a URL that passes the
    provider's own host check: anything else - every other provider, a URL on
    another host, a lookalike domain - keeps the direct load.
    """
    try:
        spec = streaming.provider_spec(provider or streaming.ANI_PROVIDER)
    except streaming.EmbedUrlError:
        return None
    if spec.key != streaming.ANI_PROVIDER:
        return None
    try:
        checked = streaming.validate_embed_url(spec.key, url)
    except streaming.EmbedUrlError:
        return None
    return build_embed_frame_html(checked)


__all__ = [
    "EMBED_FRAME_ALLOW",
    "EMBED_FRAME_BASE",
    "build_embed_frame_html",
    "escape_attribute",
    "frame_html_for",
]
