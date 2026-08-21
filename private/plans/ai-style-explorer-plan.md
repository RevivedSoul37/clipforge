# AI Style Explorer — plan

Add an AI layer after transcript/highlight selection that **explores edit styles** on one probe clip (cut edge variations × templates × music × color/lighting effects × fonts × caption styles/positions), **auto-selects the winner with a local vision LLM (Qwen2.5-VL)**, and rolls the winning style out to all approved clips — with an optional human instruction layer ("style brief") injected before generation.

## Resolved decisions

| Decision | Choice |
|---|---|
| Who picks the winner | **Vision LLM auto-selects** — Qwen2.5-VL via Ollama (`qwen2.5vl:7b` default, configurable; fall back to 3b on low VRAM) |
| Granularity | **Per video, one probe clip**: highest-scored approved/candidate clip; all approved clips render in the winning style at full quality |
| Instruction injection | **Style brief** (free text) per campaign — editable in web UI + `--brief` CLI flag; interpreted by the text LLM into variant constraints, also passed to the vision judge |
| Cut variations | **Clip-level only**: probe-clip choice, edge padding (tight VAD-trimmed vs default vs extended hook lead-in). No intra-clip re-editing |
| Color/lighting | **Programmatic effects block** (eq/curves/vignette presets) added to template schema; winning template is saveable |
| Fonts | **Bundle ~6 new OFL fonts** into `assets/fonts/` (dynamic enumeration already exists there) |
| Trigger | **New `explore-style` CLI command + full web integration** (brief textarea, explore action, progress, preview grid, save-to-campaign) |

## Architecture overview

```
analyze (existing) → candidates.json
        ↓
explore-style (NEW):
  1. pick probe clip (highest score, or --probe N)
  2. cut probe edge variations (output/raw probe files)
  3. brief → LLM interprets into axis constraints (fallback: ignore brief)
  4. generate N seeded template variants (default 10)
  5. render each variant as LOW-RES preview (540x960, crf 28, ultrafast)
  6. extract 2 frames per preview → vision LLM scores each (JSON rubric)
  7. winner → templates/<stem>_winner.json + data/style_explorations/<stem>.json
        ↓
export/pipeline renders ALL approved clips with winning template (or explicit --template)
```

Key files today: `src/apply_template.py` (render engine), `src/cut_clips.py`, `src/select_highlights.py`, `src/llm_client.py` (text-only Ollama HTTP), `src/config.py`, `main.py`, `server.py`, `web/app.js`, `templates/*.json`.

## Ordered tasks

### 1. Config additions (`src/config.py`, `config.json`)
- New `"vision"` block: `{"enabled": true, "model": "qwen2.5vl:7b", "base_url": same ollama url, "frames_per_variant": 2, "temperature": 0.1}` parsed as `config.vision_*` attrs.
- New `"explore"` block: `{"max_variants": 10, "preview_resolution": "540x960", "preview_crf": 28, "preview_preset": "ultrafast"}` parsed similarly.
- `.env.example`: `VISION_MODEL=` override note.

### 2. Template schema: effects block (`templates/`, `src/apply_template.py`)
- Add optional `"effects"` block to template JSON:
  ```json
  "effects": {"grade": "none|warm|cool|punchy|bright", "vignette": 0.0}
  ```
- In `apply_template._video_filters`, append grade filters BEFORE crop/scale chain:
  - `warm`: `eq=saturation=1.08,colorbalance=rs=0.04:gs=0.01:bs=-0.05`
  - `cool`: `colorbalance=rs=-0.04:bs=0.04, eq=saturation=0.98`
  - `punchy`: `eq=contrast=1.12:saturation=1.15, curves=preset=cross_process` (or manual curve; keep values conservative)
  - `bright`: `eq=brightness=0.06:saturation=1.05`
  - `vignette` > 0: `vignette=angle=PI/5` variant
- Update existing templates with `"effects": {"grade": "none"}` (non-breaking: treat missing as none).

