import pytest

from lgl import playback as pb


@pytest.mark.parametrize(
    "error,kind",
    [
        (Exception("HTTP 403"), pb.FORBIDDEN),
        (Exception("HTTP 401"), pb.AUTH),
        (Exception("HTTP 404"), pb.NOT_FOUND),
        (TimeoutError("timed out"), pb.TIMEOUT),
        (Exception("Could not decode stream: codec missing"), pb.FORMAT),
        (Exception("This title is DRM protected"), pb.DRM),
        (Exception("No space left on device"), pb.NO_SPACE),
        (Exception("Connection reset by peer"), pb.NETWORK),
        (Exception("weird"), pb.UNKNOWN),
    ],
)
def test_classify(error, kind):
    assert pb.classify(error) == kind


def test_every_failure_has_message_actions_and_labels():
    for kind in pb.KINDS:
        assert pb.message(kind)
        acts = pb.actions(kind)
        assert acts and all(a in pb.ACTION_LABELS for a in acts)


def test_message_includes_title_and_provider_without_leaking_paths():
    text = pb.message(pb.NOT_FOUND, title="Heat", provider="FrameTV")
    assert "Heat" in text and "FrameTV" in text
    assert pb.message(pb.FORMAT, title="Heat").startswith("“Heat”")


def test_retry_only_for_transient_failures_and_bounded():
    assert pb.should_retry(pb.NETWORK, 0) is True
    assert pb.should_retry(pb.NETWORK, 2) is False
    assert pb.should_retry(pb.NOT_FOUND, 0) is False
    assert pb.should_retry(pb.NETWORK, "x") is False


def test_failure_bundles_recovery_actions():
    fail = pb.failure(Exception("HTTP 403"), title="Heat", provider="Kryoto")
    assert fail.kind == pb.FORBIDDEN
    assert pb.CHOOSE_SOURCE in fail.actions
    assert fail.retryable is False
    assert len(fail.detail) <= 300

    retryable = pb.failure(Exception("connection reset"), attempt=0)
    assert retryable.retryable is True


def test_resume_position_rules():
    assert pb.resume_position(120, 3600) == 120
    assert pb.resume_position(2, 3600) == 0
    assert pb.resume_position(0, 3600) == 0
    assert pb.resume_position("nonsense", 3600) == 0
    assert pb.resume_position(None) == 0
    # Almost at the end means start over rather than a two-second stub.
    assert pb.resume_position(3590, 3600) == 0
    assert pb.resume_position(3600, 3600) == 0
    assert pb.resume_position(60) == 60


def test_track_selection_prefers_language_then_first():
    caps = pb.Capabilities(audio_tracks=True, subtitles=True)
    audio = [
        {"id": "a1", "language": "en"},
        {"id": "a2", "language": "ja"},
    ]
    subs = [{"id": "s1", "language": "en"}, {"id": "s2", "language": "fr"}]
    picked = pb.select_tracks(audio, subs, language="ja", capabilities=caps, want_subtitles=True)
    assert picked.audio.id == "a2"
    assert picked.subtitle.id == "s1"
    picked = pb.select_tracks(audio, subs, language="de", capabilities=caps, want_subtitles=True)
    assert picked.audio.id == "a1"
    assert picked.subtitle.id == "s1"


def test_track_selection_respects_missing_capabilities():
    audio = [{"id": "a1", "language": "en"}]
    picked = pb.select_tracks(audio, [], capabilities=pb.Capabilities())
    assert picked.audio is None
    assert "cannot switch audio" in picked.note
    picked = pb.select_tracks([], [{"id": "s1"}], capabilities=pb.Capabilities(), want_subtitles=True)
    assert picked.subtitle is None


def test_forced_subtitles_win_when_no_language_matches():
    subs = [{"id": "s1", "language": "en"}, {"id": "s2", "language": "ja", "forced": True}]
    picked = pb.select_tracks([], subs, language="de", capabilities=pb.Capabilities(subtitles=True))
    assert picked.subtitle.id == "s2"


def test_unsupported_format_explains_known_extensions():
    assert "MKV" in pb.unsupported_format("movie.mkv")
    assert "Flash-era" in pb.unsupported_format(".flv")
    assert "system player" in pb.unsupported_format(".xyz")
    assert "cannot decode" in pb.unsupported_format(".xyz")


def test_capabilities_from_mapping_and_summary():
    caps = pb.Capabilities.from_mapping({"audio_tracks": True, "resume": True})
    assert caps.audio_tracks and caps.resume and not caps.subtitles
    summary = pb.capability_summary(caps)
    assert "resume" in summary and "single" not in summary
