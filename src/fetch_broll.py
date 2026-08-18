"""B-roll library + emotion-driven placement.

Placement psychology (the important part): b-roll must NEVER show the host or
literal actions from the dialogue (no "he said gym -> show a gym"). It shows
OTHER people (stock or AI-generated) embodying the EMOTIONAL subtext of the
moment:

  struggle  - depressed, stressed, needing help, overwhelmed
  joy       - laughing, celebrating, relieved
  wealth    - counting money, success, luxury, earning
  health    - exercising, eating well, energetic
  focus     - deep work, studying, determination
  community - family, friends, support, togetherness

The LLM tags each cue with an emotion; this module resolves it to a local file
in data/broll/<emotion>/ (rotation, no repeat inside one clip). If the library
slot is empty and an API key is configured (PEXELS_API_KEY / PIXABAY_API_KEY),
missing emotions are fetched once and cached into the library. AI-generated
clips can be dropped into the same folders by hand — they are treated exactly
like stock.
"""
import json
import random
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from src.config import config

VALID_EMOTIONS = ("struggle", "joy", "wealth", "health", "focus", "community")

SEARCH_QUERIES = {
    "struggle": ["sad man alone dark", "stressed person head in hands",
                 "depressed man sitting alone"],
    "joy": ["happy people laughing", "man celebrating success",
            "friends laughing together"],
    "wealth": ["counting money hands", "successful businessman office",
               "luxury lifestyle"],
    "health": ["man running sunrise", "healthy eating vegetables",
               "workout gym motivation"],
    "focus": ["man working laptop focused", "studying late night",
              "deep concentration work"],
    "community": ["family hugging", "friends talking cafe",
                  "people helping each other"],
}

VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
MAX_CUES_PER_CLIP = 4
MAX_CUE_LEN = 8.0


def library_dir():
    return config.broll_dir


def _library_files(emotion):
    d = library_dir() / emotion
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS + IMAGE_EXTS)


def _pick(emotion, used, rng):
    files = [f for f in _library_files(emotion) if f.name not in used]
    if not files:
        files = _library_files(emotion)
    if not files:
        return None
    return rng.choice(files)


def _probe_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except (ValueError, AttributeError):
        return None


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())


def _fetch_pexels(emotion, count=2):
    key = config.broll_pexels_key
    if not key:
        return 0
    added = 0
    for query in SEARCH_QUERIES[emotion][:2]:
        url = ("https://api.pexels.com/videos/search?query="
               + urllib.parse.quote(query) + "&per_page=2")
        req = urllib.request.Request(
            url, headers={"Authorization": key, "User-Agent": "ClipForge/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            print(f"[broll] pexels fetch failed for '{query}': {exc}")
            continue
        for video in data.get("videos", [])[:count]:
            files = [f for f in video.get("video_files", [])
                     if f.get("file_type") == "video/mp4" and f.get("link")]
            if not files:
                continue
            best = min(files, key=lambda f: abs((f.get("width") or 0) - 1280))
            link = best.get("link")
            dest = library_dir() / emotion / f"pexels_{video.get('id')}_{added}.mp4"
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                _download(link, dest)
                added += 1
            except (urllib.error.URLError, OSError) as exc:
                print(f"[broll] download failed: {exc}")
                dest.unlink(missing_ok=True)
    return added


def _fetch_pixabay(emotion, count=2):
    key = config.broll_pixabay_key
    if not key:
        return 0
    added = 0
    for query in SEARCH_QUERIES[emotion][:2]:
        url = ("https://pixabay.com/api/videos/?key=" + key
               + "&q=" + urllib.parse.quote(query) + "&per_page=2")
        req = urllib.request.Request(url, headers={"User-Agent": "ClipForge/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            print(f"[broll] pixabay fetch failed for '{query}': {exc}")
            continue
        for hit in data.get("hits", [])[:count]:
            videos = hit.get("videos", {})
            medium = videos.get("medium") or videos.get("small") or videos.get("tiny")
            link = (medium or {}).get("url")
            if not link:
                continue
            dest = library_dir() / emotion / f"pixabay_{hit.get('id')}_{added}.mp4"
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                _download(link, dest)
                added += 1
            except (urllib.error.URLError, OSError) as exc:
                print(f"[broll] download failed: {exc}")
                dest.unlink(missing_ok=True)
    return added


def _clean_cues(cues, clip_start, clip_end):
    """Clamp cues into the padded clip window, cap count/length, drop bad ones."""
    cleaned = []
    for c in cues or []:
        try:
            start, end = float(c.get("start")), float(c.get("end"))
        except (TypeError, ValueError):
            continue
        emotion = str(c.get("emotion", "")).strip().lower()
        if emotion not in VALID_EMOTIONS:
            continue
        start = max(clip_start, min(start, clip_end - 1.0))
        end = min(clip_end, max(end, start + 1.5))
        if end - start > MAX_CUE_LEN:
            end = start + MAX_CUE_LEN
        if end <= start:
            continue
        cleaned.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "emotion": emotion,
            "note": str(c.get("note", "") or "").strip(),
        })
        if len(cleaned) >= MAX_CUES_PER_CLIP:
            break
    cleaned.sort(key=lambda c: c["start"])
    return cleaned


def resolve_cues(clip, seed=0):
    """Pick library files for each cue of a clip. Returns resolved cue list."""
    cues = _clean_cues(clip.get("broll"), clip["start"], clip["end"])
    if not cues:
        return []
    rng = random.Random(seed)
    used = set()
    resolved = []
    for cue in cues:
        path = _pick(cue["emotion"], used, rng)
        if path is None:
            print(f"[broll] no library asset for '{cue['emotion']}' "
                  f"(drop clips/images into {library_dir() / cue['emotion']} "
                  f"or set PEXELS_API_KEY/PIXABAY_API_KEY and run broll fetch)")
            continue
        used.add(path.name)
        resolved.append({**cue, "file": str(path),
                         "kind": "image" if path.suffix.lower() in IMAGE_EXTS else "video"})
    return resolved


def fetch_missing(progress=None):
    """Fill empty emotion slots from configured providers. Returns counts."""
    library_dir().mkdir(parents=True, exist_ok=True)
    report = {}
    emotions = [e for e in VALID_EMOTIONS if not _library_files(e)]
    for i, emotion in enumerate(emotions):
        print(f"[broll] fetching stock for '{emotion}'")
        n = _fetch_pexels(emotion)
        if n == 0:
            n = _fetch_pixabay(emotion)
        report[emotion] = n
        if progress:
            progress((i + 1) / max(1, len(emotions)))
    return report


def build_manifest(video_stem, clips):
    """Resolve b-roll for every clip and persist data/broll/<stem>_broll.json."""
    library_dir().mkdir(parents=True, exist_ok=True)
    entries = []
    for i, clip in enumerate(clips, start=1):
        resolved = resolve_cues(clip, seed=i)
        if resolved:
            entries.append({"clip_index": i, "cues": resolved})
    data = {"video_id": video_stem, "clips": entries}
    path = library_dir() / f"{video_stem}_broll.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(e["cues"]) for e in entries)
    print(f"[broll] manifest -> {path} ({total} cues across {len(entries)} clips)")
    return path


def read_manifest(video_stem):
    path = library_dir() / f"{video_stem}_broll.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def slugify(text, n=40):
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:n] or "clip"
