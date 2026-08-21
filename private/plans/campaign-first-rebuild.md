# Campaign-first rebuild (foundation + UX)

Pause feature work (diarization, presets, batch queue, visual timeline). Refactor ClipForge so **Campaign is the parent object** and the UI is a production workspace. Wipe existing on-disk test data.

Do **not** implement speaker diarization, preset modes, batch queue, or the visual timeline in this pass. Leave explicit stubs so those four plug in later without another rewrite.

## Goal

Replace the current **per-video wizard** (Source → Analyze → Review → Export → Style Lab) with:

1. Home = campaign list
2. Campaign workspace = Overview / Sources / Candidates / Approved / Exports / Settings
3. Campaign JSON as the contract between UI and pipeline

Analyze and export still run **one source at a time** (existing `/api/run`). Lists are **campaign-wide**.

## Current vs target

Today (`src/campaigns.py`, `web/`):

- Campaign folder already exists (`data/campaigns/<id>/input|transcripts|clip_candidates|output|…`)
- UI is still a video-scoped pipeline plus a gig kanban (`analyzing|reviewing|exported|posted`)
- `Campaign.public()` is gig-shaped: `platform`, `payout_rate`, `deadline`, `status` active/submitted/paid/expired
- Dashboard cards show those fields, not funnel counts
- Review/Export read one `currentVideoId`

Target:

```
Campaign
 ├── Sources
 ├── Transcripts          (derived, not a nav tab)
 ├── Analysis             (per-source action)
 ├── Candidates
 ├── Approved clips
 ├── Exports
 ├── Settings             (brief, golden style, music, run knobs)
 ├── preset               (null stub)
 └── speakers             ([] stub)
```

## Decisions (locked)

- **Campaign = production studio**, not a clipping gig. Drop payout / deadline / paid / posted as primary UX. Brief + style live in Settings.
- **Candidates / Approved / Exports are campaign-wide.** Each row carries `source_id`. Analyze/export buttons stay per-source until batch exists.
- **Wipe test data** on implement: campaigns, transcripts, candidates, frames, input, output. Keep `templates/`, `music/`, `assets/fonts`, `config.json`, `.env`.
- **Out of scope:** diarization, presets, batch queue, timeline, new AI quality work.

---

## 1. Campaign contract

Expand `Campaign.public()` / `public(detail=True)` so the UI never invents a second model.

```json
{
  "id": "podcast-clips-a1b2c3",
  "name": "Podcast Clips",
  "created_at": "…",
  "updated_at": "…",
  "preset_id": null,
  "speakers": [],
  "settings": {
    "min_score": 0.5,
    "max_clips": 10,
    "default_template": "square_captioned",
    "music_enabled": true,
    "music_track": "",
    "music_volume": 0.12
  },
  "funnel": {
    "sources": 8,
    "transcribed": 8,
    "analysed": 8,
    "candidates": 63,
    "approved": 14,
    "exported": 7
  },
  "processing_status": "idle",
  "has_rules": false,
  "has_template": false
}
```

Detail payload also includes `rules_summary`, `sources[]` (or those come from dedicated endpoints — see APIs).

**Derive funnel from disk**, do not trust `clips.json` as source of truth:

| Count | Source |
|---|---|
| sources | `input/` video files |
| transcribed | matching `transcripts/<stem>_transcript*.json` |
| analysed | matching `clip_candidates/<stem>_candidates.json` |
| candidates | sum of `clips[]` in those files |
| approved | clips with `status == "approved"` |
| exported | `output/` mp4s for those stems (existing `_media_list` logic) |

Keep writing `clips.json` from `sync_clips_from_candidates` / `mark_clips_exported` if cheap, but **stop using it for dashboard/kanban**. Delete the kanban UI.

`create_campaign(name)` only. Drop required platform/payout/deadline. `update_campaign` allowed keys: `name`, `settings` (merged), later `preset_id`. Remove `CAMPAIGN_STATUSES` from the primary path (leave unused constants if needed for one release).

`meta.json` gains `updated_at`, `settings`, `preset_id`, `speakers: []`. Ignore leftover `platform`/`payout_rate`/`deadline`/`status` if present.

---

## 2. APIs

Keep existing run/upload/candidates/music/broll/preview routes. Add aggregators; slim create/list.

