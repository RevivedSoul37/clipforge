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
        self.previews_dir = self._resolve(paths.get("previews", "data/previews"))
        self.explorations_dir = self._resolve(paths.get("explorations", "data/style_explorations"))
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
        vad = t.get("vad") if isinstance(t.get("vad"), dict) else {}
        self.vad_enabled = bool(vad.get("enabled", True))
        self.vad_threshold = float(vad.get("threshold", 0.5))
        self.vad_pad_in = float(vad.get("pad_in", 0.12))
        self.vad_pad_out = float(vad.get("pad_out", 0.18))
        self.vad_min_len = float(vad.get("min_len", 12.0))

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

        self.smtp_host = self.env.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(self.env.get("SMTP_PORT") or 587)
        self.smtp_user = self.env.get("SMTP_USER", "")
        self.smtp_pass = self.env.get("SMTP_PASS", "")
        self.transcript_recipient = self.env.get(
            "TRANSCRIPT_RECIPIENT_EMAIL", "r73608925@gmail.com")
        self.transcript_forward = self.env.get(
            "TRANSCRIPT_FORWARD_EMAIL", "syedmunavarahmed444@gmail.com")

        # Highlight selection source: "email" (transcript emailed out; the AI
        # reply is ingested by src/email_highlights.py) or "local" (Ollama).
        # Campaigns can override this per-campaign via settings.local_highlights.
        self.highlight_source = (self.env.get("HIGHLIGHT_SOURCE") or "email").strip().lower()
        self.local_highlights = self.highlight_source != "email"
        self.highlight_reply_sender = self.env.get(
            "HIGHLIGHT_REPLY_SENDER", "syedmunavarahmed444@gmail.com")
        self.imap_host = self.env.get("IMAP_HOST", "imap.gmail.com")
        self.imap_port = int(self.env.get("IMAP_PORT") or 993)
        self.imap_folder = self.env.get("IMAP_FOLDER", "INBOX")

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

        vis = _deep_get(self.data, "vision", {}) or {}
        self.vision_enabled = bool(vis.get("enabled", True))
        self.vision_model = self.env.get("VISION_MODEL") or vis.get("model", "qwen2.5vl:7b")
        self.vision_base_url = (self.env.get("VISION_BASE_URL")
                                or vis.get("base_url") or self.llm_base_url)
        self.vision_frames_per_variant = int(vis.get("frames_per_variant", 2))
        self.vision_temperature = float(vis.get("temperature", 0.1))

        tg = _deep_get(self.data, "telegram", {}) or {}
        self.telegram_enabled = bool(tg.get("enabled", True))
        self.telegram_bot_token = self.env.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = self.env.get("TELEGRAM_CHAT_ID", "")

        exp = _deep_get(self.data, "explore", {}) or {}
        self.explore_max_variants = int(exp.get("max_variants", 10))
        self.explore_preview_resolution = exp.get("preview_resolution", "540x960")
        self.explore_preview_crf = int(exp.get("preview_crf", 28))
        self.explore_preview_preset = exp.get("preview_preset", "ultrafast")

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
        self.previews_dir = camp.previews_dir
        self.explorations_dir = camp.explorations_dir
        self.rules_file = camp.rules_summary_path
        settings = camp.settings()
        if settings.get("default_template"):
            self.default_template = settings["default_template"]
        if camp.has_template():
            self.default_template = str(camp.template_path)
        return camp

    def ensure_dirs(self):
        for d in (self.input_dir, self.output_dir, self.raw_dir, self.music_dir,
                  self.broll_dir, self.transcripts_dir, self.context_dir,
                  self.candidates_dir, self.frames_dir, self.campaigns_dir,
                  self.previews_dir, self.explorations_dir):
            d.mkdir(parents=True, exist_ok=True)


config = Config()

