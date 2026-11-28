"""The anime embed is framed, and only the anime embed.

ani.pm documents that an embed opened as a page redirects to the title page
instead of playing, so the desktop players load a tiny wrapper document with
the embed in a full-screen iframe.  These tests pin the wrapper's contract:
the URL is escaped, the allow list is the documented one, there is no sandbox
attribute, and every other provider keeps its direct load.
"""

from __future__ import annotations

from lgl import embed_frame, streaming

ANI = "https://ani.pm/embed/ani/21/3/sub"
ANI_MAL = "https://ani.pm/embed/mal/5114/1/dub"


def test_the_wrapper_frames_the_embed_full_screen() -> None:
    html = embed_frame.build_embed_frame_html(ANI)
    assert html.startswith("<!doctype html>")
    assert f'<iframe src="{ANI}"' in html
    assert "allowfullscreen" in html
    assert "position:fixed;inset:0;width:100%;height:100%" in html
    assert html.rstrip().endswith("</body></html>")


def test_the_wrapper_allows_what_the_provider_documents() -> None:
    html = embed_frame.build_embed_frame_html(ANI)
    assert (
        'allow="autoplay; fullscreen; picture-in-picture; '
        'encrypted-media; screen-wake-lock"' in html
    )


def test_the_wrapper_has_no_sandbox_attribute() -> None:
    assert "sandbox" not in embed_frame.build_embed_frame_html(ANI)


def test_the_embed_url_cannot_break_out_of_the_attribute() -> None:
    html = embed_frame.build_embed_frame_html(
        'https://ani.pm/embed/ani/1/1/sub"><script>alert(1)</script>&x=\'y\''
    )
    assert "<script>" not in html
    assert '"><script>' not in html
    assert "&quot;&gt;&lt;script&gt;" in html
    assert "&amp;x=&#39;y&#39;" in html


def test_escaping_handles_every_character_that_matters() -> None:
    assert embed_frame.escape_attribute('<&>"\'') == (
        "&lt;&amp;&gt;&quot;&#39;"
    )


def test_only_ani_embeds_are_framed() -> None:
    assert embed_frame.frame_html_for(ANI) is not None
    assert embed_frame.frame_html_for(ANI_MAL) is not None
    assert embed_frame.frame_html_for(ANI, streaming.ANI_PROVIDER) is not None
    # Another provider's URL, and another provider asked explicitly.
    cinesrc = "https://cinesrc.st/embed/movie/603"
    assert embed_frame.frame_html_for(cinesrc) is None
    assert embed_frame.frame_html_for(ANI, "cinesrc") is None
    assert embed_frame.frame_html_for(ANI, "not-a-provider") is None


def test_the_ani_host_check_still_decides() -> None:
    # A lookalike host, plain HTTP and another path never reach the wrapper.
    assert embed_frame.frame_html_for("https://ani.pm.evil/embed/ani/1/1/sub") is None
    assert embed_frame.frame_html_for("http://ani.pm/embed/ani/1/1/sub") is None
    assert embed_frame.frame_html_for("https://ani.pm/watch/ani/1/1/sub") is None
    assert embed_frame.frame_html_for("") is None
    assert embed_frame.frame_html_for(None) is None


def test_the_base_is_a_real_https_origin_of_its_own() -> None:
    base = embed_frame.EMBED_FRAME_BASE
    assert base.startswith("https://")
    assert base.endswith("/")
    assert "lumora.local" not in base
    # Same-origin with the provider would change how the embed sees its parent.
    assert streaming.SPECS[streaming.ANI_PROVIDER].host not in base
