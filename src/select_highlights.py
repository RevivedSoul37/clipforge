"""Phase 2 — Highlight selection (LLM).

Sends the transcript + per-video context to a local LLM (Ollama) with a scoring
prompt, and parses a structured JSON list of candidate clips: start, end, reason,
score. Transcripts longer than the model context are chunked by time, scored per
chunk, then merged, deduped, clamped, and sorted by score.
"""
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from src.config import config

SYSTEM_PROMPT = (
    "You are an expert short-form video clip editor. You read a transcript of a "
    "long video and pick the moments that would make strong standalone clips for "
    "social media (Shorts, Reels, TikTok). A strong clip has a clear hook, a "
    "punchline, an emotional peak, or a genuinely useful insight, and is "
    "self-contained (a viewer with no other context understands it).\n"
    "Respond with JSON only, in this exact shape:\n"
    '{"clips": [{"start": <seconds>, "end": <seconds>, "reason": "<short tag + why>", '
    '"score": <0.0 to 1.0>, "hook": "<one punchy on-screen title line for this clip>", '
    '"broll": [{"start": <seconds>, "end": <seconds>, "emotion": "<one of the list>", '
    '"note": "<why this emotion fits>"}]}]}\n'
    "start/end are absolute seconds from the start of the full video. Keep each "
    "clip between 30 and 90 seconds. Do not invent dialogue; only clip real "
    "moments present in the transcript. The hook is a short curiosity-driving "
    "title (max 8 words) shown on screen above the clip, derived from the clip's "
    "content.\n"
    "B-ROLL PLACEMENT PSYCHOLOGY (critical): for 1-3 short emotional beats per "
    "clip, tag a broll cue. The b-roll will ALWAYS show OTHER people (stock or "
    "AI-generated), never the host, and never literal actions from the dialogue "
    "(do NOT show a gym because he says 'gym'). Pick the EMOTIONAL subtext of the "
    "moment from exactly these emotions: struggle (depressed, stressed, needing "
    "help, overwhelmed), joy (laughing, celebrating, relief), wealth (earning, "
    "success, counting money), health (energetic, exercising, eating well), "
    "focus (deep work, determination), community (family, friends, support). "
    "Each cue 2-8 seconds, inside the clip's start/end."
)

STRICT_RETRY_PROMPT = (
    "Your previous answer was not valid JSON. Reply with ONLY a single JSON "
    "object and nothing else (no markdown, no commentary): "
    '{"clips": [{"start": <seconds>, "end": <seconds>, "reason": "<string>", '
    '"score": <number>}]}'
)


