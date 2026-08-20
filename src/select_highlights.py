"""Phase 2 Ã¢â‚¬â€ Highlight selection (LLM).

Sends the (cleaned) transcript + per-video context to a local LLM (Ollama) and
parses a structured JSON list of candidate clips. Reliability is built on
three mechanics instead of hoping the model guesses right:

1. Clips are requested BY SEGMENT ID (start_segment/end_segment), not by
   freely-invented seconds. Ids map deterministically to the exact word-level
   timestamps, so clips can never "lose" their first or last sentence.
2. Chunks overlap by `llm.chunk_overlap_words`, so a highlight straddling a
   window boundary is not silently amputated; duplicates are merged.
3. Post-processing trims the LLM's rough range to real sentence boundaries
   using the per-word gaps in the transcript, then enforces length limits.

Output: data/clip_candidates/<video_id>_candidates.json
"""
import json
from pathlib import Path

from src.config import config
from src.llm_client import call_ollama
from src.select_prompts import SELECT_SYSTEM_PROMPT, SELECT_RETRY_PROMPT

MIN_CLIP_LEN = 12.0
TARGET_MIN_LEN = 20.0
MAX_CLIP_LEN = 120.0


def _as_rules(rules_summary=None):
    """Normalize a campaign rules object or a legacy text blob."""
    if isinstance(rules_summary, dict):
        return rules_summary
    if isinstance(rules_summary, str) and rules_summary.strip():
        return {"content_criteria": [ln.strip().lstrip("-• ")
                                     for ln in rules_summary.splitlines()
                                     if ln.strip() and not ln.strip().startswith("#")]}
    path = Path(config.rules_file)
    if path.suffix.lower() == ".json" and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    kept = [ln.strip().lstrip("-• ") for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    return {"content_criteria": kept} if kept else {}


def _bullet_block(items):
    lines = []
    for item in items or []:
        s = str(item).strip()
        if s:
            lines.append(f"- {s}")
    return "\n".join(lines)


def _build_system_prompt(rules_summary=None):
    prompt = SELECT_SYSTEM_PROMPT
    rules = _as_rules(rules_summary)
    if not rules:
        return prompt
    parts = []
    safety = _bullet_block(rules.get("brand_safety"))
    if safety:
        parts.append(
            "<brand_safety_hard_filters>\n"
            "NEVER select a moment that violates any of these. They are "
            "hard exclusion filters, not preferences. If a highlight would "
            "break one, drop it even if it would otherwise score highly:\n"
            f"{safety}\n</brand_safety_hard_filters>"
        )
    criteria = _bullet_block(rules.get("content_criteria"))
    if criteria:
        parts.append(
            "<content_criteria_preferences>\n"
            "Prefer (or avoid) moments that match these scoring preferences:\n"
            f"{criteria}\n</content_criteria_preferences>"
        )
    style = _bullet_block(rules.get("editing_style"))
    if style:
        parts.append(
            "<editing_style>\n"
            "When choosing among otherwise valid clips, prefer ones that "
            "fit this editing style:\n"
            f"{style}\n</editing_style>"
        )
    if parts:
        prompt += "\n\n" + "\n\n".join(parts)
    return prompt


def _fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _chunk_segments(segments, max_words, overlap_words):
    """Split into ~max_words chunks, carrying `overlap_words` of overlap over
    so boundary-straddling highlights survive."""
    if overlap_words is None:
        overlap_words = max(50, int(max_words * 0.2))
    overlap_words = max(0, min(overlap_words, max_words // 2))
    chunks, current, word_count = [], [], 0
    for seg in segments:
        wc = len(seg.get("words") or []) or len(seg.get("text", "").split())
        current.append(seg)
        word_count += wc
        if word_count >= max_words:
            chunks.append(current)
            # carry trailing overlap tokens into the next chunk
            carry, carry_wc = [], 0
            for seg_back in reversed(current):
                seg_wc = len(seg_back.get("words") or []) or len(seg_back.get("text", "").split())
                if carry_wc + seg_wc > overlap_words:
                    break
                carry.insert(0, seg_back)
                carry_wc += seg_wc
            current, word_count = carry, carry_wc
    if current:
        chunks.append(current)
    return chunks


def _format_chunk(segments):
    lines = []
    for seg in segments:
        sid = seg["id"]
        lines.append(f"[S{sid}] ({_fmt_time(seg['start'])} - {_fmt_time(seg['end'])}) "
                     f"{seg.get('text', '').strip()}")
    return "\n".join(lines)


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


def _build_user_prompt(context, chunk_text, chunk_idx, total_chunks):
    ctx = {
        "video_type": context.get("video_type"),
        "tone": context.get("tone"),
        "topics": context.get("topics"),
        "target_platform": context.get("target_platform"),
    }
    return (
        f"Video context: {json.dumps(ctx, ensure_ascii=False)}\n"
        f"Excerpt {chunk_idx + 1}/{total_chunks} of the video "
        f"(timestamps inside square brackets are the exact segment ranges):\n\n"
        f"{chunk_text}\n\n"
        "Pick the strongest highlight clips from this excerpt. Reference every "
        "clip by its segment ids (start_segment/end_segment). Return JSON per "
        "the system instruction."
    )


# --------------------------------------------------------------------------- #
# post-processing: ids -> timestamps, sentence-boundary trimming, limits
# --------------------------------------------------------------------------- #
def _word_gaps_in(segments, start, end):
    """Yield (gap_start, gap_length) pauses inside [start, end] from the
    word stream Ã¢â‚¬â€ a gap >= 0.25s is a usable sentence boundary."""
    prev_end = None
    for seg in segments:
        if seg["end"] <= start or seg["start"] >= end:
            continue
        for w in seg.get("words") or []:
            if prev_end is not None and w["start"] > prev_end:
                gap = w["start"] - prev_end
                if gap >= 0.25:
                    yield prev_end, gap
            prev_end = w["end"]


def _trim_to_boundaries(segments, start, end):
    """Snap a rough LLM range to natural sentence edges without shrinking it
    below MIN_CLIP_LEN: extend outward to the nearest pause when the edge
    lands mid-sentence (tolerance 0.8s), never past a real pause that is
    already inside the range."""
    # start: don't cut the first word; pull back to the nearest segment start
    seg_start = next((s["start"] for s in segments if s["start"] <= start + 0.6 and s["end"] > start), None)
    if seg_start is not None and start - seg_start <= 0.8:
        start = seg_start
    # end: extend to the natural end of the last touched sentence
    seg_end = next((s["end"] for s in reversed(segments) if s["start"] < end and s["end"] >= end - 0.8), None)
    if seg_end is not None and seg_end - end <= 1.5:
        end = seg_end
    return start, end


def _extend_short(s_id, e_id, segments_by_id, all_ids, duration, max_pull=2):
    """Widen a clip below TARGET_MIN_LEN by pulling in adjacent segments
    (up to max_pull per side), never crossing a >1.2s silence gap.

    Very short clips usually mean the LLM trimmed the setup/punchline context;
    widening to the nearest natural edges restores it. If the neighbourhood
    can't reach the target, the original range is returned unchanged.
    """
    if s_id not in all_ids or e_id not in all_ids:
        return s_id, e_id
    i0, i1 = all_ids.index(s_id), all_ids.index(e_id)
    start = segments_by_id[all_ids[i0]]["start"]
    end = segments_by_id[all_ids[i1]]["end"]
    if end - start >= TARGET_MIN_LEN:
        return s_id, e_id

    left, right = 0, 0
    while end - start < TARGET_MIN_LEN and left + right < 2 * max_pull:
        progressed = False
        if left < max_pull and i0 - left - 1 >= 0:
            cand = all_ids[i0 - left - 1]
            if start - segments_by_id[cand]["end"] <= 1.2:
                start = segments_by_id[cand]["start"]
                left += 1
                progressed = True
        if not progressed and right < max_pull and i1 + right + 1 < len(all_ids):
            cand = all_ids[i1 + right + 1]
            if segments_by_id[cand]["start"] - end <= 1.2:
                end = segments_by_id[cand]["end"]
                right += 1
                progressed = True
        if not progressed:
            break
    new_s = all_ids[i0 - left]
    new_e = all_ids[i1 + right]
    return new_s, new_e


def _resolve_clip(c, segments_by_id, all_segments, duration):
    """Map LLM ids (or fallback seconds) to exact timestamps."""
    all_ids = [s["id"] for s in all_segments]
    start_seg = c.get("start_segment")
    end_seg = c.get("end_segment", start_seg)
    try:
        start_seg, end_seg = int(start_seg), int(end_seg)
    except (TypeError, ValueError):
        start_seg = end_seg = None

    if start_seg is not None and end_seg is not None and start_seg <= end_seg:
        if start_seg not in segments_by_id or end_seg not in segments_by_id:
            return None
        start_seg, end_seg = _extend_short(start_seg, end_seg, segments_by_id,
                                           all_ids, duration)
        start = segments_by_id[start_seg]["start"]
        end = segments_by_id[end_seg]["end"]
        s_id, e_id = start_seg, end_seg
    else:
        # model ignored ids -> fall back to nearest-segment snapping
        try:
            start, end = float(c.get("start")), float(c.get("end"))
        except (TypeError, ValueError):
            return None
        if start >= end:
            return None
        s_id = min(all_segments, key=lambda s: abs(s["start"] - start))["id"]
        e_id = min(all_segments, key=lambda s: abs(s["end"] - end))["id"]
        if s_id > e_id:
            return None
        start = segments_by_id[s_id]["start"]
        end = segments_by_id[e_id]["end"]

    start, end = max(0.0, start), min(float(duration), end)
    start, end = _trim_to_boundaries(all_segments, start, end)
    if end - start < MIN_CLIP_LEN:
        return None
    if end - start > MAX_CLIP_LEN:
        end = start + MAX_CLIP_LEN
    return start, end, s_id, e_id


def _clean_broll(broll_raw, start, end):
    broll = []
    for b in (broll_raw or [])[:4]:
        if not isinstance(b, dict):
            continue
        try:
            b_start = max(start, min(float(b.get("start", start)), end))
            b_end = min(end, max(float(b.get("end", end)), b_start + 1.0))
        except (TypeError, ValueError):
            continue
        if b_end <= b_start:
            continue
        broll.append({
            "start": round(b_start, 3),
            "end": round(b_end, 3),
            "emotion": str(b.get("emotion", "")).strip().lower(),
            "note": str(b.get("note", "") or "").strip(),
        })
    return broll


def _merge_candidates(cands):
    """Dedupe overlapping candidates (chunk overlaps re-score the same text):
    keep the higher-scoring clip, extend it to cover the overlapping span."""
    cands.sort(key=lambda c: c["score"], reverse=True)
    kept = []
    for c in cands:
        hit = None
        for k in kept:
            inter = min(c["end"], k["end"]) - max(c["start"], k["start"])
            union = max(c["end"], k["end"]) - min(c["start"], k["start"])
            if inter > 0 and inter / union >= 0.4:
                hit = k
                break
        if hit is None:
            kept.append(c)
            continue
        # merge: take union of the range, keep the better score/reason
        if c["start"] < hit["start"]:
            hit["start"] = c["start"]
        if c["end"] > hit["end"]:
            hit["end"] = c["end"]
        if c["score"] > hit["score"]:
            hit["score"] = c["score"]
            hit["reason"] = c["reason"]
            hit["hook"] = c["hook"]
            hit["broll"] = c["broll"]
    for k in kept:
        if k["end"] - k["start"] > MAX_CLIP_LEN:
            k["end"] = k["start"] + MAX_CLIP_LEN
    return kept


def _finalize(clips_raw, segments, duration, min_score, max_clips):
    segments_by_id = {s["id"]: s for s in segments}
    cleaned = []
    for c in clips_raw:
        resolved = _resolve_clip(c, segments_by_id, segments, duration)
        if resolved is None:
            continue
        start, end, sid_start, sid_end = resolved
        try:
            score = float(c.get("score", 0.5))
        except (TypeError, ValueError):
            score = 0.5
        score = min(1.0, max(0.0, score))
        if score < min_score:
            continue
        cleaned.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "start_segment": sid_start,
            "end_segment": sid_end,
            "reason": str(c.get("reason", "")).strip(),
            "score": round(score, 3),
            "hook": str(c.get("hook", "") or "").strip(),
            "broll": _clean_broll(c.get("broll"), start, end),
            "status": "pending",
        })

    cleaned = _merge_candidates(cleaned)
    cleaned.sort(key=lambda c: c["score"], reverse=True)
    return cleaned[:max_clips]


# --------------------------------------------------------------------------- #
# main entry
# --------------------------------------------------------------------------- #
def select_highlights(video_path, transcript_path=None, context_path=None,
                      output_dir=None, max_clips=None, min_score=None,
                      rules_summary=None, rules_text=None, progress=None):
    from src.clean_transcript import best_transcript_path

    video_path = Path(video_path)
    transcript_path = Path(transcript_path) if transcript_path else best_transcript_path(video_path)
    context_path = Path(context_path) if context_path else \
        config.context_dir / f"{video_path.stem}_context.json"

    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}. Run transcribe first.")
    if not context_path.exists():
        raise FileNotFoundError(f"Context not found: {context_path}. Run context first.")

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    duration = float(transcript.get("duration", 0))
    segments = []
    for i, seg in enumerate(transcript.get("segments", [])):
        seg = dict(seg)
        seg["id"] = i
        segments.append(seg)
    max_clips = max_clips or config.llm_max_clips
    min_score = min_score if min_score is not None else config.llm_min_score

    if duration <= 0 or not segments:
        raise ValueError(f"Transcript {transcript_path} has no usable segments/duration.")

    print(f"[select] calling {config.llm_model} on {len(segments)} segments "
          f"({duration:.0f}s video) using transcript {transcript_path.name}")

    all_clips = []
    chunks = _chunk_segments(segments, config.llm_chunk_words,
                             config.llm_chunk_overlap_words)
    total_chunks = max(1, len(chunks))
    for i, chunk in enumerate(chunks):
        user_prompt = _build_user_prompt(context, _format_chunk(chunk), i, total_chunks)
        messages = [
            {"role": "system", "content": _build_system_prompt(rules_summary or rules_text)},
            {"role": "user", "content": user_prompt},
        ]
        print(f"[select] chunk {i + 1}/{total_chunks}")
        parsed = None
        for attempt in (1, 2):
            try:
                content = call_ollama(messages)
                parsed = _extract_json(content)
            except Exception as exc:  # noqa: BLE001
                print(f"[select]  attempt {attempt} failed: {exc}")
                parsed = None
            if parsed is not None:
                break
            messages.append({"role": "user", "content": SELECT_RETRY_PROMPT})
        if parsed is None:
            print("[select]  could not parse LLM output for this chunk; "
                  "flagging for manual review instead of crashing.")
        else:
            all_clips.extend(parsed.get("clips", []))
        if progress:
            progress((i + 1) / total_chunks)

    clips = _finalize(all_clips, segments, duration, min_score, max_clips)

    result = {
        "video_id": video_path.stem,
        "source": str(video_path),
        "duration": duration,
        "model": config.llm_model,
        "min_score": min_score,
        "clips": clips,
    }
    out_dir = Path(output_dir) if output_dir else config.candidates_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{video_path.stem}_candidates.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[select] saved {len(clips)} candidate clips -> {out_path}")
    return out_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Select highlights via LLM.")
    parser.add_argument("video")
    parser.add_argument("--transcript")
    parser.add_argument("--context")
    parser.add_argument("--max-clips", type=int)
    parser.add_argument("--min-score", type=float)
    args = parser.parse_args()
    select_highlights(args.video, transcript_path=args.transcript,
                      context_path=args.context, max_clips=args.max_clips,
                      min_score=args.min_score)