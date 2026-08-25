"""AI Style Explorer — variant generation, preview rendering, vision judging.

Explores edit styles on ONE probe clip per video: cut edge variations x
templates (font/caption/position/crop/music/effects axes) are rendered as
low-res previews, scored by a local vision LLM (qwen2.5vl via Ollama), and
the winning style is persisted for full-quality rollout to all approved clips.

Design guards (per plan):
- Deterministic seeded sampling (seed = hash of video stem), never a full
  cartesian product; 2 "safe" variants derived from golden templates.
- `interpret_brief` maps the free-text style brief to advisory constraints;
  any LLM failure means the brief is simply ignored for generation (it is
  still passed verbatim to the vision judge).
- Judging failures degrade per-variant; if ALL variants fail to score,
  exploration falls back to the current default template — exploring must
  never block export.
"""
import base64
import hashlib
import json
import random
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from src.config import config
from src.llm_client import call_ollama
from src import apply_template as at
from src import cut_clips

TEMPLATE_DIR = config.root / "templates"

FONT_REGISTRY = [
    {"family": "Anton", "file": "Anton-Regular.ttf", "weight_hint": "display"},
    {"family": "Archivo Black", "file": "ArchivoBlack-Regular.ttf", "weight_hint": "display"},
    {"family": "Bangers", "file": "Bangers-Regular.ttf", "weight_hint": "display"},
    {"family": "Barlow Condensed", "file": "BarlowCondensed-Bold.ttf", "weight_hint": "bold"},
    {"family": "Saira Condensed", "file": "SairaCondensed-Bold.ttf", "weight_hint": "bold"},
    {"family": "Kanit", "file": "Kanit-Bold.ttf", "weight_hint": "bold"},
    {"family": "Lato", "file": "Lato-Bold.ttf", "weight_hint": "bold"},
    {"family": "Poppins-Bold", "file": "Poppins-Bold.ttf", "weight_hint": "bold"},
    {"family": "Bebas Neue", "file": "BebasNeue-Regular.ttf", "weight_hint": "display"},
]

HOOK_COMBOS = [
    {"font": "Bebas Neue", "color": "#F1EFD5"},
    {"font": "Anton", "color": "#FFFFFF"},
    {"font": "Archivo Black", "color": "#FFE14D"},
]

CAPTION_STYLES = {
    "white": {"color": "#FFFFFF", "outline_color": "#000000", "outline_width": 4,
              "gradient": {"enabled": False}, "highlight_keyword": {"enabled": False}},
    "cream": {"color": "#FAF3D0", "outline_color": "#000000", "outline_width": 4,
              "gradient": {"enabled": False}, "highlight_keyword": {"enabled": False}},
    "yellow_gradient": {"color": "#F7E94A", "outline_color": "#000000", "outline_width": 5,
                        "gradient": {"enabled": True, "top": "#FFF35C", "bottom": "#FF9A3D"},
                        "highlight_keyword": {"enabled": False}},
    "keyword_red": {"color": "#FFFFFF", "outline_color": "#000000", "outline_width": 4,
                    "gradient": {"enabled": False},
                    "highlight_keyword": {"enabled": True, "color": "#E00000"}},
}

GRADES = ("none", "warm", "cool", "punchy", "bright")

RED_HEXES = ("#E00000", "#FC0100", "#FF2222", "#D90000")

BRIEF_SYSTEM = (
    "You translate a short creator style brief into JSON constraints for a "
    "video edit-style generator.\n"
    "Return ONLY JSON with exactly these keys:\n"
    " banned_colors: array of keywords from {red,yellow,green,blue,white,"
    "black,orange} the brief forbids (empty if none).\n"
    " prefer: array of any of {warm_grades,cool_grades,punchy_grades,"
    "bright_grades,dark_grades,vignette,music,no_music,big_captions,"
    "small_captions} the brief implies.\n"
    " fonts: array of font family names explicitly requested (empty if none).\n"
    " notes: one short sentence paraphrasing the brief for a human reviewer.\n"
    "Be conservative: only include what the brief clearly states."
)