def _load_rules():
    try:
        lines = Path(config.rules_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    kept = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    return "\n".join(kept)


def _build_system_prompt():
    prompt = SYSTEM_PROMPT
    rules = _load_rules()
    if rules:
        prompt += ("\n\nUSER-DEFINED SELECTION RULES (follow these when scoring "
                   "and picking clips):\n" + rules)
    return prompt


def _fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _call_ollama(messages, model, base_url, format_json=True, timeout=600):
    endpoint = f"{base_url.rstrip('/')}/api/chat"
    attempts = (True, False) if format_json else (False,)
    last_err = None
    for use_format in attempts:
        payload = {"model": model, "messages": messages, "stream": False}
        if use_format:
            payload["format"] = "json"
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["message"]["content"]
        except urllib.error.HTTPError as exc:
            last_err = exc
            continue  # e.g. model rejects `format: json`; retry without it
    raise last_err


def _chunk_segments(segments, max_words):
    chunks, current, word_count = [], [], 0
    for seg in segments:
        wc = len(seg.get("words") or []) or len(seg.get("text", "").split())
        current.append(seg)
        word_count += wc
        if word_count >= max_words:
            chunks.append(current)
            current, word_count = [], 0
    if current:
        chunks.append(current)
    return chunks


def _format_chunk(segments):
    lines = []
    for seg in segments:
        lines.append(f"[{_fmt_time(seg['start'])}] {seg.get('text', '').strip()}")
    return "\n".join(lines)


def _extract_json(content):
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _build_user_prompt(context, chunk_text):
    ctx = {
        "video_type": context.get("video_type"),
        "tone": context.get("tone"),
        "topics": context.get("topics"),
        "target_platform": context.get("target_platform"),
    }
    return (
        f"Video context: {json.dumps(ctx, ensure_ascii=False)}\n\n"
        "Transcript excerpt (timestamps are absolute seconds from video start):\n"
        f"{chunk_text}\n\n"
        "Pick the strongest highlight clips from this excerpt. "
        "Return JSON per the system instruction."
    )


def _finalize(clips, duration, max_clips):
    cleaned = []
    for c in clips:
        try:
            start, end = float(c.get("start")), float(c.get("end"))
            score = float(c.get("score", 0.5))
        except (TypeError, ValueError):
            continue
        if start >= end:
            continue
        start, end = max(0.0, start), min(float(duration), end)
        if end - start < 30.0:
            continue
        if end - start > 90.0:
            end = start + 90.0
        broll = []
        for b in (c.get("broll") or [])[:4]:
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
        cleaned.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "reason": str(c.get("reason", "")).strip(),
            "score": round(min(1.0, max(0.0, score)), 3),
            "hook": str(c.get("hook", "") or "").strip(),
            "broll": broll,
            "status": "pending",
        })

    cleaned.sort(key=lambda c: c["score"], reverse=True)
    kept = []
    for c in cleaned:
        if any(c["start"] < k["end"] and c["end"] > k["start"] for k in kept):
            continue
        kept.append(c)
        if len(kept) >= max_clips:
            break
    return kept


def select_highlights(video_path, transcript_path=None, context_path=None,
                      output_dir=None, max_clips=None, min_score=None, progress=None):
    video_path = Path(video_path)
    transcript_path = Path(transcript_path) if transcript_path else \
        config.transcripts_dir / f"{video_path.stem}_transcript.json"
    context_path = Path(context_path) if context_path else \
        config.context_dir / f"{video_path.stem}_context.json"

    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}. Run transcribe first.")
    if not context_path.exists():
        raise FileNotFoundError(f"Context not found: {context_path}. Run context first.")

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    duration = float(transcript.get("duration", 0))
    segments = transcript.get("segments", [])
    max_clips = max_clips or config.llm_max_clips
    min_score = min_score if min_score is not None else config.llm_min_score

    if duration <= 0 or not segments:
        raise ValueError(f"Transcript {transcript_path} has no usable segments/duration.")

    print(f"[select] calling {config.llm_model} on {len(segments)} segments "
          f"({duration:.0f}s video)")

    all_clips = []
    chunks = _chunk_segments(segments, config.llm_chunk_words)
    total_chunks = max(1, len(chunks))
    for i, chunk in enumerate(chunks):
        user_prompt = _build_user_prompt(context, _format_chunk(chunk))
        messages = [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": user_prompt},
        ]
        print(f"[select] chunk {i + 1}/{total_chunks}")
        parsed = None
        for attempt in (1, 2):
            try:
                content = _call_ollama(messages, config.llm_model, config.llm_base_url)
                parsed = _extract_json(content)
            except Exception as exc:  # noqa: BLE001
                print(f"[select]  attempt {attempt} failed: {exc}")
                parsed = None
            if parsed is not None:
                break
            messages.append({"role": "user", "content": STRICT_RETRY_PROMPT})
        if parsed is None:
            print("[select]  could not parse LLM output for this chunk; "
                  "flagging for manual review instead of crashing.")
        else:
            all_clips.extend(parsed.get("clips", []))
        if progress:
            progress((i + 1) / total_chunks)

    clips = _finalize(all_clips, duration, max_clips)

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
