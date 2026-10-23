"""Speech-to-text through the gateway's TRANSCRIBE purpose, or the built-in Whisper (10.4).

Which engine runs is decided per job, never as a fallback after a failure (P5):

1. TRANSCRIBE has a usable model (routed, allowed by the cloud ceiling, its provider cleared for
   private data): the recording goes to that API, and only there.
2. Otherwise the media worker's built-in faster-whisper, if the operator allows it
   (``llm.builtin_transcription``) and its model is in the ``models`` volume. It never leaves
   the server.
3. Otherwise the derivative waits for a model (WAITING_FOR_MODEL).

For the API the audio is decoded once (PyAV, streaming) into 16 kHz mono WAV segments of at most
``llm.transcribe_segment_seconds`` in the job's tmpfs directory; a tail shorter than
``MIN_TAIL_SECONDS`` joins the previous segment (providers refuse very short clips). Segments
are sent in order and their timestamps shifted by each segment's offset. A rejected request
(4xx) fails the derivative; an unavailable provider (5xx, 429, timeout) is retried by the task
runner from the first segment.
"""

from __future__ import annotations

import asyncio
import shutil
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.llm.gateway import (
    LLMOutputInvalid,
    LLMPurpose,
    ModelNotConfigured,
    ModelRejected,
    ModelUnavailable,
)
from spoldzielnia_ai.llm.transcription import Segment
from spoldzielnia_ai.workers.engines.base import PermanentError, TransientError, WaitingForModel
from spoldzielnia_ai.workers.engines.whisper import SAMPLE_RATE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.llm.transcription import Transcription
    from spoldzielnia_ai.workers.engines.base import Engine

log = get_logger(__name__)

MIN_TAIL_SECONDS = 2


class Transcriber(Protocol):
    """What the engine needs from ``LiteLLMGateway``."""

    async def refresh(self) -> Any: ...

    def configured(self, purpose: LLMPurpose, private_data: bool = False) -> bool: ...

    async def transcribe(
        self, audio: bytes, *, filename: str = ..., language: str | None = None
    ) -> Transcription: ...


@dataclass(frozen=True, slots=True)
class AudioChunk:
    path: Path
    offset: float  # seconds from the start of the recording
    duration: float


class _ChunkWriter:
    """WAV files of at most ``limit`` samples; a short tail goes to the previous one."""

    def __init__(self, directory: Path, limit: int, min_tail: int) -> None:
        self._directory = directory
        self._limit = limit
        self._min_tail = min_tail
        self._writer: wave.Wave_write | None = None
        self._written = 0
        self._starts: list[int] = []
        self._sizes: list[int] = []
        self._pending: list[bytes] = []
        self._pending_samples = 0

    def _open(self) -> None:
        if self._writer is not None:
            self._writer.close()
        index = len(self._starts)
        writer = wave.open(str(self._directory / f"segment-{index:04d}.wav"), "wb")  # noqa: SIM115 - closed in _open/close
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        self._starts.append(sum(self._sizes))
        self._sizes.append(0)
        self._writer = writer
        self._written = 0

    def _write(self, data: bytes) -> None:
        assert self._writer is not None  # noqa: S101
        self._writer.writeframes(data)
        samples = len(data) // 2
        self._written += samples
        self._sizes[-1] += samples

    def add(self, data: bytes) -> None:
        while data:
            if self._writer is None:
                self._open()
            if self._written < self._limit:
                take = min(len(data), (self._limit - self._written) * 2)
                self._write(data[:take])
                data = data[take:]
                continue
            # The current segment is full: hold the start of the next one until it is long
            # enough to stand alone.
            take = min(len(data), (self._min_tail - self._pending_samples) * 2)
            self._pending.append(data[:take])
            self._pending_samples += take // 2
            data = data[take:]
            if self._pending_samples >= self._min_tail:
                self._open()
                self._flush_pending()

    def _flush_pending(self) -> None:
        for part in self._pending:
            self._write(part)
        self._pending, self._pending_samples = [], 0

    def close(self) -> list[AudioChunk]:
        if self._writer is None:
            return []
        self._flush_pending()  # a short tail: into the last segment
        self._writer.close()
        return [
            AudioChunk(
                self._directory / f"segment-{i:04d}.wav",
                start / SAMPLE_RATE,
                size / SAMPLE_RATE,
            )
            for i, (start, size) in enumerate(zip(self._starts, self._sizes, strict=True))
        ]

    def abort(self) -> None:
        if self._writer is not None:
            self._writer.close()


