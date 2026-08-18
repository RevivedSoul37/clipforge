# Memory.md — ClipForge (project memory)

## Status
Full v1 project scaffolded and **verified end-to-end** on 2026-08-17 with a real
narrated video (`input/sample3.mp4`). All stages ran successfully: transcribe →
context → select (gemma4:12b) → cut → render (vertical captioned). GPU works.

A **web UI** was added on 2026-08-18: a Starlette backend (`server.py`) + vanilla
frontend (`web/`), launched with `start_clipforge.bat`. Replaces the old Streamlit
review UI as the primary interface.

**Audit + fixes on 2026-08-18** (details under "Fixes log" below): raw-clip size
capping, cut manifest, safe path handling, process-tree cancel, efficient log
polling, preview endpoint + button, CLI stem resolution, docs sync.

## Environment (verified)
- Dedicated venv: `.venv` (Python 3.12) — use `.venv\Scripts\python.exe`.
- ffmpeg 8.1 (gyan.dev full build) + ffprobe — installed.
- Ollama 0.32.14 installed. Chosen model: `gemma4:12b`.
- faster-whisper 1.2.1, ctranslate2 4.8.1, streamlit 1.61.1 in `.venv`.
- Web server deps (starlette, uvicorn, python-multipart) installed and pinned in
  `requirements.txt` as of 2026-08-18.
- CTranslate2 sees 1 CUDA device.

## CUDA fix (important)
`ctranslate2` on Windows could not load `cublas64_12.dll` at first. Fixed by:
1. `pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12` into `.venv`.
2. Copying `cublas64_12.dll`, `cublasLt64_12.dll`, `cudart64_12.dll` from
   `.venv\Lib\site-packages\nvidia\*\bin` into `.venv\Lib\site-packages\ctranslate2\`
   (its `__init__.py` loads DLLs from its own dir). Also `transcribe.py` calls
   `_register_cuda_dll_dirs()` to add the nvidia bin dirs to the DLL search path.
   A venv recreate will wipe the copies — re-run this if that happens.

## Decisions made
- LLM calls go through Ollama's HTTP API (`/api/chat`) via stdlib `urllib` — no
  `ollama`/`requests` Python dependency needed.
- `.env` parsed manually (no `python-dotenv`) to keep dependencies minimal.
- Transcripts are chunked by ~1200 words (`config.json → llm.chunk_words`) before the
  LLM call so long videos don't overflow the model context.
- Raw cuts are re-encoded (fast `-ss` input seek) and record their **actual padded**
  start/end, so `apply_template` can shift word timestamps to the raw clip timeline
  exactly (padding is added at cut time and reflected in metadata).
- Captions are rendered via a generated ASS file + ffmpeg `subtitles` filter (placed in
  a temp dir and referenced by relative name to dodge Windows path-escaping).
- Default LLM model: `gemma4:12b` (user-selected). Default Whisper model: `large-v3`
  (user-selected). Default template: `vertical_captioned`.

## Fixes log (2026-08-18 audit)
- **Template consolidation (user request):** removed `vertical_captioned`,
  `square_clean`, `vertical_hook`. Single template `square_captioned` is now the
  only one and the default: full-bleed 1:1, Bebas Neue hook on top, gradient word
  captions at the bottom (per-word color lerp between `captions.gradient.top` and
  `.bottom`, since libass has no native text-gradient tag). Fonts bundled in
  `assets/fonts/` (BebasNeue-Regular.ttf, Poppins-Bold.ttf) and shipped to libass
  via `subtitles=...:fontsdir=.` (fonts copied to the temp dir to avoid Windows
  path/colon issues in the filter string).
- **ASS escaping bug:** `_build_ass` escaped the whole dialogue line *after*
  assembling `{\c...}` override tags, so tags rendered as literal text. Now words
  are escaped individually before tag assembly.
- **Reference-style template** `templates/vertical_hook.json` (new default):
  letterboxed 16:9 on black, cream hook title on top, bold yellow word captions
  centered over the video — matches the style the user showed (whop screen-rec).
  `apply_template` gained: `crop.mode=letterbox` (scale+pad), `hook` ASS style fed
  from each candidate's `hook` field, caption `position` (center/bottom/top),
  `max_words` per caption line, and ASS text escaping.
- **AI selection rules UI:** sidebar panel "AI selection rules" (textarea + Save)
  writes `data/selection_rules.txt`; `select_highlights` appends it to the system
  prompt (`#` comment lines ignored). Verified live: rules shifted picks and the
  model returned hooks. Server endpoints `GET/POST /api/rules`.
- **LLM hooks:** `select_highlights` now asks the model for a `hook` line per clip;
  stored in candidates, editable per-clip in the review UI ("Hook title" field),
  passed through `main.py` render/export/pipeline/batch into `apply_template`.
- **Old-style caption note:** captions were never broken — the C1032 exports used
  `square_clean` (no captions by design). Default template is now `vertical_hook`.
