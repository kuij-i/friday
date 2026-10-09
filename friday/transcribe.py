"""Optional local speech-to-text for voice notes.

Uses faster-whisper so transcription stays on your machine (no audio leaves
it). It's an optional dependency: if it isn't installed, voice handling
degrades to a friendly message instead of crashing.

    pip install -r requirements-voice.txt
"""
from __future__ import annotations

from pathlib import Path


class TranscriberUnavailable(RuntimeError):
    pass


class Transcriber:
    def __init__(self, model_size: str = "base") -> None:
        self.model_size = model_size
        self._model = None  # lazy — loading a whisper model is expensive

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:  # pragma: no cover
                raise TranscriberUnavailable(
                    "faster-whisper is not installed. Run: pip install -r requirements-voice.txt"
                ) from e
            # int8 on CPU is the portable default; fine for short voice notes.
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, audio_path: Path | str) -> str:
        model = self._ensure_model()
        segments, _info = model.transcribe(str(audio_path), vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()
