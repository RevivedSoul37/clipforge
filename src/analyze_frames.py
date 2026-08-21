"""Style analysis over extracted frames.

Detects the editing "constitution" of a reference clip set:
  - output aspect + letterbox/band geometry (pure-black band detection)
  - hook text block at the top of the video band
  - caption text block in the lower part of the video band
  - keyword highlight color (pure saturated reds/yellows)
  - static CTA line above the band bottom

Emits a style report (JSON) and a draft template JSON compatible with
src/apply_template.py. This is the analysis half of the style-extraction
loop; the human/agent reviews the draft and tunes it before use.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from src.config import config


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _clusters(rows, gap=8):
    if len(rows) == 0:
        return []
    out, g = [], [rows[0]]
    for r in rows[1:]:
        if r - g[-1] <= gap:
            g.append(r)
        else:
            out.append((g[0], g[-1]))
            g = [r]
    out.append((g[0], g[-1]))
    return out


def _hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*(int(max(0, min(255, v))) for v in rgb))


def _median_rgb(im, mask):
    px = im[mask]
    if len(px) == 0:
        return None
    return [int(v) for v in np.median(px, axis=0)]


def _load_gray(path):
    return np.asarray(Image.open(path).convert("RGB")).astype(int)


def _band_bounds(im):
    """Video band bounds via per-row brightness std (black bands -> std~0)."""
    h = im.shape[0]
    rowstd = im.std(axis=(1, 2))
    video_rows = rowstd > 12
    if not video_rows.any():
        return 0, h - 1
    top = int(np.argmax(video_rows))
    bot = int(h - np.argmax(video_rows[::-1]) - 1)
    return top, bot


# masks ------------------------------------------------------------------ #
def _red_mask(im):
    return (im[:, :, 0] > 160) & (im[:, :, 1] < 70) & (im[:, :, 2] < 70)


def _yellow_mask(im):
    return ((im[:, :, 0] > 185) & (im[:, :, 1] > 150) &
            (im[:, :, 2] < 140) & (im[:, :, 0] - im[:, :, 2] > 80))


def _white_mask(im):
    mx = im.max(axis=2)
    mn = im.min(axis=2)
    return (im.mean(axis=2) > 190) & ((mx - mn) < 60)


# --------------------------------------------------------------------------- #
# per-frame analysis
# --------------------------------------------------------------------------- #
def analyze_frame(path, w, h):
    im = _load_gray(path)
    fh, fw = im.shape[:2]
    vt, vb = _band_bounds(im)
    band_h = vb - vt + 1
    r = {
        "frame": Path(path).name,
        "video_band": [vt, vb],
        "band_fill": round(band_h / fh, 4),
    }

    # hook: white-ish text in the top 22% of the band
    hook_zone = im[vt: min(vt + int(band_h * 0.22), fh)]
    if hook_zone.size:
        wm = _white_mask(hook_zone)
        rows = np.where(wm.sum(axis=1) > fw * 0.01)[0]
        cl = _clusters(list(rows))
        if cl and cl[0][1] - cl[0][0] >= 4:
            rgb = _median_rgb(hook_zone, wm)
            r["hook"] = {
                "rows": [int(cl[0][0]) + vt, int(cl[0][1]) + vt],
                "rgb": rgb,
            }

    # captions: warm-white or colored text in the lower 45% of the band
    cap_zone = im[max(vt + int(band_h * 0.55), 0): vb + 1]
    if cap_zone.size:
        wm = _white_mask(cap_zone)
        rm = _red_mask(cap_zone)
        ym = _yellow_mask(cap_zone)
        rows = np.where((wm | rm | ym).sum(axis=1) > fw * 0.015)[0]
        cl = _clusters(list(rows))
        if cl:
            top_c = cl[0]
            rgb = (_median_rgb(cap_zone, wm) or
                   _median_rgb(cap_zone, rm) or
                   _median_rgb(cap_zone, ym))
            r["captions"] = {
                "rows": [int(top_c[0]) + vt + int(band_h * 0.55), int(top_c[1]) + vt + int(band_h * 0.55)],
                "rgb": rgb,
                "has_red": bool(rm.sum() > 30),
                "has_yellow": bool(ym.sum() > 30),
            }
            if rm.sum() > 30:
                r["captions"]["keyword_rgb"] = _median_rgb(cap_zone, rm)
            if ym.sum() > 30:
                r["captions"]["secondary_rgb"] = _median_rgb(cap_zone, ym)

    # CTA: red text in the bottom 12% of the band (static red line)
    cta_zone = im[max(vb - int(band_h * 0.12), 0): vb + 1]
    if cta_zone.size:
        rm = _red_mask(cta_zone)
        rows = np.where(rm.sum(axis=1) > fw * 0.008)[0]
        cl = _clusters(list(rows))
        if cl and cl[-1][1] - cl[-1][0] >= 3:
            r["cta"] = {
                "rows": [int(cl[-1][0]) + vb - int(band_h * 0.12), int(cl[-1][1]) + vb - int(band_h * 0.12)],
                "rgb": _median_rgb(cta_zone, rm),
            }
    return r


# --------------------------------------------------------------------------- #
# aggregate across frames -> style report -> draft template
# --------------------------------------------------------------------------- #
def analyze_style(stem, progress=None):
    frames_dir = config.frames_dir / stem
    if not frames_dir.is_dir():
        raise FileNotFoundError(
            f"No frames for '{stem}'. Run the frames command first.")
    frames = sorted(frames_dir.glob("f_*.jpg"))
    if not frames:
        raise FileNotFoundError(f"No frame images in {frames_dir}")

    per_frame = []
    for i, f in enumerate(frames):
        im = _load_gray(f)
        fh, fw = im.shape[:2]
        per_frame.append(analyze_frame(f, fw, fh))
        if progress:
            progress((i + 1) / len(frames))

    # aggregate geometry
    fills = [pf["band_fill"] for pf in per_frame]
    bands = [pf["video_band"] for pf in per_frame]
    median_fill = float(np.median(fills))

    # aspect classification
    w0, h0 = per_frame[0] and frames[0].stem, None
    first = np.asarray(Image.open(frames[0]).convert("RGB"))
    fh, fw = first.shape[:2]
    source_ar = None
    man = None
    man_path = frames_dir / "manifest.json"
    if man_path.exists():
        man = json.loads(man_path.read_text(encoding="utf-8"))
        if man.get("source_resolution"):
            sw, sh = man["source_resolution"]
            source_ar = round(sw / sh, 4)

    # band aspect: if band_fill * frame_h / frame_w is close to 1 -> square band
    band_ar = round((median_fill * fh) / fw, 3)

    hooks = [pf["hook"] for pf in per_frame if "hook" in pf]
    caps = [pf["captions"] for pf in per_frame if "captions" in pf]
    ctas = [pf["cta"] for pf in per_frame if "cta" in pf]

    def _med_rgb(items, key):
        vals = [it[key] for it in items if it.get(key)]
        if not vals:
            return None
        return [int(v) for v in np.median(np.array(vals), axis=0)]

    def _med_rel(items, key, fh, base="rows"):
        """median of row positions expressed relative to frame height."""
        vals = []
        for it in items:
            rr = it.get(base)
            if rr:
                vals.append(rr[key] / fh)
        return float(np.median(vals)) if vals else None

    red_kw = _med_rgb(caps, "keyword_rgb")
    hook_rgb = _med_rgb(hooks, "rgb")
    cap_rgb = _med_rgb(caps, "rgb")
    cta_rgb = _med_rgb(ctas, "rgb")

    report = {
        "stem": stem,
        "frames_analyzed": len(per_frame),
        "source_aspect_ratio": source_ar,
        "band_fill_median": round(median_fill, 4),
        "band_aspect": band_ar,
        "layout": _classify_layout(median_fill, band_ar, source_ar),
        "hook": {
            "present_in": f"{len(hooks)}/{len(per_frame)}",
            "median_rgb": hook_rgb,
            "median_hex": _hex(hook_rgb) if hook_rgb else None,
            "anchor": _med_rel(hooks, 0, fh),
            "height_px": int(np.median([h["rows"][1] - h["rows"][0] for h in hooks])) if hooks else None,
        },
        "captions": {
            "present_in": f"{len(caps)}/{len(per_frame)}",
            "median_rgb": cap_rgb,
            "median_hex": _hex(cap_rgb) if cap_rgb else None,
            "keyword_red": bool(red_kw),
            "keyword_hex": _hex(red_kw) if red_kw else None,
            "anchor": _med_rel(caps, 0, fh),
        },
        "cta": {
            "present_in": f"{len(ctas)}/{len(per_frame)}",
            "median_rgb": cta_rgb,
            "median_hex": _hex(cta_rgb) if cta_rgb else None,
            "anchor": _med_rel(ctas, 0, fh),
        },
        "per_frame": per_frame,
    }
    return report


def _classify_layout(median_fill, band_ar, source_ar):
    if median_fill > 0.92:
        return "full_frame"
    if abs(band_ar - 1.0) <= 0.06:
        return "square_band"
    return "letterbox"


# --------------------------------------------------------------------------- #
# draft template generation
# --------------------------------------------------------------------------- #
def draft_template(report, name, cta_text="Follow for more!"):
    """Turn a style report into a draft template JSON."""
    layout = report["layout"]
    crop = {"mode": layout if layout != "full_frame" else "letterbox",
            "background": "#000000",
            "follow_speaker": layout == "square_band"}

    t = {
        "name": name,
        "description": f"Auto-drafted from reference '{report['stem']}' "
                       f"({report['frames_analyzed']} frames). Review and tune.",
        "output": {"aspect_ratio": "9:16", "resolution": "1080x1920"},
        "crop": crop,
        "hook": {"enabled": True, "font": "Bebas Neue", "size": 110,
                 "color": report["hook"]["median_hex"] or "#FAF8E6",
                 "position": "top", "margin_v": 180},
        "captions": {
            "enabled": True, "font": "Poppins-Bold", "size": 76,
            "color": report["captions"]["median_hex"] or "#FAF8E6",
            "outline_color": "#000000", "outline_width": 4,
            "position": "bottom_center", "margin_v": 250, "max_words": 4,
            "highlight_keyword": {
                "enabled": bool(report["captions"]["keyword_hex"]),
                "color": report["captions"]["keyword_hex"] or "#E00000",
            },
        },
        "music": {"enabled": True, "volume": 0.12},
        "broll": {"enabled": False, "mode": "cutaway", "pip_scale": 0.6},
        "intro": {"enabled": False},
        "outro": {"enabled": False},
        "watermark": {"enabled": False},
    }
    if report["cta"]["median_hex"]:
        t["cta"] = {
            "enabled": True,
            "text": cta_text,
            "font": "Poppins-Bold",
            "size": 48,
            "color": report["cta"]["median_hex"],
            "position": "bottom",
            "margin_v": 120,
        }
    else:
        t["cta"] = {"enabled": False}
    return t


def analyze_and_draft(stem, name=None, cta_text="Follow for more!", progress=None):
    report = analyze_style(stem, progress=progress)
    name = name or f"{stem}_style"
    template = draft_template(report, name, cta_text=cta_text)
    return report, template
