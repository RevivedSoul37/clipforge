# PRD.md — ClipForge (AI-Powered Auto Clipper)

## What to Build
A local desktop pipeline that takes a raw long-form video (gaming stream, podcast, vlog) and outputs short, auto-edited, caption-ready clips — with zero manual editing.

Flow: Raw video → Transcribe (Whisper) → LLM picks highlight moments → ffmpeg cuts clips → Template engine applies auto-edit (crop, captions, zoom, transitions, intro/outro) → Export ready-to-post vertical/horizontal clips.

## Targeted Users
- Solo content creators / streamers who record long sessions but don't have time to edit
- Faceless channel operators who need a repeatable clip pipeline
- Munavar himself, first, as the primary user and test case

## Core Features (v1)
1. Import a video file (local upload)
2. Auto-transcribe with timestamps (Whisper, local, runs on his RTX 5070 Ti)
3. LLM scans transcript and scores/selects highlight-worthy segments (funny, exciting, insightful, punchline, etc.) with start/end timestamps and a reason tag
4. User can review/approve/edit the LLM's suggested clip list before rendering
5. ffmpeg cuts approved segments
6. Auto-edit templates applied to each clip:
   - Aspect ratio conversion (16:9 → 9:16 or 1:1)
   - Auto captions burned in (styled, from Whisper word timestamps)
   - Basic zoom/pan or crop-to-face (optional, v2)
   - Intro/outro stinger, watermark (optional, template-based)
7. Batch export all approved clips to an output folder

## Out of Scope (v1)
- Cloud rendering / hosting
- Auto-posting to social platforms (v2+)
- Multi-camera editing
- Real-time/live clipping while streaming

## Success Criteria
- Feed a 1-hour raw video in, get 5-10 usable vertical clips out with captions, with minimal manual review time (<10 min review per video)
