# Rules.md — ClipForge

## Use
- Python for all backend/pipeline logic
- ffmpeg for all cutting/rendering (via subprocess, not a heavy wrapper lib)
- faster-whisper for transcription (GPU-accelerated, local, free)
- Streamlit for the review UI
- JSON files for intermediate data — no database in v1
- Ollama for local LLM calls

## Avoid
- No database (Postgres/Mongo) in v1 — folders + JSON are enough
- No moviepy for core cutting — raw ffmpeg only
- No full web frontend (React/Next.js) for review — Streamlit is enough
- No hardcoded API keys or paths — use `.env` / config file
- No real-time/live clipping in v1 — batch processing only

## Error Handling
- Every stage fails loudly with a clear message (which file, which stage, what went wrong)
- Whisper/LLM stages save intermediate output to disk immediately
- Validate clip timestamps are within the video's actual duration before ffmpeg
- If the LLM returns malformed output, retry once with a stricter prompt, then flag for manual review rather than crashing

## Per-Video Context (required before highlight selection)
- Generate a `context.json`: video type (gaming/podcast/vlog/etc.), tone, topic, target platform
- Passed alongside the transcript into the highlight-selection prompt
- Saved to `data/context/<video_id>_context.json` so it's reusable on rerun

## Self-Explaining Templates
- Every template JSON includes a `description` field in plain English
- The agent/LLM can read template descriptions to reason about which template fits a clip

## Boundaries for the Coding Agent
- Build and test one phase at a time (see Phases.md) — do not jump ahead
- Do not introduce new dependencies/libraries without flagging them first
- Keep each script single-purpose
- Always update Memory.md after completing a phase or file
- Ask before making architecture-level changes
