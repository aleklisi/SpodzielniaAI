"""Chunk 10.4: speech-to-text over the OpenAI-style API (respx), segmenting and P1/P9."""

from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from prometheus_client import REGISTRY

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import LlmCapability, LlmProviderKind
from spoldzielnia_ai.llm.gateway import (
    LOCAL_CHAT,
    LOCAL_TRANSCRIBE,
    Deployment,
    LiteLLMGateway,
    LLMOutputInvalid,
    LLMPurpose,
    ModelNotConfigured,
    ResolvedLlmConfig,
    StaticConfig,
    env_config,
)
from spoldzielnia_ai.llm.probes import (
    PROBE_SPEECH,
    deployment_for,
    probe_model,
    probe_speech,
    word_error_rate,
)
from spoldzielnia_ai.llm.transcription import Segment, Transcription
from spoldzielnia_ai.workers.engines.base import PermanentError, TransientError, WaitingForModel
from spoldzielnia_ai.workers.engines.transcription import (
    AudioChunk,
    GatewayTranscriptionEngine,
    split_audio,
    stitch,
)

LOCAL = "http://llm:11434/v1"
URL = f"{LOCAL}/audio/transcriptions"
SETTINGS = LlmSettings(transcribe_model="whisper-large-v3")
RATE = 8000  # the source; the engine resamples to 16 kHz


def _wav(path: Path, seconds: float, rate: int = RATE) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return path


def _duration(body: bytes) -> float:
    """Seconds of the WAV part of a multipart request."""
    start = body.index(b"RIFF")
    with wave.open(io.BytesIO(body[start:])) as w:
        return w.getnframes() / w.getframerate()


def _gateway(settings: LlmSettings = SETTINGS, **deployment: Any) -> LiteLLMGateway:
    if not deployment:
        return LiteLLMGateway(settings)
    base_url = deployment.pop("base_url", LOCAL)
    key = deployment.pop("api_key", None)
    target = Deployment("x/stt", "stt", base_url, key, **deployment)
    config = ResolvedLlmConfig(1, {target.name: target}, {LLMPurpose.TRANSCRIBE: target.name})
    return LiteLLMGateway(settings, source=StaticConfig(config))


def _engine(gateway: LiteLLMGateway, builtin: Any = None) -> GatewayTranscriptionEngine:
    return GatewayTranscriptionEngine(lambda: gateway, SETTINGS, lambda: builtin)


def _refused() -> float:
    return REGISTRY.get_sample_value("llm_route_refused_total", {"purpose": "TRANSCRIBE"}) or 0.0