def split_audio(path: Path, directory: Path, seconds: int) -> list[AudioChunk]:
    """Any container PyAV reads → 16 kHz mono 16-bit WAV segments in ``directory``."""
    import av  # pyright: ignore[reportMissingTypeStubs]
    from av.error import FFmpegError

    directory.mkdir(parents=True, exist_ok=True)
    writer = _ChunkWriter(directory, seconds * SAMPLE_RATE, MIN_TAIL_SECONDS * SAMPLE_RATE)
    try:
        with av.open(str(path), mode="r") as container:  # pyright: ignore[reportUnknownMemberType]
            if not container.streams.audio:
                raise PermanentError("no_audio_stream")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

            def frames() -> Iterator[Any]:
                for frame in container.decode(container.streams.audio[0]):
                    yield from resampler.resample(frame)
                yield from resampler.resample(None)

            for frame in frames():
                writer.add(frame.to_ndarray().tobytes())
    except FFmpegError as exc:
        writer.abort()
        raise PermanentError("audio_undecodable") from exc
    except BaseException:
        writer.abort()
        raise
    chunks = writer.close()
    if not chunks:
        raise PermanentError("no_speech_detected")
    return chunks


def stitch(parts: list[tuple[AudioChunk, Transcription]]) -> list[Segment]:
    """Timestamps relative to the recording. A reply without segments counts as one segment
    spanning its chunk."""
    out: list[Segment] = []
    for chunk, result in parts:
        if result.segments:
            out.extend(
                Segment(chunk.offset + s.start, chunk.offset + s.end, s.text)
                for s in result.segments
            )
        elif result.text:
            out.append(Segment(chunk.offset, chunk.offset + chunk.duration, result.text))
    return out


class GatewayTranscriptionEngine:
    """Runs in a worker thread: each call gets its own event loop for the async gateway."""

    def __init__(
        self,
        gateway: Callable[[], Transcriber],
        settings: LlmSettings,
        builtin: Callable[[], Engine | None] = lambda: None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._builtin = builtin

    def extract(self, path: Path, mime_type: str) -> str:
        gateway = self._gateway()
        asyncio.run(gateway.refresh())
        if not gateway.configured(LLMPurpose.TRANSCRIBE):
            builtin = self._builtin()
            if builtin is None:
                raise WaitingForModel(LLMPurpose.TRANSCRIBE.value)
            return builtin.extract(path, mime_type)
        directory = path.parent / "segments"
        try:
            chunks = split_audio(path, directory, self._settings.transcribe_segment_seconds)
            segments = asyncio.run(self.transcribe(gateway, chunks))
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        text = " ".join(s.text for s in segments).strip()
        if not text:
            raise PermanentError("no_speech_detected")
        return text

    async def transcribe(self, gateway: Transcriber, chunks: list[AudioChunk]) -> list[Segment]:
        parts: list[tuple[AudioChunk, Transcription]] = []
        for index, chunk in enumerate(chunks):
            audio = await asyncio.to_thread(chunk.path.read_bytes)
            try:
                result = await gateway.transcribe(
                    audio, filename=chunk.path.name, language=self._settings.transcribe_language
                )
            except ModelNotConfigured as exc:  # un-cleared or un-routed meanwhile
                raise WaitingForModel(LLMPurpose.TRANSCRIBE.value) from exc
            except ModelUnavailable as exc:
                raise TransientError(str(exc)) from exc
            except ModelRejected as exc:
                raise PermanentError("transcription_rejected") from exc
            except LLMOutputInvalid as exc:
                raise PermanentError("transcription_invalid") from exc
            log.info("worker.transcribed_segment", segment=index + 1, of=len(chunks))
            parts.append((chunk, result))
        return stitch(parts)
