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
        self.frames_dir = self._resolve(paths.get("frames", "data/frames"))
        self.campaigns_dir = self._resolve(paths.get("campaigns", "data/campaigns"))
        self.active_campaign_id = None

        t = _deep_get(self.data, "transcription", {}) or {}
        self.whisper_model = self.env.get("WHISPER_MODEL") or t.get("model", "large-v3")
        self.whisper_device = t.get("device", "auto")
        self.whisper_compute = t.get("compute_type", "auto")
        self.whisper_language = t.get("language") or None
        # Decoder biasing: a glossary prompt reduces phonetic mishearings
        # (e.g. "gym" -> "jym"). Edit per-channel in config.json.
        self.whisper_initial_prompt = t.get("initial_prompt") or None
        self.whisper_condition_on_previous = bool(t.get("condition_on_previous_text", False))
        self.whisper_low_confidence = float(t.get("low_confidence_threshold", 0.55))

        llm = _deep_get(self.data, "llm", {}) or {}
        self.llm_provider = llm.get("provider", "ollama")
        self.llm_model = self.env.get("OLLAMA_MODEL") or llm.get("model", "llama3:8b")
        self.llm_base_url = self.env.get("OLLAMA_BASE_URL") or llm.get("base_url", "http://localhost:11434")
        self.llm_max_clips = int(llm.get("max_clips", 10))
        self.llm_min_score = float(llm.get("min_score", 0.5))
        self.llm_chunk_words = int(llm.get("chunk_words", 1200))
        self.llm_chunk_overlap_words = int(llm.get("chunk_overlap_words", 250))
        self.llm_num_ctx = int(llm.get("num_ctx", 8192))
        self.llm_temperature = float(llm.get("temperature", 0.2))
        # num_predict 0 = provider default; set >0 to cap output tokens.
        self.llm_num_predict = int(llm.get("num_predict", 0))
        # think: false stops reasoning-style models from returning empty
        # answers (they otherwise spend the whole budget on internal tokens).
        self.llm_think = bool(llm.get("think", False))
        self.clean_transcript = bool(llm.get("clean_transcript", True))
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

    def campaign_root(self, campaign_id: str) -> Path:
        return self.campaigns_dir / campaign_id

    def input_dir_for(self, campaign_id=None) -> Path:
        if campaign_id:
            return self.campaign_root(campaign_id) / "input"
        return self.input_dir

    def output_dir_for(self, campaign_id=None) -> Path:
        if campaign_id:
            return self.campaign_root(campaign_id) / "output"
        return self.output_dir

    def raw_dir_for(self, campaign_id=None) -> Path:
        if campaign_id:
            return self.campaign_root(campaign_id) / "output" / "raw"
        return self.raw_dir

    def activate_campaign(self, campaign_id: str):
        """Remap pipeline dirs onto a campaign folder for this process."""
        from src.campaigns import get_campaign
        camp = get_campaign(campaign_id)
        if camp is None:
            raise FileNotFoundError(f"campaign not found: {campaign_id}")
        camp.ensure_dirs()
        self.active_campaign_id = camp.id
        self.input_dir = camp.input_dir
        self.output_dir = camp.output_dir
        self.raw_dir = camp.raw_dir
        self.transcripts_dir = camp.transcripts_dir
        self.context_dir = camp.context_dir
        self.candidates_dir = camp.candidates_dir
        self.frames_dir = camp.frames_dir
        self.rules_file = camp.rules_summary_path
        if camp.has_template():
            self.default_template = str(camp.template_path)
        return camp

    def ensure_dirs(self):
        for d in (self.input_dir, self.output_dir, self.raw_dir, self.music_dir,
                  self.broll_dir, self.transcripts_dir, self.context_dir,
                  self.candidates_dir, self.frames_dir, self.campaigns_dir):
            d.mkdir(parents=True, exist_ok=True)


config = Config()

