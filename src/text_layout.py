"""Centralized text layout engine for 9:16 video rendering.

Single source of truth for where every piece of text goes and how big it is.
The renderer (ASS/libass via FFmpeg) consumes layout results; it never decides
its own coordinates.

Pipeline:

    TextContent -> TextStyle -> Measurement -> LayoutEngine
        -> CollisionCheck -> SafeZoneValidation -> SubjectScoring
        -> FinalLayout -> Renderer

Key invariants:
- All layout math happens in the CANONICAL 1080x1920 coordinate space, regardless
  of the actual render resolution. ASS PlayResX/PlayResY is pinned to the
  canonical size so libass scales subtitles uniformly -> preview and final
  export share one layout model.
- Styles describe HOW text looks (font/size/color/stroke). The layout engine
  decides WHERE text goes (x/y) within safe zones, free of collisions.
- Every element has a stable unique id; duplicates are rejected.
- Bounding boxes include stroke, shadow and padding, not just raw glyphs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from src.config import config

# --------------------------------------------------------------------------- #
# canonical canvas + safe zones
# --------------------------------------------------------------------------- #
CANVAS_W = 1080
CANVAS_H = 1920
ASPECT_W = 9
ASPECT_H = 16

DEFAULT_SAFE = {
    "top": 180,
    "bottom": 300,
    "left": 80,
    "right": 80,
}

FONT_DIR = config.root / "assets" / "fonts"

# family name -> ttf file. Falls back to disk globbing for unknown families.
FONT_FILES = {
    "Anton": "Anton-Regular.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Bangers": "Bangers-Regular.ttf",
    "Barlow Condensed": "BarlowCondensed-Bold.ttf",
    "Saira Condensed": "SairaCondensed-Bold.ttf",
    "Kanit": "Kanit-Bold.ttf",
    "Lato": "Lato-Bold.ttf",
    "Lato-Bold": "Lato-Bold.ttf",
    "Poppins-Bold": "Poppins-Bold.ttf",
    "Poppins": "Poppins-Bold.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Arial": "Arial",
}

_FONT_CACHE = {}


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
class TextType(str, Enum):
    HOOK = "HOOK"
    TITLE = "TITLE"
    CAPTION = "CAPTION"
    SUBTITLE = "SUBTITLE"
    HIGHLIGHT = "HIGHLIGHT"
    CTA = "CTA"
    WATERMARK = "WATERMARK"


@dataclass
class SafeArea:
    top: int = 180
    bottom: int = 300
    left: int = 80
    right: int = 80

    @property
    def interior_w(self):
        return CANVAS_W - self.left - self.right

    @property
    def interior_h(self):
        return CANVAS_H - self.top - self.bottom

    def interior_rect(self):
        return self.left, self.top, self.interior_w, self.interior_h

    def contains(self, box: "BoundingBox", slack: int = 0) -> bool:
        return (box.x >= self.left - slack
                and box.y >= self.top - slack
                and box.x + box.width <= CANVAS_W - self.right + slack
                and box.y + box.height <= CANVAS_H - self.bottom + slack)


@dataclass
class TextStyle:
    font: str = "Poppins-Bold"
    font_weight: str = "bold"
    font_size: int = 80
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: int = 4
    shadow: bool = False
    shadow_offset: int = 4
    alignment: str = "bottom_center"  # preferred
    max_width: Optional[int] = None
    max_lines: int = 3
    min_font_size: int = 48
    max_font_size: int = 130
    line_height_mult: float = 1.08
    letter_spacing: int = 0
    background: Optional[dict] = None
    padding: int = 0


@dataclass
class TextElement:
    id: str
    type: TextType
    text: str
    style: TextStyle
    # optional normalized preferred anchor (0..1) in canvas space; None = auto
    preferred_x: Optional[float] = None
    preferred_y: Optional[float] = None


@dataclass
class BoundingBox:
    x: int
    y: int
    width: int
    height: int

    def intersects(self, other: "BoundingBox", slack: int = 0) -> bool:
        return not (self.x + self.width + slack <= other.x
                    or other.x + other.width + slack <= self.x
                    or self.y + self.height + slack <= other.y
                    or other.y + other.height + slack <= self.y)

    def as_dict(self):
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass
class LayoutResult:
    element_id: str
    type: TextType
    text: str
    lines: list
    font_size: int
    line_height: int
    alignment: int            # ASS an value
    anchor_x: int             # ASS pos x
    anchor_y: int             # ASS pos y
    bounding_box: BoundingBox
    score: float = 0.0
    style: TextStyle = None

    def as_dict(self):
        return {
            "element_id": self.element_id,
            "type": self.type.value,
            "lines": self.lines,
            "font_size": self.font_size,
            "line_height": self.line_height,
            "alignment": self.alignment,
            "anchor": {"x": self.anchor_x, "y": self.anchor_y},
            "bounding_box": self.bounding_box.as_dict(),
            "score": round(self.score, 3),
        }


# --------------------------------------------------------------------------- #
# font loading + measurement (PIL)
# --------------------------------------------------------------------------- #
def resolve_font_path(family: str) -> Optional[Path]:
    """Resolve a font family name to a TTF path on disk."""
    if not family:
        return None
    mapped = FONT_FILES.get(family)
    if mapped:
        if Path(mapped).is_absolute() or "/" in mapped or "\\" in mapped:
            p = Path(mapped)
        else:
            p = FONT_DIR / mapped
        if p.exists():
            return p
    # try direct name variants
    for cand in (f"{family}.ttf", f"{family.replace(' ', '')}.ttf",
                 f"{family.replace(' ', '-')}.ttf"):
        p = FONT_DIR / cand
        if p.exists():
            return p
    # case-insensitive glob
    if FONT_DIR.is_dir():
        for f in FONT_DIR.glob("*.ttf"):
            if f.stem.lower() == family.lower():
                return f
    return None


def load_font(family: str, size: int):
    """Cached PIL ImageFont for (family, size). Falls back to a default font."""
    key = (family, int(size))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    from PIL import ImageFont
    path = resolve_font_path(family)
    try:
        if path is not None and path.exists():
            font = ImageFont.truetype(str(path), int(size))
        else:
            font = ImageFont.load_default()
            _FONT_CACHE[key] = font
            return font
    except Exception:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def measure_line(text: str, family: str, size: int, spacing: int = 0) -> tuple:
    """Return (width, ascent, descent) for a single line at the given size."""
    font = load_font(family, size)
    try:
        ascent, descent = font.getmetrics()
    except Exception:
        ascent, descent = int(size), int(size * 0.25)
    try:
        width = font.getlength(text or " ")
    except Exception:
        try:
            bbox = font.getbbox(text or " ")
            width = bbox[2] - bbox[0]
        except Exception:
            width = int(size * 0.5 * len(text or " "))
    if spacing and text:
        width += spacing * max(0, len(text) - 1)
    return int(width), int(ascent), int(descent)


def measure_block(lines: list, family: str, size: int,
                 line_height_mult: float = 1.08, spacing: int = 0) -> tuple:
    """Return (width, height, line_height) for a wrapped block of lines."""
    if not lines:
        return 0, 0, 0
    widths = []
    _, asc, desc = measure_line("Mg", family, size, spacing)
    line_h = max(1, int((asc + desc) * line_height_mult))
    max_w = 0
    for ln in lines:
        w, _, _ = measure_line(ln, family, size, spacing)
        widths.append(w)
        max_w = max(max_w, w)
    return max_w, line_h * len(lines), line_h


# --------------------------------------------------------------------------- #
# word wrapping + line balancing
# --------------------------------------------------------------------------- #
def _split_words(text: str) -> list:
    text = (text or "").replace("\n", " ")
    return [w for w in text.split(" ") if w != ""]


def wrap_greedy(text: str, family: str, size: int, max_width: int,
                spacing: int = 0) -> list:
    """Greedy word wrap that never splits a word. Returns list of line strings."""
    words = _split_words(text)
    if not words:
        return []
    lines = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        width, _, _ = measure_line(trial, family, size, spacing)
        if width <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def balance_lines(lines: list, family: str, size: int, max_width: int,
                  spacing: int = 0) -> list:
    """Re-balance break points to even out line widths without splitting words.

    Only redistributes whole words between adjacent lines; never creates a line
    wider than max_width. Improves the look of multi-line titles so breaks read
    as intentional rather than accidental."""
    if len(lines) <= 1:
        return lines
    words = []
    for ln in lines:
        words.extend(_split_words(ln))
    if not words:
        return lines
    n = len(lines)
    improved = True
    out = list(lines)
    guard = 0
    while improved and guard < n * len(words) + 50:
        improved = False
        guard += 1
        for i in range(n - 1):
            # try moving the first word of line i+1 up into line i
            up = _split_words(out[i])
            down = _split_words(out[i + 1])
            if not down:
                continue
            candidate_up = up + [down[0]]
            w_up = measure_line(" ".join(candidate_up), family, size, spacing)[0]
            if w_up <= max_width:
                # measure variance improvement
                before = [measure_line(out[j], family, size, spacing)[0]
                          for j in (i, i + 1)]
                after = [w_up, measure_line(" ".join(down[1:]), family, size, spacing)[0]]
                before_max = max(before)
                after_max = max(after)
                if after_max < before_max or (
                        after_max == before_max and max(after) - min(after)
                        < max(before) - min(before)):
                    out[i] = " ".join(candidate_up)
                    out[i + 1] = " ".join(down[1:])
                    improved = True
            # try moving the last word of line i down into line i+1
            # (prepend to line i+1 to preserve word order)
            if len(up) > 1:
                w_down = measure_line(" ".join([up[-1]] + down), family, size, spacing)[0]
                if w_down <= max_width:
                    before = [measure_line(out[j], family, size, spacing)[0]
                              for j in (i, i + 1)]
                    after = [measure_line(" ".join(up[:-1]), family, size, spacing)[0], w_down]
                    if max(after) < max(before) or (
                            max(after) == max(before) and max(after) - min(after)
                            < max(before) - min(before)):
                        out[i] = " ".join(up[:-1])
                        out[i + 1] = " ".join([up[-1]] + down)
                        improved = True
    # drop empty trailing lines
    return [ln for ln in out if ln.strip()]


def wrap_text(text: str, family: str, size: int, max_width: int,
              spacing: int = 0, balance: bool = True) -> list:
    lines = wrap_greedy(text, family, size, max_width, spacing)
    if balance and len(lines) > 1:
        lines = balance_lines(lines, family, size, max_width, spacing)
    return lines


# --------------------------------------------------------------------------- #
# fit: auto-scale font until text fits
# --------------------------------------------------------------------------- #
def fit_text(text: str, style: TextStyle, max_width: int, max_lines: int,
             max_height: Optional[int] = None) -> dict:
    """Find the largest font size <= style.font_size that fits the text within
    max_width x max_lines (and optional max_height). Never splits words.

    Returns {lines, font_size, line_height, width, height}.
    If it still cannot fit at min font, returns the min-font layout (caller may
    then choose to shorten the text)."""
    if not text or not text.strip():
        return {"lines": [], "font_size": style.font_size, "line_height": 0,
                "width": 0, "height": 0}
    text = text.strip()
    size = int(min(style.font_size, style.max_font_size))
    min_size = int(max(8, style.min_font_size))
    step = max(2, size // 20)
    last = None
    while size >= min_size:
        lines = wrap_text(text, style.font, size, max_width,
                          style.letter_spacing, balance=True)
        w, h, lh = measure_block(lines, style.font, size,
                                 style.line_height_mult, style.letter_spacing)
        fits = (len(lines) <= max_lines and w <= max_width
                and (max_height is None or h <= max_height))
        last = {"lines": lines, "font_size": size, "line_height": lh,
                "width": w, "height": h}
        if fits:
            return last
        size -= step
    return last


# --------------------------------------------------------------------------- #
# bounding box (incl. stroke/shadow/padding)
# --------------------------------------------------------------------------- #
def text_bounding_box(lines: list, font_size: int, line_height: int,
                     width: int, height: int, style: TextStyle,
                     anchor_x: int, anchor_y: int, alignment: int) -> BoundingBox:
    """Compute the visual bounding box given the ASS anchor + alignment.

    anchor_x/anchor_y are the ASS \\pos coordinates; alignment is the ASS an
    value. The box is expanded by outline/shadow/padding so collision detection
    uses the true visual footprint."""
    stroke = int(style.outline_width or 0)
    shadow_off = int(style.shadow_offset or 0) if style.shadow else 0
    pad = int(style.padding or 0)
    extra = stroke + pad
    # account for shadow extending to bottom-right
    w = width + 2 * extra + shadow_off
    h = height + 2 * extra + shadow_off
    cx = anchor_x
    # vertical placement depends on ASS alignment
    if alignment in (8, 7, 9):  # top
        top = anchor_y
    elif alignment in (4, 5, 6):  # middle
        top = anchor_y - h // 2
    else:  # bottom (1,2,3)
        top = anchor_y - h
    # horizontal: center for 7,8,9 / 4,5,6 / 1,2,3 -> center column
    left = cx - w // 2
    return BoundingBox(int(left), int(top), int(w), int(h))


# --------------------------------------------------------------------------- #
# ASS alignment helpers
# --------------------------------------------------------------------------- #
def alignment_for(preferred: str) -> int:
    """Map a preferred position name to an ASS \\an value."""
    p = (preferred or "").lower()
    if p in ("top", "top_center", "topcenter"):
        return 8
    if p in ("middle_center", "center", "middle"):
        return 5
    if p in ("bottom", "bottom_center", "bottomcenter"):
        return 2
    return 2


# --------------------------------------------------------------------------- #
# candidate position generation per text type
# --------------------------------------------------------------------------- #
def _candidate_anchors(element: TextElement, canvas_w: int, canvas_h: int,
                       safe: SafeArea, fit: dict, style: TextStyle) -> list:
    """Generate (alignment, anchor_x, anchor_y, score_base) candidates.

    The layout engine picks the best-scoring non-colliding candidate."""
    cx = canvas_w // 2
    h = fit["height"]
    lh = fit["line_height"]
    cands = []
    t = element.type

    # honor explicit normalized preferred position when present
    px = int(element.preferred_x * canvas_w) if element.preferred_x is not None else cx
    py = element.preferred_y * canvas_h if element.preferred_y is not None else None

    if t in (TextType.HOOK, TextType.TITLE):
        al = alignment_for(style.alignment or "top_center")
        # step downward from top safe margin
        top0 = safe.top + 20
        if py is not None:
            cands.append((al, px, int(py), 1.0))
        for i, off in enumerate(range(0, 360, 40)):
            y = top0 + off + lh // 2 if al == 8 else top0 + off
            cands.append((al, cx, y, 1.0 - 0.02 * i))
    elif t == TextType.CAPTION:
        al = alignment_for(style.alignment or "bottom_center")
        # step upward from the bottom safe margin
        bot0 = canvas_h - safe.bottom - 20
        if py is not None:
            cands.append((al, px, int(py), 1.0))
        for i, off in enumerate(range(0, 360, 40)):
            y = bot0 - off - h + lh if al != 2 else bot0 - off
            cands.append((al, cx, y, 1.0 - 0.02 * i))
    elif t == TextType.SUBTITLE:
        al = alignment_for(style.alignment or "bottom_center")
        bot0 = canvas_h - safe.bottom - 20
        for i, off in enumerate(range(0, 300, 40)):
            y = bot0 - off - h if al != 2 else bot0 - off
            cands.append((al, cx, y, 1.0 - 0.02 * i))
    elif t == TextType.CTA:
        al = alignment_for(style.alignment or "bottom_center")
        # reserved strip just above the very bottom safe line. Step upward
        # through several candidates so the CTA can escape a large caption
        # region that already occupies the bottom band.
        bot0 = canvas_h - safe.bottom - 40
        for i, off in enumerate(range(0, 520, 60)):
            y = bot0 - off
            cands.append((al, cx, y, 1.0 - 0.04 * i))
    elif t == TextType.WATERMARK:
        al = 7  # top-left
        cands.append((al, safe.left + 20, safe.top + 20, 1.0))
        al = 9  # top-right
        cands.append((al, canvas_w - safe.right - 20, safe.top + 20, 0.95))
    else:
        al = alignment_for(style.alignment)
        cands.append((al, cx, canvas_h // 2, 1.0))
    return cands


# --------------------------------------------------------------------------- #
# collision + subject-aware scoring
# --------------------------------------------------------------------------- #
def _overlap_area(a: BoundingBox, b: BoundingBox) -> int:
    if not a.intersects(b):
        return 0
    ix = max(0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    iy = max(0, min(a.y + a.height, b.y + b.height) - max(a.y, b.y))
    return ix * iy


def _subject_penalty(box: BoundingBox, subject: Optional[BoundingBox]) -> float:
    if subject is None:
        return 0.0
    ov = _overlap_area(box, subject)
    if ov <= 0:
        return 0.0
    return ov / float(max(1, subject.width * subject.height))


def _score_candidate(box: BoundingBox, safe: SafeArea, existing: list,
                     subject: Optional[BoundingBox], base: float) -> float:
    score = base
    if not safe.contains(box):
        score -= 1.0
    for ex in existing:
        if box.intersects(ex["box"]):
            score -= 0.6
    score -= 0.5 * _subject_penalty(box, subject)
    return score


# --------------------------------------------------------------------------- #
# main layout API
# --------------------------------------------------------------------------- #
def layout_element(element: TextElement, style: TextStyle, canvas_w: int,
                   canvas_h: int, safe: SafeArea, existing: list,
                   subject: Optional[BoundingBox] = None,
                   representative_text: Optional[str] = None) -> Optional[LayoutResult]:
    """Lay out one element. `existing` is a list of {id, box} already placed.

    For CAPTION elements, pass representative_text = the widest caption line so
    the region is sized to fit every timed line that will appear there."""
    text = (representative_text or element.text or "").strip()
    max_w = style.max_width or (canvas_w - safe.left - safe.right)
    fit = fit_text(text, style, max_w, style.max_lines)
    if not fit["lines"]:
        return None
    cands = _candidate_anchors(element, canvas_w, canvas_h, safe, fit, style)
    best = None
    for al, ax, ay, base in cands:
        box = text_bounding_box(fit["lines"], fit["font_size"],
                                fit["line_height"], fit["width"],
                                fit["height"], style, ax, ay, al)
        sc = _score_candidate(box, safe, existing, subject, base)
        if best is None or sc > best[1]:
            best = ((al, ax, ay), sc, box)
    if best is None:
        return None
    (al, ax, ay), sc, box = best
    return LayoutResult(
        element_id=element.id, type=element.type, text=text,
        lines=fit["lines"], font_size=fit["font_size"],
        line_height=fit["line_height"], alignment=al,
        anchor_x=ax, anchor_y=ay, bounding_box=box, score=sc, style=style)


def layout_frame(elements: list, canvas_w: int = CANVAS_W,
                 canvas_h: int = CANVAS_H, safe: Optional[SafeArea] = None,
                 subject: Optional[BoundingBox] = None) -> list:
    """Lay out a full set of elements in priority order, avoiding collisions.

    WATERMARK is placed first (fixed), then HOOK/TITLE, then CAPTION, then CTA.
    Returns a list of LayoutResult in the same element order as the input."""
    safe = safe or SafeArea()
    order = {TextType.WATERMARK: 0, TextType.HOOK: 1, TextType.TITLE: 1,
             TextType.CTA: 3, TextType.SUBTITLE: 4, TextType.CAPTION: 2,
             TextType.HIGHLIGHT: 5}
    indexed = sorted(enumerate(elements), key=lambda iv: order.get(iv[1].type, 9))
    placed = []  # {id, box}
    results_by_idx = {}
    for idx, el in indexed:
        rep = None
        if el.type == TextType.CAPTION and isinstance(el.text, list):
            # caption region sized to the widest line
            rep = max(el.text, key=lambda s: len(s)) if el.text else None
            txt_for_el = rep or ""
        else:
            txt_for_el = el.text
        # support list text (caption lines) by sizing to the widest
        if isinstance(txt_for_el, list):
            rep = max(txt_for_el, key=lambda s: len(s)) if txt_for_el else ""
            txt_for_el = rep or ""
        res = layout_element(el, el.style, canvas_w, canvas_h, safe,
                             placed, subject, representative_text=txt_for_el
                             if el.type == TextType.CAPTION else None)
        if res is not None:
            placed.append({"id": el.id, "box": res.bounding_box})
        results_by_idx[idx] = res
    return [results_by_idx.get(i) for i in range(len(elements))]


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_layout(results: list, canvas_w: int = CANVAS_W,
                    canvas_h: int = CANVAS_H, safe: Optional[SafeArea] = None,
                    subject: Optional[BoundingBox] = None) -> dict:
    """Validate a finished layout. Returns {valid, issues}."""
    safe = safe or SafeArea()
    issues = []
    seen_ids = set()
    boxes = []
    for r in results:
        if r is None:
            continue
        if r.element_id in seen_ids:
            issues.append("duplicateElement")
        seen_ids.add(r.element_id)
        box = r.bounding_box
        boxes.append((r.element_id, box))
        if box.x < 0 or box.y < 0 or box.x + box.width > canvas_w:
            issues.append("horizontalOverflow")
        if box.y + box.height > canvas_h:
            issues.append("verticalOverflow")
        if not safe.contains(box):
            issues.append("safeZoneViolation")
        if r.type in (TextType.HOOK, TextType.TITLE) and len(r.lines) > 3:
            issues.append("tooManyLines")
        if r.font_size < (r.style.min_font_size if r.style else 40):
            issues.append("minimumFontViolation")
        if subject is not None and _overlap_area(box, subject) > 0.6 * (
                subject.width * subject.height):
            issues.append("subjectCollision")
    # pairwise collisions
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i][1].intersects(boxes[j][1]):
                issues.append("textCollision")
    return {"valid": len(issues) == 0, "issues": sorted(set(issues))}


# --------------------------------------------------------------------------- #
# debug overlay
# --------------------------------------------------------------------------- #
def render_debug_overlay(results: list, canvas_w: int = CANVAS_W,
                         canvas_h: int = CANVAS_H, safe: Optional[SafeArea] = None,
                         subject: Optional[BoundingBox] = None,
                         out_path: Optional[Path] = None) -> Optional[Path]:
    """Draw safe zones, bounding boxes, subject + ids onto a debug frame image."""
    from PIL import Image, ImageDraw, ImageFont
    safe = safe or SafeArea()
    img = Image.new("RGB", (canvas_w, canvas_h), (24, 24, 28))
    d = ImageDraw.Draw(img, "RGBA")
    # safe zone
    d.rectangle([safe.left, safe.top,
                 canvas_w - safe.right, canvas_h - safe.bottom],
                outline=(80, 200, 120, 200), width=4)
    d.text((safe.left + 10, safe.top + 6), "TOP SAFE",
           fill=(120, 220, 140, 220))
    d.text((safe.left + 10, canvas_h - safe.bottom - 40), "BOTTOM SAFE",
           fill=(120, 220, 140, 220))
    if subject is not None:
        d.rectangle([subject.x, subject.y, subject.x + subject.width,
                     subject.y + subject.height],
                    outline=(220, 120, 200, 180), width=6)
        d.text((subject.x + 6, subject.y + 4), "SUBJECT",
               fill=(240, 160, 220, 220))
    fnt = ImageFont.load_default()
    for r in results:
        if r is None:
            continue
        b = r.bounding_box
        d.rectangle([b.x, b.y, b.x + b.width, b.y + b.height],
                    outline=(90, 170, 255, 220), width=4)
        label = f"{r.element_id} | {r.type.value} | fs={r.font_size} lines={len(r.lines)}"
        try:
            d.text((b.x + 4, max(0, b.y - 22)), label, fill=(160, 200, 255, 230))
        except Exception:
            pass
    out_path = Path(out_path) if out_path else (config.previews_dir / "layout_debug.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path))
    return out_path


# --------------------------------------------------------------------------- #
# convenience: build TextElement list from a ClipForge template + clip data
# --------------------------------------------------------------------------- #
def style_from_template_block(block: dict, default_font: str = "Poppins-Bold") -> TextStyle:
    """Translate a template hook/captions/cta block into a TextStyle."""
    font, _ = _parse_font_name(block.get("font", default_font))
    max_w = block.get("max_width")
    return TextStyle(
        font=font,
        font_size=int(block.get("size", 80)),
        color=block.get("color", "#FFFFFF"),
        outline_color=block.get("outline_color", "#000000"),
        outline_width=int(block.get("outline_width", 4)),
        shadow=bool(block.get("shadow", False)),
        shadow_offset=int(block.get("shadow_offset", 4)),
        alignment=block.get("position", block.get("alignment", "bottom_center")),
        max_width=int(max_w) if max_w else None,
        max_lines=int(block.get("max_lines", block.get("max_words", 3))),
        min_font_size=int(block.get("min_font_size", 48)),
        max_font_size=int(block.get("max_font_size", 130)),
        line_height_mult=float(block.get("line_height_mult", 1.08)),
        letter_spacing=int(block.get("letter_spacing", 0)),
        background=block.get("background"),
        padding=int(block.get("padding", 0)),
    )


def _parse_font_name(name: str):
    """Match apply_template._parse_font without importing it (avoids cycles)."""
    for suffix in ("-BoldItalic", "-Bold", " Bold", "-Regular"):
        if name.endswith(suffix):
            base = name[:-len(suffix)]
            return base if base else name, -1 if "Bold" in suffix else 0
    return name, 0


def elements_from_template(template: dict, hook_text: Optional[str],
                           caption_texts: Optional[list] = None,
                           cta_text: Optional[str] = None) -> list:
    """Build the canonical TextElement list for one clip from a ClipForge
    template. caption_texts is the list of wrapped caption line strings that
    will appear (timed) in the caption region; the widest one sizes the region."""
    elements = []
    hook = template.get("hook", {}) or {}
    if hook.get("enabled") and hook_text:
        elements.append(TextElement(
            id="hook_001", type=TextType.HOOK, text=str(hook_text),
            style=style_from_template_block(hook, "Bebas Neue")))
    caps = template.get("captions", {}) or {}
    if caps.get("enabled") and caption_texts:
        texts = [str(t) for t in caption_texts if str(t).strip()]
        elements.append(TextElement(
            id="caption_001", type=TextType.CAPTION, text=texts,
            style=style_from_template_block(caps, "Poppins-Bold")))
    cta = template.get("cta", {}) or {}
    cta_txt = cta_text or (cta.get("text") if cta.get("enabled") else None)
    if cta.get("enabled") and cta_txt:
        elements.append(TextElement(
            id="cta_001", type=TextType.CTA, text=str(cta_txt),
            style=style_from_template_block(cta, "Poppins-Bold")))
    return elements


def layout_template(template: dict, hook_text: Optional[str],
                    caption_texts: Optional[list] = None,
                    cta_text: Optional[str] = None,
                    subject: Optional[BoundingBox] = None,
                    safe: Optional[SafeArea] = None,
                    canvas_w: int = CANVAS_W, canvas_h: int = CANVAS_H) -> list:
    """End-to-end: template + clip text -> [LayoutResult] ready for the
    renderer. This is the single entry point both the Style Explorer previews
    and the final export use, guaranteeing layout consistency."""
    elements = elements_from_template(template, hook_text, caption_texts, cta_text)
    return layout_frame(elements, canvas_w, canvas_h, safe or SafeArea(), subject)
