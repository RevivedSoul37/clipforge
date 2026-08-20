"""Phase 4 — Review UI (Streamlit).

Local web app to review LLM-selected clip candidates: one clip at a time with a
video preview, transcript snippet, reason/score, and approve/reject/edit controls.
Writes status back into the candidate JSON so later stages can act on it.

Run with: python -m streamlit run src/review_ui.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src.config import config
from src.cut_clips import cut_one


def _candidate_files():
    config.candidates_dir.mkdir(parents=True, exist_ok=True)
    return sorted(config.candidates_dir.glob("*_candidates.json"))


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _transcript_for(data):
    from src.clean_transcript import best_transcript_path
    p = best_transcript_path(data["video_id"])
    return _load(p) if p.exists() else None


def _snippet(transcript, start, end):
    if not transcript:
        return "(no transcript found)"
    parts = []
    for seg in transcript.get("segments", []):
        if seg["end"] < start or seg["start"] > end:
            continue
        parts.append(f"[{int(seg['start'] // 60):02d}:{int(seg['start'] % 60):02d}] {seg.get('text', '')}")
    return "\n".join(parts) if parts else "(no transcript in range)"


def _counts(clips):
    from collections import Counter
    return Counter(c.get("status", "pending") for c in clips)


st.set_page_config(page_title="ClipForge — Review", page_icon="🎬", layout="wide")
st.title("ClipForge — Review")

files = _candidate_files()
if not files:
    st.info("No candidates yet. Run `python main.py select <video>` first.")
    st.stop()

selected = st.sidebar.selectbox("Video", files, format_func=lambda p: p.stem.replace("_candidates", ""))
data = _load(selected)
clips = data["clips"]
source = data.get("source")

counts = _counts(clips)
st.sidebar.markdown(
    f"**{len(clips)} clips** — {counts.get('approved', 0)} approved, "
    f"{counts.get('rejected', 0)} rejected, {counts.get('pending', 0)} pending")

threshold = st.sidebar.number_input("Approve all above score", 0.0, 1.0, float(data.get("min_score", 0.5)), 0.05)
if st.sidebar.button("Approve all above threshold"):
    for c in clips:
        if c.get("score", 0.0) >= threshold and c.get("status") != "rejected":
            c["status"] = "approved"
    _save(selected, data)
    st.rerun()

transcript = _transcript_for(data)

for i, clip in enumerate(clips):
    status = clip.get("status", "pending")
    icon = {"approved": "✅", "rejected": "❌"}.get(status, "⬜")
    with st.expander(f"{icon} Clip {i + 1} — {clip['score']:.2f} — {clip['reason']}", expanded=(status == "pending")):
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        new_start = c1.number_input("Start (s)", 0.0, float(data["duration"]), float(clip["start"]), 0.1, key=f"s{i}")
        new_end = c2.number_input("End (s)", 0.0, float(data["duration"]), float(clip["end"]), 0.1, key=f"e{i}")
        if new_start != clip["start"] or new_end != clip["end"]:
            clip["start"], clip["end"] = round(new_start, 2), round(new_end, 2)

        c3.markdown(f"**Score:** {clip['score']:.2f}")
        c4.markdown(f"**Reason:** {clip['reason']}")

        st.code(_snippet(transcript, clip["start"], clip["end"]), language="text")

        b1, b2, b3 = st.columns(3)
        if b1.button("✅ Approve", key=f"a{i}"):
            clip["status"] = "approved"
            _save(selected, data)
            st.rerun()
        if b2.button("❌ Reject", key=f"r{i}"):
            clip["status"] = "rejected"
            _save(selected, data)
            st.rerun()
        if b3.button("▶ Preview cut", key=f"p{i}"):
            if not source or not Path(source).exists():
                st.error(f"Source video missing: {source}")
            else:
                preview_path = config.raw_dir / f"preview_{data['video_id']}_clip_{i + 1:02d}.mp4"
                with st.spinner("Cutting preview..."):
                    _, _, _ = cut_one(source, clip["start"], clip["end"], preview_path, lead_in=0.0, lead_out=0.0)
                st.video(str(preview_path))

if st.button("💾 Save", type="primary"):
    _save(selected, data)
    st.success("Saved.")