### 3. Font bundle (`assets/fonts/`)
- Add ~6 OFL-licensed display fonts, downloaded from Google Fonts (github raw): **Anton, Archivo Black, Montserrat-Bold, Oswald-Bold, Bangers, Inter-Bold** (final names may vary; record family name + exact TTF filename).
- Naming convention must match `_parse_font` expectations (e.g. `Montserrat-Bold.ttf`, family `Montserrat`).
- Add a `FONT_REGISTRY` list to `src/style_explorer.py`: `{family: "Anton", file: "Anton-Regular.ttf", weight_hint: "display"}` — generator only picks fonts present on disk (glob `FONT_DIR/*.ttf`, same as apply_template's fontsdir copy).
- Add `assets/fonts/OFL-licenses.md` noting sources.

### 4. Variant generator (`src/style_explorer.py`, new)
- Axes:
  - `crop`: `square_band` (follow speaker) | `letterbox`
  - `captions.font`: enumerated bundled fonts
  - `captions.style`: solid white/cream | yellow gradient | keyword-highlight-red
  - `captions.position`: safe-zone `margin_v 420-470` (bottom) | center (`position middle_center`)
  - `captions.size`: 64 / 80 / 96 (clamped for 1080 wide)
  - `hook.font`/`hook.color`: 2-3 combos
  - `effects.grade`: none + 2 presets
  - `music`: on (rotate track via existing `_pick_music`) | off
- Deterministic seeded sampling (seed = video stem hash) to `max_variants` (avoid full cartesian product; include 2 "safe" variants derived from existing golden templates).
- `interpret_brief(brief)`: call text LLM (`call_ollama`, JSON mode) to map brief → constraints `{"banned_colors": [], "prefer": ["warm_grades"], "fonts": [...], "notes": "..."}`; retry once, on failure return `{}` (brief still passed verbatim to judge).
- Generate valid template dicts (must satisfy `apply_template` schema incl. `output/crop/hook/captions/music/broll/intro/outro/watermark` keys).

### 5. Preview rendering
- Extend `apply_template.apply_template(..., preview=None)` — when `preview` is a dict (`resolution`, `crf`, `preset`), override output resolution + encode settings; output to `data/previews/<stem>/` named `variant_<i>.mp4`.
- Probe cuts: use `cut_clips.cut_one` with edge variants: (a) default padding, (b) tight (`trim_silence` with pad_in 0.05/pad_out 0.05 via `src/audio_processor`), (c) extended lead-in (+0.4s). Each edge variant pairs with a subset of template variants.

### 6. Vision judge (`src/style_explorer.py`)
- Extract `frames_per_variant` frames per preview at 30%/70% of duration via ffmpeg (`-ss -frames:v 1`) at preview resolution.
- One Ollama `/api/chat` call per variant: message `images` = base64 frames, prompt = rubric (hook legibility, caption legibility/contrast vs background, composition, brief compliance, professional feel), brief text, variant summary (what it contains). Output contract JSON: `{"scores": {"legibility": 0-10, "contrast": ..., "style": ..., "brief_fit": ...}, "total": 0-10, "verdict": "..."}` — reuse `_extract_json` style parsing + one retry prompt.
- Winner = max total; on parse failure of all retries → exclude variant. If ALL fail → fall back to current `default_template` with a warning.
- HTTP helper: new `call_ollama(..., images=None)` optional param in `src/llm_client.py` (adds `"images": [...]` to payload; works for both text and vision models).

### 7. Report + winner persistence
- `data/style_explorations/<stem>_exploration.json`: brief, probe clip info, per-variant `{file, summary, frames, scores, verdict}`, winner name, timestamp.
- Winner template → `templates/<stem>_winner.json` (`name` field set).
- `explore-style` prints summary lines (`[explore] 7/10 previews scored, winner: ... (total 8.2)`).

### 8. CLI (`main.py`)
- `explore-style <video>` command: `--brief "text"` (overrides campaign brief), `--variants N`, `--probe <clip_index>`, `--auto` (use score-threshold candidates, not just approved), `--emit-progress` wired via existing `progress.emit` stages (cut 0-15, variants 15-25, render 25-80, judge 80-100).
- `export`/`pipeline`: when no explicit `--template` and an exploration winner exists for the video stem (`templates/<stem>_winner.json`), use it. Log which template was auto-chosen.

### 9. Server + web UI (`server.py`, `web/app.js`, `web/index.html`)
- Endpoints:
  - `POST /api/run` mode `"explore-style"` (add to allowed modes; pass `brief`, `variants`).
  - `GET /api/exploration/<video_id>` → report JSON + preview file paths.
  - Style brief storage: register `"style_brief"` in `SETTINGS_KEYS` + `normalize_settings()` in `src/campaigns.py:31-128` (whitelist pattern), then save via the existing campaign-settings save endpoint + UI flow. Fallback for no-campaign runs: keep brief as `--brief` CLI arg only.
  - Static route serving `data/previews/` (or reuse existing output-serving route pattern).
  - `POST /api/exploration/<video_id>/save-to-campaign` → copies winner JSON to campaign template (`camp.template_path`).
- UI (Export page area):
  - Style-brief textarea (loads/saves per campaign).
  - "Explore styles" button (disabled until candidates exist) → starts run, shows progress via existing `@@PROGRESS@@` poller.
  - Results grid: variant preview `<video>` (muted, small) + score badge + verdict line; winner highlighted + "Save as campaign style" button; re-export hint.

### 10. Docs
- README: new "Style Explorer" section (pull `qwen2.5vl:7b` first, what exploration costs, brief examples).

## Risks & mitigations

- **Vision model not pulled** → fail fast with message `ollama pull qwen2.5vl:7b`; check via `GET /api/tags` before starting.
- **Render cost** → bounded: `max_variants` × low-res preview of ONE clip; full-quality renders happen once with the winner.
- **Score parse failures** → per-variant retry + exclusion; all-fail fallback to default template (exploration never blocks export).
- **ASS font mismatch** (family name vs file) → FONT_REGISTRY + render smoke test in validation; apply_template already copies fonts dir.
- **`eq`/`curves` filter availability** → standard in ffmpeg builds used; validate in task 2 with one manual preview render before generator work.
- **Brief injection hallucination** → constraints are advisory filters only; combinatorial generator is the source of truth (LLM failure = brief ignored, no crash).

## Validation

1. `python main.py explore-style sample --variants 4 --auto` on an existing sample video → previews exist in `data/previews/`, exploration JSON written, winner template in `templates/`.
2. Winner template renders at full quality: `python main.py export sample` → clips match preview style; captions legible with a non-bundled-before font.
3. Effects block: manually render `punchy` + `vignette` on one clip, visually confirm vs `none`.
4. Brief: `--brief "no red, warm cinematic look"` → judge verdicts reference brief; banned colors absent from variants.
5. Failure path: stop Ollama → explore-style aborts with a clear error, no partial state.
6. Web: brief save/load, explore button progress, preview grid renders, save-to-campaign switches `default_template`.

## Open questions (deferred to implementation)

- Exact final font list (download availability at impl time; OFL confirmation per file).
- Whether center-caption position needs special anchor handling in `_build_ass` for banded layouts (verify during task 4).
- Preview frame sampling percentages (30/70 assumed; tune if judge complains about frames without captions).