def test_a_25_minute_recording_is_three_requests_with_stitched_offsets(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    replies = iter(
        [
            httpx.Response(
                200,
                json={
                    "text": "Początek.",
                    "segments": [{"start": 1.0, "end": 2.5, "text": "Początek."}],
                },
            ),
            httpx.Response(200, json={"text": "Środek."}),  # no segments: one per chunk
            httpx.Response(
                200,
                json={
                    "text": "Koniec.",
                    "segments": [{"start": 3.0, "end": 4.0, "text": "Koniec."}],
                },
            ),
        ]
    )
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(replies)

    route = respx_mock.post(URL).mock(side_effect=reply)
    source = _wav(tmp_path / "input.wav", 25 * 60)
    engine = _engine(_gateway())

    chunks = split_audio(source, tmp_path / "segments", SETTINGS.transcribe_segment_seconds)
    assert [(c.offset, c.duration) for c in chunks] == [(0, 600), (600, 600), (1200, 300)]
    text = engine.extract(source, "audio/wav")

    assert text == "Początek. Środek. Koniec."
    assert route.call_count == 3
    assert [_duration(r.content) for r in requests] == [600, 600, 300]
    assert all(b'name="language"\r\n\r\npl' in r.content for r in requests)
    assert all(b'name="model"\r\n\r\nwhisper-large-v3' in r.content for r in requests)
    assert not (tmp_path / "segments").exists() or not any((tmp_path / "segments").iterdir())


def test_offsets_are_shifted_per_chunk() -> None:
    first = AudioChunk(Path("a"), 0, 600)
    second = AudioChunk(Path("b"), 600, 300)
    segments = stitch(
        [
            (first, Transcription("a", (Segment(1, 2, "a"),))),
            (second, Transcription("b c", (Segment(0.5, 1, "b"), Segment(10, 12, "c")))),
        ]
    )
    assert segments == [Segment(1, 2, "a"), Segment(600.5, 601, "b"), Segment(610, 612, "c")]
    assert stitch([(second, Transcription("tylko tekst"))]) == [Segment(600, 900, "tylko tekst")]


def test_a_short_tail_joins_the_previous_segment(tmp_path: Path) -> None:
    chunks = split_audio(_wav(tmp_path / "in.wav", 21), tmp_path / "s", 10)
    assert [(c.offset, c.duration) for c in chunks] == [(0, 10), (10, 11)]
    chunks = split_audio(_wav(tmp_path / "in2.wav", 23), tmp_path / "t", 10)
    assert [(c.offset, c.duration) for c in chunks] == [(0, 10), (10, 10), (20, 3)]


def test_not_audio_is_a_permanent_error(tmp_path: Path) -> None:
    junk = tmp_path / "x.wav"
    junk.write_bytes(b"not audio at all" * 10)
    with pytest.raises(PermanentError):
        split_audio(junk, tmp_path / "s", 600)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, PermanentError),
        (401, PermanentError),
        (413, PermanentError),
        (302, PermanentError),
        (429, TransientError),
        (500, TransientError),
        (503, TransientError),
    ],
)
@respx.mock
def test_provider_4xx_is_permanent_5xx_and_429_retry(
    tmp_path: Path, status: int, error: type[Exception]
) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status, json={"error": "x"}))
    with pytest.raises(error) as caught:
        _engine(_gateway()).extract(_wav(tmp_path / "a.wav", 3), "audio/wav")
    if isinstance(caught.value, PermanentError):
        assert caught.value.code == "transcription_rejected"


@respx.mock
def test_connection_errors_retry_and_a_reply_without_text_fails(tmp_path: Path) -> None:
    route = respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TransientError):
        _engine(_gateway()).extract(_wav(tmp_path / "a.wav", 3), "audio/wav")
    route.mock(return_value=httpx.Response(200, json={"segments": []}))
    with pytest.raises(PermanentError, match="transcription_invalid"):
        _engine(_gateway()).extract(_wav(tmp_path / "b.wav", 3), "audio/wav")
    route.mock(return_value=httpx.Response(200, json={"text": "  "}))
    with pytest.raises(PermanentError, match="no_speech_detected"):
        _engine(_gateway()).extract(_wav(tmp_path / "c.wav", 3), "audio/wav")


@respx.mock
async def test_plain_text_replies_and_the_api_key() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200, text=" Dzień dobry. ", headers={"content-type": "text/plain"}
        )
    )
    gateway = _gateway(SETTINGS, local=True, private_data=True, api_key="sekret")
    result = await gateway.transcribe(b"RIFF....")
    assert result == Transcription("Dzień dobry.")
    assert route.calls.last.request.headers["authorization"] == "Bearer sekret"


@respx.mock
async def test_an_uncleared_provider_never_receives_a_recording(tmp_path: Path) -> None:
    route = respx.post(url__regex=r".*").mock(return_value=httpx.Response(200, json={"text": "x"}))
    uncleared = _gateway(SETTINGS, local=True, private_data=False)
    before = _refused()

    with pytest.raises(ModelNotConfigured):
        await uncleared.transcribe(b"RIFF....")
    # Even a caller passing private_data=False: TRANSCRIBE is a private purpose (P1).
    with pytest.raises(ModelNotConfigured):
        await uncleared.transcribe(b"RIFF....", private_data=False)
    # The engine does not even try: the derivative waits for a cleared model.
    with pytest.raises(WaitingForModel, match="TRANSCRIBE"):
        await asyncio.to_thread(_engine(uncleared).extract, _wav(tmp_path / "a.wav", 3), "")

    assert _refused() == before + 2
    assert route.call_count == 0


