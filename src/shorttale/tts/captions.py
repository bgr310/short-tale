"""Word-level caption timing.

We already know exactly what was said — we wrote it. So rather than trusting
Whisper's transcription, we use Whisper only for *timing* and then align those
timings back onto our own script text. Captions can therefore never display a
transcription error, which is the usual failure mode of this stage.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)

_MODEL = None
_MODEL_KEY: tuple | None = None
_PUNCT = re.compile(r"[^\w']+")


def _norm(w: str) -> str:
    return _PUNCT.sub("", w.lower())


def _pick_device(preference: str) -> tuple[str, str]:
    """Return (device, compute_type). Falls back to CPU if CUDA isn't usable."""
    pref = (preference or "auto").lower()
    if pref == "cpu":
        return "cpu", "int8"
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception as exc:  # noqa: BLE001
        log.debug("CUDA probe failed: %s", exc)
    if pref == "cuda":
        log.warning("WHISPER_DEVICE=cuda but no CUDA device is visible — using CPU")
    return "cpu", "int8"


def get_model():
    """Load (and cache) the Whisper model.

    Loaded lazily and only after the language model has been evicted from
    VRAM, so the two never contend for the same 12GB.
    """
    global _MODEL, _MODEL_KEY
    s = get_settings()
    device, compute_type = _pick_device(s.whisper_device)
    key = (s.whisper_model, device, compute_type)
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL

    from faster_whisper import WhisperModel

    log.info("loading whisper %s on %s (%s)", s.whisper_model, device, compute_type)
    try:
        _MODEL = WhisperModel(
            s.whisper_model,
            device=device,
            compute_type=compute_type,
            download_root=str(s.models_dir / "whisper"),
        )
    except Exception as exc:  # noqa: BLE001
        if device == "cuda":
            log.warning("whisper failed to start on CUDA (%s) — retrying on CPU", exc)
            _MODEL = WhisperModel(
                s.whisper_model,
                device="cpu",
                compute_type="int8",
                download_root=str(s.models_dir / "whisper"),
            )
            key = (s.whisper_model, "cpu", "int8")
        else:
            raise
    _MODEL_KEY = key
    return _MODEL


def unload_model() -> None:
    """Release Whisper's VRAM once captions are timed."""
    global _MODEL, _MODEL_KEY
    _MODEL = None
    _MODEL_KEY = None
    try:
        import gc

        gc.collect()
    except Exception:  # noqa: BLE001
        pass


def transcribe_file(path: Path, word_timestamps: bool = True) -> list[dict]:
    """Raw Whisper output as a list of segment dicts."""
    model = get_model()
    segments, _info = model.transcribe(
        str(path),
        word_timestamps=word_timestamps,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        beam_size=1,          # timing does not benefit from a wide beam
        condition_on_previous_text=False,
    )
    out: list[dict] = []
    for seg in segments:
        row = {"start": seg.start, "end": seg.end, "text": seg.text.strip(), "words": []}
        for w in getattr(seg, "words", None) or []:
            row["words"].append(
                {"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
            )
        out.append(row)
    return out


def align_to_script(
    audio: Path, script_words: list[str], fallback_duration: float
) -> list[dict]:
    """Time our own words using Whisper's timings.

    Whisper's transcript and our script are matched with a sequence diff.
    Matched words take Whisper's timing directly; unmatched runs (a
    mis-hearing, a normalised number) are spread evenly across the gap between
    their neighbours, so the caption never drifts out of sync.
    """
    if not script_words:
        return []

    try:
        segs = transcribe_file(audio, word_timestamps=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("whisper failed (%s) — falling back to even word spacing", exc)
        return _even_spacing(script_words, fallback_duration)

    heard = [w for seg in segs for w in seg["words"]]
    if not heard:
        log.warning("whisper returned no words — falling back to even spacing")
        return _even_spacing(script_words, fallback_duration)

    a = [_norm(w) for w in script_words]
    b = [_norm(w["word"]) for w in heard]

    timed: list[dict | None] = [None] * len(script_words)
    for op, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "equal":
            continue
        for k in range(i2 - i1):
            h = heard[j1 + k]
            timed[i1 + k] = {
                "word": script_words[i1 + k],
                "start": h["start"],
                "end": h["end"],
            }

    matched = sum(1 for t in timed if t)
    log.info(
        "caption alignment: %d/%d words matched (%.0f%%)",
        matched, len(script_words), 100.0 * matched / len(script_words),
    )
    if matched < max(3, len(script_words) * 0.35):
        log.warning("alignment too weak to trust — using even spacing")
        return _even_spacing(script_words, fallback_duration)

    return _fill_gaps(timed, script_words, fallback_duration)


def _fill_gaps(
    timed: list[dict | None], words: list[str], total: float
) -> list[dict]:
    n = len(words)
    # Anchor both ends so interpolation always has bounds.
    first = next((i for i, t in enumerate(timed) if t), None)
    last = next((i for i in range(n - 1, -1, -1) if timed[i]), None)
    if first is None or last is None:
        return _even_spacing(words, total)

    out: list[dict] = [dict(t) if t else {"word": words[i]} for i, t in enumerate(timed)]

    # Leading unmatched run
    if first > 0:
        span = out[first]["start"]
        step = span / first if first else 0
        for i in range(first):
            out[i]["start"] = i * step
            out[i]["end"] = (i + 1) * step
    # Trailing unmatched run
    if last < n - 1:
        span = max(total - out[last]["end"], 0.4 * (n - 1 - last))
        step = span / (n - 1 - last)
        for k, i in enumerate(range(last + 1, n)):
            out[i]["start"] = out[last]["end"] + k * step
            out[i]["end"] = out[last]["end"] + (k + 1) * step

    # Interior gaps
    i = first
    while i <= last:
        if "start" in out[i]:
            i += 1
            continue
        j = i
        while j <= last and "start" not in out[j]:
            j += 1
        lo = out[i - 1]["end"]
        hi = out[j]["start"] if j <= last else total
        step = max((hi - lo) / (j - i), 0.05)
        for k in range(i, j):
            out[k]["start"] = lo + (k - i) * step
            out[k]["end"] = lo + (k - i + 1) * step
        i = j

    # Guarantee monotonicity and a minimum on-screen time.
    prev_end = 0.0
    for w in out:
        w["start"] = max(float(w.get("start", prev_end)), prev_end)
        w["end"] = max(float(w.get("end", w["start"] + 0.12)), w["start"] + 0.12)
        prev_end = w["end"]
    return out


def _even_spacing(words: list[str], total: float) -> list[dict]:
    """Last-resort timing: divide the duration by the word count.

    Weighted by word length so long words hold the screen longer — it reads
    far better than a flat division.
    """
    weights = [max(len(w), 2) for w in words]
    span = total / max(sum(weights), 1)
    out, cursor = [], 0.0
    for w, wt in zip(words, weights):
        dur = max(wt * span, 0.12)
        out.append({"word": w, "start": round(cursor, 3), "end": round(cursor + dur, 3)})
        cursor += dur
    return out