JUDGE_SYSTEM = (
    "You are a professional short-form video editor judging ONE caption style "
    "variant. You see frames from a single 9:16 preview (burned-in hook at "
    "the top, captions over or beside the footage, black bands where the "
    "clip is letterboxed or square-banded).\n"
    "Score every criterion 0-10 as an integer or one-decimal number, then a "
    "total:\n"
    "- legibility: are hook and caption text crisp, readable, not cut off?\n"
    "- contrast: does text separate clearly from the background behind it?\n"
    "- style: composition, font appropriateness, professional feel?\n"
    "- brief_fit: does it follow the style brief? (10 if no brief given.)\n"
    "Return ONLY JSON: {\"scores\": {\"legibility\": n, \"contrast\": n, "
    "\"style\": n, \"brief_fit\": n}, \"total\": n, \"verdict\": \"one short "
    "sentence explaining the score and any brief violations\"} where total is "
    "the mean of the four scores rounded to one decimal."
)


def _seed_from_stem(stem):
    return int(hashlib.md5(stem.encode("utf-8")).hexdigest()[:12], 16)


def _extract_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(content[start:end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                pass
    return None


def available_fonts():
    """FONT_REGISTRY entries whose TTF is actually on disk."""
    disk = {p.name for p in at.FONT_DIR.glob("*.ttf")}
    return [f for f in FONT_REGISTRY if f["file"] in disk]


def check_vision_ready():
    """Return None when the vision stack is ready, else a human-readable
    error describing what to fix (missing model / Ollama down / disabled)."""
    if not config.vision_enabled:
        return ("Vision judging is disabled (vision.enabled=false in "
                "config.json). Enable it or the style explorer cannot run.")
    url = config.vision_base_url.rstrip("/")
    tag, _, _ = config.vision_model.partition(":")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return (f"Ollama is not reachable at {url}. Start Ollama, then run "
                f"`ollama pull {config.vision_model}`.")
    models = {m.get("name", "") for m in data.get("models", [])}
    if config.vision_model not in models and not any(
            m.startswith(tag + ":") for m in models):
        return (f"Vision model '{config.vision_model}' is not pulled. "
                f"Run: ollama pull {config.vision_model} "
                f"(or qwen2.5vl:3b on low VRAM, then set vision.model).")
    return None


def interpret_brief(brief):
    """Map a free-text style brief to advisory generator constraints.

    Any failure degrades to {} — the brief is still passed verbatim to the
    vision judge, so exploration never depends on the text LLM."""
    brief = (brief or "").strip()
    if not brief:
        return {}
    messages = [
        {"role": "system", "content": BRIEF_SYSTEM},
        {"role": "user", "content": brief[:2000]},
    ]
    for attempt in (1, 2):
        try:
            parsed = _extract_json(call_ollama(messages, temperature=0.1))
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] brief interpretation attempt {attempt} failed: {exc}")
            parsed = None
        if isinstance(parsed, dict):
            out = {
                "banned_colors": [str(c).strip().lower() for c in parsed.get("banned_colors", [])
                                   if str(c).strip()],
                "prefer": [str(p).strip().lower() for p in parsed.get("prefer", [])
                           if str(p).strip()],
                "fonts": [str(f).strip() for f in parsed.get("fonts", [])
                          if str(f).strip()],
                "notes": str(parsed.get("notes", "")).strip(),
            }
            print(f"[explore] brief -> banned={out['banned_colors']} "
                  f"prefer={out['prefer']} fonts={out['fonts']}")
            return out
        if attempt == 1:
            messages.append({"role": "user", "content":
                             "Return ONLY the JSON object described, no prose."})
    print("[explore] brief interpretation failed twice; ignoring brief for "
          "variant generation (judge still sees it)")
    return {}


def golden_templates():
    """Templates flagged golden:true, best-effort loaded."""
    out = []
    for p in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            tpl = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if tpl.get("golden") and tpl.get("output", {}).get("resolution"):
            out.append(tpl)
    return out


def generate_variants(video_stem, max_variants=None, constraints=None):
    """Deterministically sample template variants for the probe.

    Returns a list of (variant_name, template_dict, summary_str). The first
    two variants are always safe copies of the golden templates (with the
    effects axis applied), so exploration never loses the known-good looks."""
    constraints = constraints or {}
    max_variants = int(max_variants or config.explore_max_variants)
    rng = random.Random(_seed_from_stem(video_stem) + max_variants)
    fonts = available_fonts()
    if not fonts:
        raise RuntimeError(f"No bundled fonts found in {at.FONT_DIR}")
    caption_font_names = [f["family"] for f in fonts]
    banned_colors = set(constraints.get("banned_colors", []))
    prefer = set(constraints.get("prefer", []))
    req_fonts = [f for f in constraints.get("fonts", [])
                 if f in caption_font_names]

    grade_pool = list(GRADES)
    if "warm_grades" in prefer:
        grade_pool = ["warm", "punchy", "none"]
    elif "cool_grades" in prefer:
        grade_pool = ["cool", "none", "punchy"]
    elif "bright_grades" in prefer:
        grade_pool = ["bright", "none", "warm"]
    elif "dark_grades" in prefer:
        grade_pool = ["cool", "none", "bright"]
    if "no_music" in prefer:
        music_choices = [False, False, True]
    elif "music" in prefer:
        music_choices = [True, True, False]
    else:
        music_choices = [True, True, False]

    sizes = [64, 80, 96]
    if "big_captions" in prefer:
        sizes = [80, 96]
    elif "small_captions" in prefer:
        sizes = [64, 80]

    variants = []

    def add(name, tpl, summary):
        tpl = json.loads(json.dumps(tpl))  # deep copy
        tpl["name"] = name
        variants.append((name, tpl, summary))

    for i, base in enumerate(golden_templates()[:2]):
        add(f"{base['name']}_base{i + 1}", base,
            f"golden baseline '{base['name']}' (crop="
            f"{base.get('crop', {}).get('mode')} grade=none)")
    n_seed = sum(1 for _ in variants)

    hook_pool = HOOK_COMBOS
    if "red" in banned_colors:
        hook_pool = [h for h in HOOK_COMBOS
                     if h["color"] not in RED_HEXES] or HOOK_COMBOS
    style_pool = [s for s in CAPTION_STYLES
                  if not ("red" in banned_colors and s == "keyword_red")]
    style_pool = style_pool or list(CAPTION_STYLES)
    fonts_pool = req_fonts or caption_font_names

    attempts = 0
    seen = {name for name, _, _ in variants}
    while len(variants) < max_variants and attempts < max_variants * 25:
        attempts += 1
        crop = rng.choice(["square_band", "letterbox"])
        font = rng.choice(fonts_pool)
        style_key = rng.choice(style_pool)
        style = CAPTION_STYLES[style_key]
        if "red" in banned_colors and style.get("highlight_keyword", {}).get("color") in RED_HEXES:
            continue
        # position is now a REGION preference (top/middle/bottom band), not an
        # absolute margin_v. The layout engine resolves actual coordinates from
        # this normalized preference + safe zones + collisions — both for the
        # low-res preview and the full-quality export, identically.
        position = rng.choice(["bottom", "center"])
        size = rng.choice(sizes)
        hook = rng.choice(hook_pool)
        grade = rng.choice(grade_pool)
        vignette = "vignette" in prefer and rng.random() < 0.6
        music = rng.choice(music_choices)

        cap_pos = "middle_center" if position == "center" else "bottom_center"
        anchor = "band" if crop == "letterbox" else "frame"
        tpl = {
            "name": "",
            "output": {"aspect_ratio": "9:16", "resolution": "1080x1920"},
            "crop": {"mode": crop, "background": "#000000",
                     "follow_speaker": crop == "square_band"},
            "effects": {"grade": grade, "vignette": 0.5 if vignette else 0.0},
            "hook": {"enabled": True, "font": hook["font"], "size": 96,
                     "color": hook["color"], "position": "top",
                     "anchor": anchor},
            "captions": {
                "enabled": True, "font": font, "size": size,
                "position": cap_pos, "anchor": anchor,
                "max_words": 4, "max_lines": 3,
            },
            "music": {"enabled": music, "track": "", "volume": 0.12},
            "broll": {"enabled": False, "mode": "cutaway", "pip_scale": 0.6},
            "intro": {"enabled": False},
            "outro": {"enabled": False},
            "watermark": {"enabled": False},
        }
        # caption color/gradient/keyword styling from the style axis
        tpl["captions"].update({k: v for k, v in style.items() if k != "color"
                                or not tpl["captions"].get("gradient", {}).get("enabled")})
        tpl["captions"]["color"] = style["color"]

        name = f"v{len(variants) - n_seed + 1:02d}_{crop}_{style_key}"
        if name in seen:
            name = f"{name}_{len(variants)}"
        seen.add(name)
        summary = (f"crop={crop} font={font} style={style_key} pos={position} "
                   f"size={size} hook={hook['font']}/{hook['color']} "
                   f"grade={grade}{' +vignette' if vignette else ''} "
                   f"music={'on' if music else 'off'}")
        add(name, tpl, summary)

    return variants[:max_variants]


def cut_probe_variants(video, clip, transcript_path, out_dir):
    """Cut the probe clip three ways: default padding, tight VAD trim, and an
    extended hook lead-in. Returns [{edge, path, start, end}, ...] each usable
    as (raw_path, start, end) for apply_template."""
    from src.clean_transcript import best_transcript_path
    from src import audio_processor as ap

    video = Path(video)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = float(clip["start"]), float(clip["end"])
    edges = []

    path, s, e = cut_clips.cut_one(video, start, end,
                                   out_dir / "probe_e0_default.mp4")
    edges.append({"edge": "default", "path": path, "start": s, "end": e})

    t_start, t_end = start, end
    try:
        tp = Path(transcript_path) if transcript_path else best_transcript_path(video)
        data = json.loads(tp.read_text(encoding="utf-8"))
        spans = ap.spans_from_words(data.get("segments", []))
        t_start, t_end = ap.trim_silence(start, end, spans,
                                         pad_in=0.05, pad_out=0.05, min_len=3.0)
    except Exception as exc:  # noqa: BLE001
        print(f"[explore] tight trim unavailable ({exc}); using default range")
    path, s, e = cut_clips.cut_one(video, t_start, t_end,
                                   out_dir / "probe_e1_tight.mp4",
                                   lead_in=0.0, lead_out=0.0)
    edges.append({"edge": "tight", "path": path, "start": s, "end": e})

    path, s, e = cut_clips.cut_one(video, max(0.0, start - 0.4), end,
                                   out_dir / "probe_e2_lead.mp4",
                                   lead_in=0.05, lead_out=0.05)
    edges.append({"edge": "extended_lead", "path": path, "start": s, "end": e})
    return edges


def _probe_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return float(proc.stdout.strip() or 0.0)


def render_variant(variant_index, template, probe, preview_dir, hook_text):
    """Render one variant as a LOW-RES preview (resolution override + fast
    encode) into data/previews/<stem>/variant_NN.mp4."""
    preview = {
        "resolution": config.explore_preview_resolution,
        "crf": config.explore_preview_crf,
        "preset": config.explore_preview_preset,
    }
    out = at.apply_template(
        probe["path"], probe["transcript"], probe["start"], probe["end"],
        template=template, output_dir=preview_dir,
        hook_text=hook_text, out_name=f"variant_{variant_index:02d}.mp4",
        preview=preview)
    return out


def extract_frames(video_path, out_dir, n=None, percents=(0.3, 0.7), prefix="frame"):
    """Extract judgement frames at fractional durations via -ss -frames:v 1."""
    n = n or config.vision_frames_per_variant
    percents = list(percents)[:n] if n <= len(percents) \
        else [i / (n + 1) for i in range(1, n + 1)]
    dur = _probe_duration(video_path)
    if dur <= 0:
        return []
    out = []
    for i, pct in enumerate(percents):
        t = max(0.05, min(dur - 0.05, dur * pct))
        dest = Path(out_dir) / f"{prefix}_{i}.jpg"
        cmd = ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(video_path),
               "-frames:v", "1", "-q:v", "3", str(dest)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size:
            out.append(dest)
    return out


def judge_variant(frames, summary, brief):
    """Score one variant with the vision LLM. Returns the parsed dict with
    scores/total/verdict, or None on failure (variant then gets excluded)."""
    if not frames:
        return None
    images = [base64.b64encode(p.read_bytes()).decode("ascii") for p in frames]
    brief_txt = (brief or "").strip()
    user = ("STYLE BRIEF: " + brief_txt + "\n" if brief_txt else "No style brief.\n")
    user += ("VARIANT DESCRIPTION: " + summary + "\n"
             "The attached frames are from this variant's low-resolution "
             "preview (same style, smaller pixels). Score it per the rubric.")
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]
    parsed = None
    for attempt in (1, 2):
        try:
            content = call_ollama(messages, model=config.vision_model,
                                  base_url=config.vision_base_url,
                                  temperature=config.vision_temperature,
                                  images=images,
                                  timeout=300, num_ctx=8192)
            parsed = _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] judge attempt {attempt} error: {exc}")
            parsed = None
        if parsed and "scores" in parsed and "total" in parsed:
            try:
                parsed["total"] = float(parsed["total"])
                parsed["scores"] = {k: float(v)
                                    for k, v in parsed["scores"].items()}
                parsed["verdict"] = str(parsed.get("verdict", "")).strip()
                break
            except (TypeError, ValueError):
                parsed = None
        if attempt == 1:
            messages.append({"role": "user", "content":
                             "Your last answer was not valid JSON matching "
                             "the contract. Reply with ONLY the JSON object."})
    return parsed if parsed else None


