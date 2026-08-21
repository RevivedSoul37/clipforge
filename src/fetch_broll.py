"""On-clip stock B-roll: LLM query → Pexels/Pixabay → local cache.

The LLM tags each cue with a short stock-search query. At clip time we fetch
that query from Pexels, then Pixabay, cache under data/broll/cache/<slug>/,
and overlay the file. Emotion tags from older candidates are mapped to a
fallback query so existing JSON still resolves.
"""
import json
import random
import re
import subprocess
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
MAX_QUERY_LEN = 80


def library_dir():
    return config.broll_dir


def cache_root():
    return library_dir() / "cache"


def slugify(text, n=40):
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:n] or "clip"


def _normalize_query(text):
    q = re.sub(r"\s+", " ", str(text or "").strip())[:MAX_QUERY_LEN]
    return q


def _query_for_cue(cue):
    query = _normalize_query(cue.get("query"))
    if query:
        return query
    emotion = str(cue.get("emotion", "")).strip().lower()
    fallback = SEARCH_QUERIES.get(emotion)
    if fallback:
        return fallback[0]
    return ""


def _cache_dir(query):
    return cache_root() / slugify(query, n=60)


def _cache_files(query):
    d = _cache_dir(query)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS + IMAGE_EXTS)


def cache_stats():
    root = cache_root()
    if not root.is_dir():
        return {"queries": 0, "files": 0}
    queries = 0
    files = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        n = sum(1 for p in d.iterdir()
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS + IMAGE_EXTS)
        if n:
            queries += 1
            files += n
    return {"queries": queries, "files": files}


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


def _fetch_pexels_query(query, dest_dir, count=2):
    key = config.broll_pexels_key
    if not key:
        return 0
    url = ("https://api.pexels.com/videos/search?query="
           + urllib.parse.quote(query) + "&per_page=4")
    req = urllib.request.Request(
        url, headers={"Authorization": key, "User-Agent": "ClipForge/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[broll] pexels fetch failed for '{query}': {exc}")
        return 0
    added = 0
    dest_dir.mkdir(parents=True, exist_ok=True)
    for video in data.get("videos", []):
        if added >= count:
            break
        files = [f for f in video.get("video_files", [])
                 if f.get("file_type") == "video/mp4" and f.get("link")]
        if not files:
            continue
        best = min(files, key=lambda f: abs((f.get("width") or 0) - 1280))
        link = best.get("link")
        dest = dest_dir / f"pexels_{video.get('id')}.mp4"
        if dest.exists():
            added += 1
            continue
        try:
            _download(link, dest)
            added += 1
        except (urllib.error.URLError, OSError) as exc:
            print(f"[broll] download failed: {exc}")
            dest.unlink(missing_ok=True)
    return added


def _fetch_pixabay_query(query, dest_dir, count=2):
    key = config.broll_pixabay_key
    if not key:
        return 0
    url = ("https://pixabay.com/api/videos/?key=" + key
           + "&q=" + urllib.parse.quote(query) + "&per_page=4")
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[broll] pixabay fetch failed for '{query}': {exc}")
        return 0
    added = 0
    dest_dir.mkdir(parents=True, exist_ok=True)
    for hit in data.get("hits", []):
        if added >= count:
            break
        videos = hit.get("videos", {})
        medium = videos.get("medium") or videos.get("small") or videos.get("tiny")
        link = (medium or {}).get("url")
        if not link:
            continue
        dest = dest_dir / f"pixabay_{hit.get('id')}.mp4"
        if dest.exists():
            added += 1
            continue
        try:
            _download(link, dest)
            added += 1
        except (urllib.error.URLError, OSError) as exc:
            print(f"[broll] download failed: {exc}")
            dest.unlink(missing_ok=True)
    return added


def _ensure_files(query):
    files = _cache_files(query)
    if files:
        return files
    if not config.broll_pexels_key and not config.broll_pixabay_key:
        print("[broll] no PEXELS_API_KEY / PIXABAY_API_KEY — skip fetch for "
              f"'{query}'")
        return []
    dest = _cache_dir(query)
    print(f"[broll] fetching stock for '{query}'")
    n = _fetch_pexels_query(query, dest)
    if n == 0:
        n = _fetch_pixabay_query(query, dest)
    if n == 0:
        print(f"[broll] no stock results for '{query}'")
    return _cache_files(query)


def _pick(query, used, rng):
    files = _ensure_files(query)
    unused = [f for f in files if f.name not in used]
    pool = unused or files
    if not pool:
        return None
    return rng.choice(pool)


def _clean_cues(cues, clip_start, clip_end):
    """Clamp cues into the padded clip window, cap count/length, drop bad ones."""
    cleaned = []
    for c in cues or []:
        try:
            start, end = float(c.get("start")), float(c.get("end"))
        except (TypeError, ValueError):
            continue
        query = _query_for_cue(c)
        if not query:
            continue
        start = max(clip_start, min(start, clip_end - 1.0))
        end = min(clip_end, max(end, start + 1.5))
        if end - start > MAX_CUE_LEN:
            end = start + MAX_CUE_LEN
        if end <= start:
            continue
        item = {
            "start": round(start, 3),
            "end": round(end, 3),
            "query": query,
            "note": str(c.get("note", "") or "").strip(),
        }
        emotion = str(c.get("emotion", "")).strip().lower()
        if emotion:
            item["emotion"] = emotion
        cleaned.append(item)
        if len(cleaned) >= MAX_CUES_PER_CLIP:
            break
    cleaned.sort(key=lambda c: c["start"])
    return cleaned


def resolve_cues(clip, seed=0):
    """Fetch/cache stock files for each cue. Returns resolved cue list."""
    cues = _clean_cues(clip.get("broll"), clip["start"], clip["end"])
    if not cues:
        return []
    rng = random.Random(seed)
    used = set()
    resolved = []
    for cue in cues:
        path = _pick(cue["query"], used, rng)
        if path is None:
            print(f"[broll] no stock asset for '{cue['query']}'")
            continue
        used.add(path.name)
        resolved.append({
            **cue,
            "file": str(path),
            "kind": "image" if path.suffix.lower() in IMAGE_EXTS else "video",
        })
    return resolved


def fetch_missing(progress=None):
    """B-roll is fetched per clip in build_manifest. Reports provider status."""
    if progress:
        progress(1.0)
    return {
        "pexels": bool(config.broll_pexels_key),
        "pixabay": bool(config.broll_pixabay_key),
    }


def build_manifest(video_stem, clips):
    """Fetch stock for every clip cue and persist data/broll/<stem>_broll.json."""
    library_dir().mkdir(parents=True, exist_ok=True)
    cache_root().mkdir(parents=True, exist_ok=True)
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
