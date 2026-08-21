"""Clip-boundary silence trim.

Speech spans come from Silero VAD (when the wav is still around) or from
Whisper word timestamps. `trim_silence` snaps a clip's start/end inward to
real speech so cuts don't open or close on dead air.
"""
from pathlib import Path

from src.config import config

SAMPLE_RATE = 16000


def _merge_spans(spans, gap=0.4):
    if not spans:
        return []
    ordered = sorted((float(s), float(e)) for s, e in spans if e > s)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start": round(s, 3), "end": round(e, 3)} for s, e in merged]


def spans_from_words(segments, merge_gap=0.4):
    """Speech windows from Whisper word (or segment) timestamps."""
    raw = []
    for seg in segments or []:
        words = seg.get("words") or []
        if words:
            for w in words:
                try:
                    raw.append((float(w["start"]), float(w["end"])))
                except (TypeError, ValueError, KeyError):
                    continue
        else:
            try:
                raw.append((float(seg["start"]), float(seg["end"])))
            except (TypeError, ValueError, KeyError):
                continue
    return _merge_spans(raw, gap=merge_gap)


def detect_speech(wav_path, threshold=None):
    """Silero VAD on a 16 kHz mono wav. Returns spans or None if unavailable."""
    threshold = config.vad_threshold if threshold is None else threshold
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad, read_audio
    except ImportError:
        print("[vad] silero-vad not installed; using transcript word timings")
        return None
    wav_path = Path(wav_path)
    if not wav_path.exists():
        return None
    try:
        model = load_silero_vad()
        wav = read_audio(str(wav_path), sampling_rate=SAMPLE_RATE)
        ts = get_speech_timestamps(
            wav, model,
            threshold=float(threshold),
            min_speech_duration_ms=250,
            min_silence_duration_ms=350,
            sampling_rate=SAMPLE_RATE,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[vad] silero failed ({exc}); using transcript word timings")
        return None
    spans = []
    for t in ts:
        start = t["start"] / SAMPLE_RATE if isinstance(t, dict) else t.start / SAMPLE_RATE
        end = t["end"] / SAMPLE_RATE if isinstance(t, dict) else t.end / SAMPLE_RATE
        spans.append((start, end))
    merged = _merge_spans(spans, gap=0.35)
    print(f"[vad] silero: {len(merged)} speech regions in {wav_path.name}")
    return merged


def overlapping(spans, start, end):
    out = []
    for s in spans or []:
        if s["end"] <= start or s["start"] >= end:
            continue
        out.append(s)
    return out


def trim_silence(start, end, spans, pad_in=None, pad_out=None, min_len=None):
    """Snap [start, end] inward to speech. Never shrinks below min_len."""
    pad_in = config.vad_pad_in if pad_in is None else pad_in
    pad_out = config.vad_pad_out if pad_out is None else pad_out
    min_len = config.vad_min_len if min_len is None else min_len
    start, end = float(start), float(end)
    if end - start < min_len or not spans:
        return round(start, 3), round(end, 3)

    hits = overlapping(spans, start, end)
    if not hits:
        return round(start, 3), round(end, 3)

    speech_start = hits[0]["start"]
    speech_end = hits[-1]["end"]
    new_start = max(start, speech_start - pad_in)
    new_end = min(end, speech_end + pad_out)
    if new_end - new_start < min_len:
        return round(start, 3), round(end, 3)
    return round(new_start, 3), round(new_end, 3)


def trim_clip(clip, spans, **kwargs):
    start, end = trim_silence(clip["start"], clip["end"], spans, **kwargs)
    if start == clip["start"] and end == clip["end"]:
        return clip
    out = dict(clip)
    out["start"], out["end"] = start, end
    return out
