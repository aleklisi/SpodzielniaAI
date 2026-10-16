"""Speech-to-text with faster-whisper (CTranslate2). Typed adapter (conventions §2.2)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.workers.engines.base import PermanentError

if TYPE_CHECKING:
    from pathlib import Path

    from spoldzielnia_ai.core.settings import ModelSettings


SAMPLE_RATE = 16_000


def decode_audio(path: Path) -> Any:
    """Any container PyAV reads → 16 kHz mono float32 (what Whisper expects).

    Like ``faster_whisper.decode_audio``, which still passes ``metadata_errors=`` to
    ``av.open`` (removed in PyAV 15).
    """
    import av  # pyright: ignore[reportMissingTypeStubs]
    import numpy as np
    from av.error import FFmpegError

    chunks: list[Any] = []
    try:
        with av.open(str(path), mode="r") as container:  # pyright: ignore[reportUnknownMemberType]
            if not container.streams.audio:
                raise PermanentError("no_audio_stream")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(container.streams.audio[0]):
                chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
            chunks.extend(f.to_ndarray() for f in resampler.resample(None))
    except FFmpegError as exc:
        raise PermanentError("audio_undecodable") from exc
    if not chunks:
        raise PermanentError("no_speech_detected")
    return np.concatenate(chunks, axis=1).reshape(-1).astype(np.float32) / 32768.0


class WhisperEngine:
    """Loads the model once per worker process (from the read-only models volume)."""

    def __init__(self, settings: ModelSettings) -> None:
        self._settings = settings
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel  # pyright: ignore[reportMissingTypeStubs]

                try:
                    self._model = WhisperModel(
                        self._settings.whisper_dir,
                        device=self._settings.whisper_device,
                        compute_type=self._settings.whisper_compute_type,
                        local_files_only=True,
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    raise PermanentError("transcription_model_unavailable") from exc
            return self._model

    def extract(self, path: Path, mime_type: str) -> str:
        model = self._load()
        audio = decode_audio(path)
        try:
            segments, _info = model.transcribe(
                audio,
                language=self._settings.whisper_language,
                beam_size=self._settings.whisper_beam_size,
                vad_filter=True,
            )
            text = " ".join(str(segment.text).strip() for segment in segments)
        except (RuntimeError, ValueError, OSError) as exc:
            raise PermanentError("audio_undecodable") from exc
        if not text.strip():
            raise PermanentError("no_speech_detected")
        return text.strip()