| Method | Path | Role |
|---|---|---|
| GET | `/api/campaigns` | list with `funnel` + `updated_at` (not clip_counts / payout) |
| POST | `/api/campaigns` | `{ name }` only |
| GET | `/api/campaigns/{id}` | detail contract above |
| PATCH | `/api/campaigns/{id}` | name + settings |
| GET | `/api/campaigns/{id}/sources` | videos + per-source stage: none / uploaded / transcribed / analysed / has_approved / exported, plus candidate/approved counts |
| GET | `/api/campaigns/{id}/candidates` | all sources’ clips, each with `source_id`, `source_name`, transcript snippet optional |
| GET | `/api/campaigns/{id}/exports` | all output mp4s with `source_id` |

Save-review: existing `POST /api/candidates` is per-video. Keep it; Candidates page groups by source and PATCHes per file (or one POST per source on Save).

`GET /api/state?campaign_id=` can stay for templates + config defaults used by Settings/Export.

Do **not** add `/api/jobs` or speaker routes.

---

## 3. UI information architecture

**Routes**

- `#/dashboard`
- `#/campaign/<id>` → Overview
- `#/campaign/<id>/sources`
- `#/campaign/<id>/candidates`
- `#/campaign/<id>/approved`
- `#/campaign/<id>/exports`
- `#/campaign/<id>/settings`

Remove pages: `source`, `analyze`, `review`, `export`, `style` as top-level pipeline. Style Lab folds into Settings as a collapsed optional block (reuse existing extract/analyze JS; do not delete backend).

**Shell**

```
┌ ClipForge ──────────────────────────────────┐
│  Campaigns          [campaign name]         │
├────────────┬────────────────────────────────┤
│ Overview   │  (page)                        │
│ Sources    │                                │
│ Candidates │                                │
│ Approved   │                                │
│ Exports    │                                │
│ Settings   │                                │
└────────────┴────────────────────────────────┘
```

Home has no sidebar. Campaign workspace has a **left subnav** (not the current top pipeline links). Global run bar stays at the bottom.

**Dashboard**

- Campaign cards: name, last activity (`updated_at`), funnel line `8 videos · 63 candidates · 14 approved · 7 exported`
- Empty state: one primary **New campaign** (name only). No always-visible payout form.
- Click card → Overview

**Overview**

Vertical funnel, not a kanban:

```
12 Sources → 12 Transcribed → 12 Analysed → 84 Candidates → 17 Approved → 6 Exported
```

Each step is a link to that tab. Idle copy when empty: “Add a source to start this campaign.” Primary CTA: **Add sources**.

**Sources**

- Library of campaign videos (move existing upload/dropzone here)
- Per row: filename, size, stage chip, counts, actions **Analyze** / **Export approved** (export disabled until that source has approved clips)
- Selecting a video is no longer a global “working on” bar. Analyze/export pass `video` in `/api/run` from the row.

**Candidates** (replaces Review)

- Campaign-wide list, grouped by source
- Same approve / reject / hook / in-out fields as today’s review cards
- Save decisions per source via existing candidates POST
- No timeline

**Approved**

- Filter of candidates with `status === "approved"`
- Per source (or per clip) **Export** → `mode: "export"` for that `video`

**Exports**

- Grid of rendered mp4s (existing output cards), grouped by source
- Open-folder + download unchanged

**Settings**

- Brief upload + four rule sections (move from current campaign detail)
- Golden style buttons (Square band / Letterbox) — persist as `settings.default_template`
- Music + b-roll status (move from Export)
- Min score / max clips (move from Analyze)
- Style Lab optional, last

---

## 4. Visual redesign

Current UI is generic dark + teal (`#0b0d10` / `#2de1c2` / Bahnschrift). Replace tokens and layout; bump cache `?v=` on css/js.

**Subject:** a clipping bay for long-form interviews → 9:16 posts.

**Palette (named)**

| Token | Hex | Use |
|---|---|---|
| Graphite | `#161311` | app ground |
| Slate | `#221E1A` | raised panels |
| Bone | `#F3EBE1` | primary text |
| Dust | `#9C9186` | secondary |
| Cadmium | `#FF5C1A` | primary actions, active nav, funnel fill |
| Rec | `#C81D25` | reject / destructive |
| Phosphor | `#D4F06E` | success / exported chip only |

No teal. No numbered step badges (`<span class="stepno">`).

**Type**

- Display (campaign titles, funnel numbers): `"IBM Plex Sans Condensed"` (or `"Barlow Condensed"`) — industrial edit-bay, not Bahnschrift
- Body: `"IBM Plex Sans"` / system-ui
- Data (timecodes, counts): `"IBM Plex Mono"` / existing mono stack
- Load via existing local fonts if added under `assets/fonts`, else system fallbacks that match condensed + grotesque. Do not use Inter / Roboto / Bahnschrift as display.

**Layout**

- Dashboard: 2-column card grid, generous padding, no form panel above the grid
- Workspace: ~220px left rail, content column max ~1080px
- Campaign cards: left **9:16 film-frame** (signature) whose inner bar fills by `exported/max(sources,1)` — the one memorable device
- Funnel on Overview: a single vertical stack of labeled counts, cadmium tick on completed stages, not a row of identical stat chips

**Copy**

- “Campaigns” home, not “Dashboard”
- Buttons: “New campaign”, “Add sources”, “Find highlights”, “Save decisions”, “Export approved”
- Empty Candidates: “Analyze a source to get candidates.”
- Errors stay specific (existing toast pattern)

**Motion**

- Funnel fill and film-frame bar: one 200–300ms ease. Respect `prefers-reduced-motion`. No page-load choreography.

---

## 5. Wipe test data

On implement, delete contents (not the folders if code assumes they exist):

- `data/campaigns/`
- `data/transcripts/`, `data/context/`, `data/clip_candidates/`, `data/frames/`
- `input/`, `output/` (including `output/raw/`)
- Do **not** delete `data/broll/cache` requirement — empty it; keys stay in `.env`
- Do **not** delete `templates/`, `music/`, `assets/`, `config.json`

If a campaign dir is mid-run, wipe is still OK — user stated all of it is test data.

Touch `updated_at` on analyze/export success so dashboard “last activity” works after the wipe.

---

## 6. Files to change

| File | What |
|---|---|
| `src/campaigns.py` | funnel helpers, settings on meta, slim create/update, `updated_at`, speakers/preset stubs, drop kanban from `public()` |
| `server.py` | list/create/get/patch payloads; new sources/candidates/exports aggregators |
| `web/index.html` | new shell + six campaign pages; delete wizard + kanban markup |
| `web/app.js` | router, dashboard cards, funnel, campaign-wide lists, per-source run, settings; delete kanban/clip-board |
| `web/style.css` | new tokens, sidebar, cards, funnel, film-frame signature |
| `main.py` | set `updated_at` after analyze/export (small) |
| `config.py` | unchanged unless settings defaults need a helper |

Do not restructure pipeline stages (`transcribe`, `select_highlights`, `fetch_broll`, `apply_template`). They already key off `config.activate_campaign`.

---

## 7. Failure modes

- **Empty campaign:** Overview + Sources empty states; no crash on missing candidates files
- **Partial analyze:** source chip `transcribed` vs `analysed` independently
- **Save review with mixed sources:** never write source B’s file when saving source A
- **Export with zero approved:** button disabled; server still returns existing “No approved clips” if hit
- **Run in progress:** keep global run bar; disable Analyze/Export on that source while `status` is running
- **Legacy meta.json:** ignore gig fields; funnel still computes
- **Missing MediaPipe/silero:** unrelated; leave as-is

---

## 8. Validation

1. Wipe dirs; restart `server.py`; `#/dashboard` shows empty + New campaign
2. Create “Podcast Clips” (name only) → Overview funnel all zeros
3. Upload one video on Sources → sources=1, rest 0
4. Analyze that row → transcribed/analysed/candidates increment; Candidates lists clips with `source_id`
5. Approve two, Save → Approved tab shows two; funnel approved=2
6. Export approved on that source → Exports grid; funnel exported ≥ 1; dashboard card counts match
7. Second source in same campaign appears in the same Candidates list, grouped
8. Settings golden style persists after reload (`meta.settings.default_template`)
9. No gig fields, no kanban, no Source/Analyze/Review/Export top nav
10. Style Lab still reachable from Settings (optional)

---

## 9. Explicit non-goals

- Speaker diarization / `speakers[]` UI
- Preset JSON / campaign preset picker (`preset_id` stays null)
- Batch job queue / multi-video progress list
- wavesurfer timeline
- New VAD/B-roll/crop work
- Migrating old test campaigns (wipe instead)

After this ships, the next feature should be **preset modes** attached to `campaign.settings` / `preset_id`, then speakers, then batch on Sources, then timeline on Candidates.
