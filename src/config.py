"""Central configuration for ClipForge.

Loads config.json (user settings) and .env (secrets/overrides), then exposes a
single `config` object with resolved absolute paths and typed defaults.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}")


def _deep_get(mapping: dict, dotted: str, default=None):
    cur = mapping
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


class Config:
    def __init__(self):
        self.root = ROOT
        self.env = {**os.environ, **_load_dotenv(ROOT / ".env")}
        self.data = _load_json(ROOT / "config.json")

        paths = _deep_get(self.data, "paths", {}) or {}
        self.input_dir = self._resolve(paths.get("input", "input"))
        self.output_dir = self._resolve(paths.get("output", "output"))
        self.raw_dir = self._resolve(paths.get("raw", "output/raw"))
        self.music_dir = self._resolve(paths.get("music", "music"))
        self.broll_dir = self._resolve(paths.get("broll", "data/broll"))
        self.transcripts_dir = self._resolve(paths.get("transcripts", "data/transcripts"))
        self.context_dir = self._resolve(paths.get("context", "data/context"))
        self.candidates_dir = self._resolve(paths.get("clip_candidates", "data/clip_candidates"))

        t = _deep_get(self.data, "transcription", {}) or {}
        self.whisper_model = self.env.get("WHISPER_MODEL") or t.get("model", "large-v3")
        self.whisper_device = t.get("device", "auto")
        self.whisper_compute = t.get("compute_type", "auto")
        self.whisper_language = t.get("language") or None

        llm = _deep_get(self.data, "llm", {}) or {}
        self.llm_provider = llm.get("provider", "ollama")
        self.llm_model = self.env.get("OLLAMA_MODEL") or llm.get("model", "llama3:8b")
        self.llm_base_url = self.env.get("OLLAMA_BASE_URL") or llm.get("base_url", "http://localhost:11434")
        self.llm_max_clips = int(llm.get("max_clips", 10))
        self.llm_min_score = float(llm.get("min_score", 0.5))
        self.llm_chunk_words = int(llm.get("chunk_words", 1200))
        self.rules_file = self._resolve(llm.get("rules_file", "data/selection_rules.txt"))
        self.broll_pexels_key = self.env.get("PEXELS_API_KEY", "")
        self.broll_pixabay_key = self.env.get("PIXABAY_API_KEY", "")

        ctx = _deep_get(self.data, "context", {}) or {}
        self.target_platform = ctx.get("target_platform", "youtube_shorts")

        cut = _deep_get(self.data, "cutting", {}) or {}
        self.lead_in = float(cut.get("lead_in_seconds", 0.3))
        self.lead_out = float(cut.get("lead_out_seconds", 0.3))
        raw_h = cut.get("max_raw_height", 1080)
        self.max_raw_height = int(raw_h) if raw_h else None
        enc = cut.get("encode", {}) or {}
        self.video_codec = enc.get("video_codec", "libx264")
        self.audio_codec = enc.get("audio_codec", "aac")
        self.crf = enc.get("crf", 18)
        self.preset = enc.get("preset", "veryfast")

        self.default_template = self.data.get("default_template", "square_captioned")

    def _resolve(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.root / p

    def ensure_dirs(self):
        for d in (self.input_dir, self.output_dir, self.raw_dir, self.music_dir,
                  self.broll_dir, self.transcripts_dir, self.context_dir,
                  self.candidates_dir):
            d.mkdir(parents=True, exist_ok=True)


config = Config()