- **Raw clip size bug:** 4K sources produced multi-hundred-MB raw intermediates
  (C1032 clip_01 was 594 MB). `cut_clips` now downscales sources above
  `cutting.max_raw_height` (default 1080, set in config.json) at cut time using
  `scale=-2:<height>`. C1032 clips dropped to 53 MB / 33 MB with no quality loss
  downstream (templates render at 1080p anyway).
- **Render caption drift:** `cmd_render` re-derived padded ranges from the
  candidates file instead of using what `cut` actually produced. `cut_clips` now
  writes `output/raw/<video>_manifest.json` with the actual padded start/end per
  cut; `cmd_render` prefers the manifest (falls back to recomputed ranges only if
  the manifest is missing).
- **CLI stem resolution:** `python main.py analyze sample3` now works (bare stem
  resolves inside `input/`); previously only filenames/paths worked (server had
  its own resolver).
- **Cancel orphans:** cancel now kills the process tree (Windows: `taskkill /F /T`;
  POSIX: killpg on `start_new_session`) so ffmpeg no longer survives.
- **Path traversal:** `/api/media` resolver uses `resolve()` + `relative_to(ROOT)`
  instead of a `startswith` string check; traversal attempts 404.
- **Polling weight:** `/api/run/{id}` now accepts `?since=<log_index>` and returns
  only new log lines with absolute indices (`log_index`, `log_dropped`, `log_total`);
  the frontend tracks `shownLogs` instead of re-downloading up to 4000 lines / poll.
- **Error surface:** failed runs now surface the most relevant recent log line
  instead of blindly `logs[-1]`.
- **Empty `-vf` crash:** `apply_template` omits `-vf` when no filters apply.
- **Preview:** new `POST /api/preview` + "▶ Preview" button on review cards cuts an
  exact (no-padding) preview into `output/raw/preview_<stem>_<start>-<end>.mp4`
  (old previews of the same video are deleted first). Preview survives
  approve/reject re-renders via an in-memory url cache.
- **Misc:** wrong `[select] chunk i/N` total fixed; `_media_list` no longer matches
  other videos' clips by prefix; open-folder button calls new
  `POST /api/open-folder` (explorer/xdg/open); `cmd_context` emits progress.
- Docs synced: Architecture.md now matches reality (web UI, manifest, CLI stems);
  Design.md covers the web UI; ClipForge_Full_Blueprint.md kept as the original
  planning snapshot (do not treat it as current).

## Still open / next steps
- Streamlit review UI (`python main.py review`) is legacy now; the web UI replaced it.
- The first two test videos ("Ai feature.mp4", desktop screen recordings) had **no
  speech** (Whisper returned 0 segments) — they're music/demo-only, so there's nothing
  to clip. Use `input/sample3.mp4` (a 36-min narrated tech-news video) as the demo.
- Font: captions reference "Montserrat-Bold"; if not installed, libass silently falls
  back to a default sans. Install the font or add a `font_path` to the template for a
  specific look.
- `intro`/`outro`/`watermark` are schema-only in v1 — `apply_template` raises
  `NotImplementedError` if enabled.

## Web UI (added 2026-08-18)
- `server.py` — Starlette + uvicorn backend. Runs the pipeline as a subprocess
  (`python -u main.py --emit-progress <mode> <video> ...`), captures stdout, and
  mirrors it to (a) the terminal and (b) a per-run `logs` list polled by the frontend.
- Progress: `main.py --emit-progress` prints `@@PROGRESS@@ {"percent":..,"stage":..,"message":..}`
  lines. `src/progress.py` is the emitter; transcribe/select/cut take a `progress`
  callback. Stages: transcribe → context → select → cut → render.
- API: `GET /api/state`, `GET /api/video/{id}`, `POST /api/run`, `GET /api/run/{id}`,
  `POST /api/run/{id}/cancel` (terminates the subprocess), `POST /api/candidates`
  (save review decisions), `POST /api/upload` (streams to disk, auto-renames
  duplicates, rejects non-video types), `GET /api/media`.
- `POST /api/run` builds subprocess args **per-mode** — only flags a command actually
  accepts are passed (e.g. `analyze` gets `--min-score`/`--max-clips` but never
  `--template`; `export` gets `--template`/`--auto`). Earlier it forwarded
  `--template`/`--min-score` to every mode, which crashed `analyze` and `export`.
- Frontend: `web/index.html` + `style.css` + `app.js` (vanilla, no build step). Dark
  theme (#0E0E12), teal accent (#2DE1C2), playhead progress rail + 5 stage chips,
  film-sprocket log console, review cards, output gallery. Polls `/api/run/{id}` every
  500 ms for progress + logs. Poll gives up after 8 consecutive failures and shows
  "Lost connection" (so a dead server no longer leaves an infinite spinner). Overlay
  has a Cancel button that POSTs to the cancel endpoint.
- Launch: double-click `start_clipforge.bat` (checks ffmpeg + .venv, opens browser to
  http://localhost:8600, runs `server.py` and streams backend logs in that window).
- Port: 8600 (pass a different port as `server.py 9000`).
