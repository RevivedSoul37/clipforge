"""Phase 1.7 Ã¢â‚¬â€ Transcript cleanup (LLM).

Fixes the small mishearings that Whisper inevitably produces ("jym" for "gym",
"jim look at the time" instead of "gym, look at the time", etc.) WITHOUT
re-listening to the audio and WITHOUT hallucinating new content.

How it works:
1. Read the transcript and flag every segment that contains at least one word
   whose probability is below `transcription.low_confidence_threshold` (0.55).
2. Send each flagged segment plus its immediate neighbours (read-only context)
   to the LLM with a strict contract: return corrected text ONLY for the
   flagged ids.
3. Stitch corrections back onto the original word timestamps and write
   data/transcripts/<video_id>_transcript_clean.json. The raw transcript is
   never overwritten, so there is always a fallback.

Downstream stages (highlight selection, captions) prefer the cleaned file via
`best_transcript_path()`.
"""
import json
from pathlib import Path

from src.config import config
from src.llm_client import call_ollama
from src.select_prompts import CLEAN_SYSTEM_PROMPT


def _flagged_indices(segments, threshold):
    idxs = []
    for i, seg in enumerate(segments):
        words = seg.get("words") or []
        if any((w.get("probability") or 1.0) < threshold for w in words):
            idxs.append(i)
    return idxs


def _window(segments, flagged_ids, context, assigned):
    """Segments shown to the LLM: flagged ids +/- context, minus already-fixed ones."""
    lo, hi = min(flagged_ids), max(flagged_ids)
    lo = max(0, lo - context)
    hi = min(len(segments) - 1, hi + context)
    return [i for i in range(lo, hi + 1) if i not in assigned]


def _format_batch(segments, ids, flagged):
    lines = []
    for i in ids:
        marker = "*" if i in flagged else " "
        lines.append(f"{marker} [{i}] {segments[i].get('text', '').strip()}")
    return "\n".join(lines)


def _make_batches(segments, flagged, context=4, max_chars=5000):
    """Greedy batching: walk the flagged ids in order and keep adding the next
    one while the whole window still fits in `max_chars`. A single oversized
    flagged line still gets its own batch (it just can't be shrunk)."""
    batches = []
    assigned = set()
    current = []
    for fid in flagged:
        trial = current + [fid]
        ids = _window(segments, trial, context, assigned)
        size = sum(len(segments[i].get("text", "")) for i in ids)
        if size <= max_chars or not current:
            current = trial
            continue
        batches.append((current, _window(segments, current, context, assigned)))
        current = [fid]
    if current:
        batches.append((current, _window(segments, current, context, assigned)))
    return batches


def _extract_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`").lstrip("json").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _apply_correction(segment, fixed_text):
    """Replace segment text; map corrected tokens onto word timestamps when
    the token count matches (keeps caption alignment intact)."""
    fixed_text = fixed_text.strip()
    if not fixed_text:
        return False
    words = segment.get("words") or []
    tokens = fixed_text.split()
    if words and len(tokens) == len(words):
        for w, tok in zip(words, tokens):
            w["word"] = tok
    segment["text"] = fixed_text
    return True


def clean_transcript(video_path, transcript_path=None, output_dir=None):
    """Return the path of the cleaned transcript (or the original one when
    there is nothing to fix or the LLM is unavailable)."""
    video_path = Path(video_path)
    if transcript_path is None:
        transcript_path = config.transcripts_dir / f"{video_path.stem}_transcript.json"
    else:
        transcript_path = Path(transcript_path)
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    segments = transcript.get("segments", [])
    threshold = config.whisper_low_confidence

    flagged = _flagged_indices(segments, threshold)
    if not flagged:
        print("[clean] no low-confidence segments; skipping LLM cleanup")
        return transcript_path

    print(f"[clean] {len(flagged)} flagged segment(s) "
          f"(word probability < {threshold}); calling {config.llm_model}")

    flagged_set = set(flagged)
    assigned = set()
    fixed_count = 0
    batches = _make_batches(segments, flagged)
    total_batches = len(batches)
    for n, (batch_flagged, ids) in enumerate(batches, start=1):
        # flagged ids fixed in earlier batches now show as plain context
        visible_flagged = flagged_set - assigned
        block = _format_batch(segments, ids, visible_flagged)
        user_prompt = (
            "Transcript lines follow. Lines marked '*' contain low-confidence "
            "words and may have mishearings; unmarked lines are context only. "
            "Return corrected text for the marked lines ONLY, as JSON:\n"
            '{"segments": [{"id": <int>, "text": "<corrected line>"}]}\n\n'
            f"{block}"
        )
        messages = [
            {"role": "system", "content": CLEAN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        try:
            content = call_ollama(messages, temperature=0.0)
        except Exception as exc:  # noqa: BLE001
            print(f"[clean]  batch {n}/{total_batches} failed ({exc}); keeping raw text")
            continue
        parsed = _extract_json(content)
        if not parsed:
            print(f"[clean]  batch {n}/{total_batches}: unparsable output; keeping raw text")
            continue
        n_fixed = 0
        for item in parsed.get("segments", []):
            try:
                idx = int(item.get("id"))
                text = str(item.get("text", ""))
            except (TypeError, ValueError):
                continue
            if idx in batch_flagged and 0 <= idx < len(segments) and text.strip():
                if _apply_correction(segments[idx], text):
                    n_fixed += 1
        print(f"[clean]  batch {n}/{total_batches}: {n_fixed} correction(s) applied")
        fixed_count += n_fixed
        assigned |= set(batch_flagged)

    if fixed_count == 0:
        print("[clean] no usable corrections; keeping raw transcript")
        return transcript_path

    transcript["cleaned"] = True
    transcript["cleaned_segments"] = fixed_count
    out_dir = Path(output_dir) if output_dir else transcript_path.parent
    out_path = out_dir / f"{video_path.stem}_transcript_clean.json"
    out_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[clean] corrected {fixed_count} segment(s) -> {out_path}")
    return out_path


def best_transcript_path(video_path, transcripts_dir=None):
    """Return the cleaned transcript path when it exists, else the raw path."""
    video_path = Path(video_path)
    tdir = Path(transcripts_dir) if transcripts_dir else config.transcripts_dir
    clean = tdir / f"{video_path.stem}_transcript_clean.json"
    return clean if clean.exists() else tdir / f"{video_path.stem}_transcript.json"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fix low-confidence words via LLM.")
    parser.add_argument("video")
    parser.add_argument("--transcript")
    args = parser.parse_args()
    clean_transcript(args.video, transcript_path=args.transcript)