def exploration_path(stem):
    return config.explorations_dir / f"{stem}_exploration.json"


def report_path_for(stem):
    return exploration_path(stem)


def winner_template_path(stem):
    return TEMPLATE_DIR / f"{stem}_winner.json"


def save_report(data):
    path = exploration_path(data["video_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_report(stem):
    path = exploration_path(stem)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def roll_out_winner(video_stem, winner_template, hook_texts, progress=None):
    """Render every approved clip at full quality with the winning style.

    `hook_texts` maps raw-clip filename -> hook text (or None). Uses the raw
    clips already in config.raw_dir (normal export cut must have run)."""
    manifest = cut_clips.read_manifest(config.raw_dir, video_stem)
    if not manifest or not manifest.get("clips"):
        raise FileNotFoundError(
            f"No cut clips for '{video_stem}' in {config.raw_dir}; run export/cut first.")
    from src.clean_transcript import best_transcript_path
    tp = best_transcript_path(video_stem)
    if not tp.exists():
        raise FileNotFoundError(f"Transcript not found for '{video_stem}': {tp}")
    results = []
    total = max(1, len(manifest["clips"]))
    for i, c in enumerate(manifest["clips"]):
        raw = Path(c["path"])
        if not raw.exists():
            print(f"[explore] raw clip missing, skipping: {raw}")
            continue
        out = at.apply_template(raw, tp, c["start"], c["end"],
                                template=winner_template,
                                hook_text=hook_texts.get(raw.name))
        results.append(out)
        print(f"[explore] rollout {i + 1}/{total}: {raw.name} -> {out.name}")
        if progress:
            progress((i + 1) / total)
    return results
