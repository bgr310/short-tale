"""Local speech synthesis.

Kokoro is the default. It is an 82M-parameter model that runs on the CPU in
ONNX, which is the point: it leaves all 12GB of VRAM to the language model and
Whisper, and it removes an entire class of CUDA/onnxruntime version conflicts.
On an older CPU it still runs comfortably faster than realtime.

Piper is the fallback — smaller, plainer, and almost impossible to break.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from ..config import get_settings
from .normalize import normalize

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000


class TTSError(RuntimeError):
    pass


# ---------------------------------------------------------------------------


class KokoroEngine:
    name = "kokoro"

    def __init__(self) -> None:
        s = get_settings()
        base = s.models_dir / "kokoro"
        self.model_path = base / "kokoro-v1.0.onnx"
        self.voices_path = base / "voices-v1.0.bin"
        if not self.model_path.exists() or not self.voices_path.exists():
            raise TTSError(
                f"Kokoro model files not found in {base}. Run ./scripts/bootstrap_models.sh"
            )
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise TTSError(f"kokoro-onnx is not installed: {exc}") from exc
        self._k = Kokoro(str(self.model_path), str(self.voices_path))
        log.info("kokoro loaded from %s", base)

    def synth(self, text: str, out: Path, voice: str, rate: float) -> float:
        samples, sr = self._k.create(text, voice=voice, speed=rate, lang="en-us")
        _write_wav(out, np.asarray(samples, dtype=np.float32), sr)
        return len(samples) / float(sr)


class PiperEngine:
    name = "piper"

    def __init__(self) -> None:
        s = get_settings()
        self.bin = shutil.which("piper")
        base = s.models_dir / "piper"
        models = sorted(base.glob("*.onnx")) if base.is_dir() else []
        if not self.bin or not models:
            raise TTSError(
                "piper binary or voice model missing. Run ./scripts/bootstrap_models.sh "
                "with PIPER=1, or set TTS_ENGINE=kokoro."
            )
        self.voice_model = models[0]
        log.info("piper loaded with voice %s", self.voice_model.name)

    def synth(self, text: str, out: Path, voice: str, rate: float) -> float:
        # Piper expresses rate as length_scale, which is inverted.
        length_scale = 1.0 / max(rate, 0.1)
        cmd = [
            self.bin, "--model", str(self.voice_model),
            "--output_file", str(out), "--length_scale", f"{length_scale:.3f}",
        ]
        subprocess.run(cmd, input=text.encode(), check=True, capture_output=True, timeout=300)
        return wav_duration(out)


def _write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0:
        # Normalise to -3 dBFS so every clip sits at the same level.
        samples = samples / peak * 0.708
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


_ENGINE = None


def get_engine(preference: str | None = None):
    """Resolve and cache the speech engine. `auto` tries Kokoro then Piper."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    pref = (preference or get_settings().tts_engine or "auto").lower()
    order = {"kokoro": [KokoroEngine], "piper": [PiperEngine]}.get(
        pref, [KokoroEngine, PiperEngine]
    )

    errors = []
    for cls in order:
        try:
            _ENGINE = cls()
            return _ENGINE
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{cls.name}: {exc}")
            log.warning("speech engine %s unavailable — %s", cls.name, exc)

    raise TTSError("no speech engine available.\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------------------


def synth_lines(
    lines: list[tuple[str, str]],
    out_dir: Path,
    *,
    voice: str = "af_heart",
    rate: float = 1.0,
    gap: float = 0.22,
    engine: str | None = None,
) -> tuple[Path, list[dict]]:
    """Synthesise each line, concatenate, and report exact beat boundaries.

    Returns (combined_wav, segments) where each segment carries the line's
    text, its visual cue, and its start/end time in the combined audio.
    """
    eng = get_engine(engine)
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[np.ndarray] = []
    segments: list[dict] = []
    cursor = 0.0
    sr = SAMPLE_RATE

    for i, (text, visual) in enumerate(lines):
        spoken = normalize(text)
        if not spoken:
            continue
        part = out_dir / f"line_{i:02d}.wav"
        try:
            eng.synth(spoken, part, voice, rate)
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"speech synthesis failed on line {i}: {exc}") from exc

        data, sr = _read_wav(part)
        chunks.append(data)
        dur = len(data) / float(sr)
        segments.append(
            {
                "index": i,
                "text": text,
                "spoken": spoken,
                "visual": visual,
                "start": round(cursor, 3),
                "end": round(cursor + dur, 3),
                "wav": str(part),
            }
        )
        cursor += dur
        if i < len(lines) - 1:
            chunks.append(np.zeros(int(sr * gap), dtype=np.float32))
            cursor += gap

    if not chunks:
        raise TTSError("nothing to say — every line was empty after normalisation")

    combined = out_dir / "voice.wav"
    _write_wav(combined, np.concatenate(chunks), sr)
    log.info(
        "synthesised %d lines -> %.1fs with %s", len(segments), cursor, eng.name
    )
    return combined, segments


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        ch = w.getnchannels()
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr
