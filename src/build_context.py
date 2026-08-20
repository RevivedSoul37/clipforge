"""Phase 1.5 â€” Per-video context builder.

Before highlight selection, generate a context.json describing the video's type,
tone, topic, and target platform. v1 uses a lightweight heuristic classifier over
the transcript (no extra LLM call); the file is meant to be hand-editable so a
re-run of highlight selection stays grounded in the real video.
"""
import json
import re
from collections import Counter
from pathlib import Path

from src.config import config

STOPWORDS = set("""
a an the and or but if then else when where how what which who whom i you he she it
we they me him her us them my your his its our their is are was were be been being
am do does did doing have has had having of in on at to for with by from as into
about over under after before this that these those there here so such not no yes
just very really can could should would may might must shall will it's that's don't
i'm like get got go goes going went know think said say says got get yeah okay ok
uh um oh well so actually basically really gonna wanna
""".split())

GAMING_TERMS = ("game", "gaming", "stream", "streamer", "clutch", "ranked", "level",
                "kill", "enemy", "player", "controller", "boss", "loot", "queue",
                "match", "win", "lose", "team", "meta", "nerf", "buff", "skin",
                "loadout", "round", "server", "spawn", "squad", "duo", "health")
PODCAST_TERMS = ("podcast", "episode", "guest", "interview", "welcome back",
                 "subscribe", "today we're", "today we are", "conversation", "episode")
VLOG_TERMS = ("vlog", "day in the life", "behind the scenes", "travel", "trip",
              "morning routine", "unboxing", "come with me", "let me show you")

EXCITING_PHRASES = ("let's go", "lets go", "no way", "oh my god", "oh my gosh",
                    "insane", "crazy", "unbelievable", "huge", "amazing", "clutch",
                    "what a", "that was", "holy", "wow", "unreal")
FUNNY_PHRASES = ("haha", "laugh", "lol", "funny", "joke", "that's hilarious",
                 "thats hilarious", "lmao", "rofl")
INSIGHT_PHRASES = ("the thing is", "what i learned", "the reason", "honestly",
                   "realize", "important", "basically", "here's the thing",
                   "if you think about it", "key takeaway", "lesson")


def _tokens(text):
    return re.findall(r"[a-z']+", text.lower())


def classify_type(text):
    t = text.lower()
    scores = {
        "gaming": sum(1 for term in GAMING_TERMS if term in t),
        "podcast": sum(1 for term in PODCAST_TERMS if term in t),
        "vlog": sum(1 for term in VLOG_TERMS if term in t),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unknown"


def classify_tone(text):
    t = text.lower()
    scores = {
        "exciting": sum(1 for p in EXCITING_PHRASES if p in t),
        "funny": sum(1 for p in FUNNY_PHRASES if p in t),
        "insightful": sum(1 for p in INSIGHT_PHRASES if p in t),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "neutral"


def extract_topics(text, n=5):
    counts = Counter(w for w in _tokens(text) if w not in STOPWORDS and len(w) > 3)
    return [w for w, _ in counts.most_common(n)]


def _full_text(transcript):
    return " ".join(seg.get("text", "") for seg in transcript.get("segments", []))


def build_context(video_path, transcript_path=None, overrides=None, output_dir=None):
    from src.clean_transcript import best_transcript_path

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    transcript_path = Path(transcript_path) if transcript_path else best_transcript_path(video_path)
    if not transcript_path.exists():
        raise FileNotFoundError(
            f"Transcript not found: {transcript_path}. Run transcribe first.")

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    text = _full_text(transcript)
    overrides = overrides or {}

    context = {
        "video_id": video_path.stem,
        "source": str(video_path),
        "duration": transcript.get("duration"),
        "language": transcript.get("language"),
        "video_type": overrides.get("video_type") or classify_type(text),
        "tone": overrides.get("tone") or classify_tone(text),
        "topics": overrides.get("topics") or extract_topics(text),
        "target_platform": overrides.get("target_platform") or config.target_platform,
        "generated_by": "heuristic",
        "note": "Heuristic classification from the transcript. Edit these fields to "
                "override before running highlight selection.",
    }

    out_dir = Path(output_dir) if output_dir else config.context_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{video_path.stem}_context.json"
    out_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[context] saved -> {out_path} ({context['video_type']}/{context['tone']})")
    return out_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build per-video context JSON.")
    parser.add_argument("video")
    parser.add_argument("--transcript")
    args = parser.parse_args()
    build_context(args.video, transcript_path=args.transcript)