@respx.mock
async def test_cloud_needs_allow_cloud_and_goes_through_the_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = {"base_url": "https://api.mistral.ai/v1", "local": False, "private_data": True}
    with pytest.raises(ModelNotConfigured):
        await _gateway(SETTINGS, **dict(cloud)).transcribe(b"RIFF")

    seen: list[dict[str, Any]] = []
    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        seen.append(kwargs)
        return real(**{k: v for k, v in kwargs.items() if k != "proxy"})

    monkeypatch.setattr("spoldzielnia_ai.llm.transcription.httpx.AsyncClient", client)
    respx.post("https://api.mistral.ai/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={"text": "ok"})
    )
    allowed = SETTINGS.model_copy(
        update={"allow_cloud": True, "egress_proxy_url": "http://llm-egress:3128"}
    )
    assert (await _gateway(allowed, **dict(cloud)).transcribe(b"RIFF")).text == "ok"
    assert seen[0]["proxy"] == "http://llm-egress:3128"
    assert seen[0]["follow_redirects"] is False
    assert seen[0]["trust_env"] is False


def test_without_a_model_the_builtin_whisper_runs_or_the_job_waits(tmp_path: Path) -> None:
    class Builtin:
        def extract(self, path: Path, mime_type: str) -> str:
            return "z wbudowanego modelu"

    nothing = LiteLLMGateway(LlmSettings())  # no TRANSCRIBE model
    audio = _wav(tmp_path / "a.wav", 1)
    assert _engine(nothing, Builtin()).extract(audio, "audio/wav") == "z wbudowanego modelu"
    with pytest.raises(WaitingForModel):
        _engine(nothing).extract(audio, "audio/wav")


@respx.mock
def test_a_routed_model_is_used_instead_of_the_builtin_even_when_it_fails(
    tmp_path: Path,
) -> None:
    class Builtin:
        def extract(self, path: Path, mime_type: str) -> str:
            raise AssertionError("no fallback to the built-in model (P5)")

    respx.post(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(TransientError):
        _engine(_gateway(), Builtin()).extract(_wav(tmp_path / "a.wav", 3), "audio/wav")


def test_env_routes_transcribe_only_to_its_own_model() -> None:
    config = env_config(LlmSettings(chat_model="bielik"))
    assert LLMPurpose.TRANSCRIBE not in config.routes  # never to the chat model
    config = env_config(LlmSettings(chat_model="bielik", transcribe_model="whisper"))
    assert config.routes[LLMPurpose.TRANSCRIBE] == LOCAL_TRANSCRIBE
    assert config.routes[LLMPurpose.PRIVATE_NOTE] == LOCAL_CHAT


def test_word_error_rate() -> None:
    assert word_error_rate(PROBE_SPEECH, "pszczoły zbierają nektar z kwiatów rzepaku") == 0
    assert word_error_rate(PROBE_SPEECH, "Pszczoły zbierały nektar z kwiatów rzepaku.") == 1 / 6
    assert word_error_rate(PROBE_SPEECH, "") == 1


@pytest.mark.parametrize(
    ("reply", "ok"),
    [("Pszczoły zbierają nektar z kwiatów rzepaku.", True), ("Dzień dobry wszystkim.", False)],
)
@respx.mock
async def test_the_probe_reads_back_a_polish_clip(reply: str, ok: bool) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"text": reply}))
    deployment = deployment_for("local/stt", "stt", LOCAL, None, LlmProviderKind.LOCAL)

    report = await probe_model(LlmCapability.TRANSCRIBE, deployment, LlmSettings())

    assert report["ok"] is ok
    assert route.calls.last.request.content.count(probe_speech()[:44]) == 1
    assert reply not in str(report)  # numbers only, never the model's output


@respx.mock
async def test_a_bad_reply_is_invalid_output() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(LLMOutputInvalid):
        await _gateway().transcribe(b"RIFF")
