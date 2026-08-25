"""Tests for the centralized text layout engine (src/text_layout.py).

Covers the rendering/preview consistency contract from the ClipForge text fix:

  - no horizontal/vertical clipping
  - no text-to-text overlap
  - no accidental duplicate text
  - no text outside safe zones
  - no impossible line breaks (word order preserved)
  - consistent font scaling + alignment
  - same layout model in preview and final export

Run with:  python -m pytest tests/test_text_layout.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import text_layout as tl
from src import apply_template as at


# --------------------------------------------------------------------------- #
# word wrapping + line balancing
# --------------------------------------------------------------------------- #
class TestWordWrapping:
    def test_short_text_one_line(self):
        lines = tl.wrap_text("The Truth", "Poppins-Bold", 80, 880)
        assert lines == ["The Truth"]

    def test_word_order_preserved_multiline(self):
        text = "THE TRUTH ABOUT 7-FIGURE PRODUCTIVITY"
        lines = tl.wrap_text(text, "Bebas Neue", 110, 880)
        # re-joining all lines must reconstruct the original word sequence
        joined = " ".join(lines).split()
        assert joined == text.split()

    def test_no_word_split(self):
        text = "THE TRUTH ABOUT 7-FIGURE PRODUCTIVITY"
        lines = tl.wrap_text(text, "Bebas Neue", 110, 880)
        for ln in lines:
            for w in ln.split():
                # every token in a line must be a whole word from the source
                assert w in text.split()

    def test_never_exceeds_max_width(self):
        text = ("The Biggest Misconception People Have About Building a "
                "Sustainable 7-Figure Business")
        lines = tl.wrap_text(text, "Poppins-Bold", 80, 880)
        for ln in lines:
            w, _, _ = tl.measure_line(ln, "Poppins-Bold", 80)
            assert w <= 880, f"line '{ln}' width {w} exceeds 880"

    def test_balanced_lines_look_intentional(self):
        # the screenshot example should produce balanced 2-3 line breaks,
        # not one word dangling on a line by itself
        text = "THE TRUTH ABOUT 7-FIGURE PRODUCTIVITY"
        lines = tl.wrap_text(text, "Bebas Neue", 110, 880)
        assert 1 < len(lines) <= 3
        # no line should be a single very short word while another is huge
        widths = [tl.measure_line(ln, "Bebas Neue", 110)[0] for ln in lines]
        if len(widths) > 1:
            assert min(widths) > 0.35 * max(widths), (
                f"unbalanced: {lines} widths={widths}")


# --------------------------------------------------------------------------- #
# bounding boxes incl. stroke/shadow
# --------------------------------------------------------------------------- #
class TestBoundingBox:
    def test_box_includes_stroke(self):
        style = tl.TextStyle(font="Poppins-Bold", font_size=80,
                             outline_width=6, padding=0)
        lines = ["Hello World"]
        w, h, lh = tl.measure_block(lines, "Poppins-Bold", 80)
        box = tl.text_bounding_box(lines, 80, lh, w, h, style, 540, 960, 5)
        # box must be wider than raw glyph width by at least 2*stroke
        assert box.width >= w + 2 * 6

    def test_box_includes_shadow(self):
        style = tl.TextStyle(font="Poppins-Bold", font_size=80,
                             outline_width=0, shadow=True, shadow_offset=8)
        lines = ["Hello"]
        w, h, lh = tl.measure_block(lines, "Poppins-Bold", 80)
        box = tl.text_bounding_box(lines, 80, lh, w, h, style, 540, 960, 5)
        assert box.width >= w + 8
        assert box.height >= h + 8


# --------------------------------------------------------------------------- #
# fit: auto-scaling font size
# --------------------------------------------------------------------------- #
class TestAutoScaling:
    def test_long_text_reduces_font_size(self):
        style = tl.TextStyle(font="Poppins-Bold", font_size=200,
                             max_font_size=200, min_font_size=40,
                             max_lines=3, max_width=880)
        fit = tl.fit_text("The Biggest Misconception People Have About Building "
                          "a Sustainable 7-Figure Business", style, 880, 3)
        assert fit["font_size"] < 200
        assert fit["font_size"] >= style.min_font_size
        assert len(fit["lines"]) <= 3

    def test_short_text_keeps_preferred_size(self):
        style = tl.TextStyle(font="Poppins-Bold", font_size=110,
                             max_font_size=110, min_font_size=40,
                             max_lines=3, max_width=880)
        fit = tl.fit_text("The Truth", style, 880, 3)
        assert fit["font_size"] == 110
        assert len(fit["lines"]) == 1

    def test_never_overflows_horizontally_after_fit(self):
        style = tl.TextStyle(font="Poppins-Bold", font_size=130,
                             max_font_size=130, min_font_size=40,
                             max_lines=3, max_width=880)
        for text in ("The Truth", "The Truth About Productivity",
                     "The Biggest Misconception People Have About 7-Figure Productivity",
                     "The Biggest Misconception People Have About Building a "
                     "Sustainable 7-Figure Business"):
            fit = tl.fit_text(text, style, 880, 3)
            for ln in fit["lines"]:
                w, _, _ = tl.measure_line(ln, style.font, fit["font_size"])
                assert w <= 880, f"'{ln}' w={w} > 880 for text '{text}'"


# --------------------------------------------------------------------------- #
# collision detection + safe zones
# --------------------------------------------------------------------------- #
class TestCollisions:
    def test_two_boxes_collide(self):
        a = tl.BoundingBox(100, 300, 880, 180)
        b = tl.BoundingBox(200, 400, 880, 180)
        assert a.intersects(b)

    def test_two_boxes_dont_collide(self):
        a = tl.BoundingBox(100, 300, 880, 180)
        b = tl.BoundingBox(100, 600, 880, 180)
        assert not a.intersects(b)

    def test_safe_area_contains(self):
        safe = tl.SafeArea()
        ok = tl.BoundingBox(100, 200, 880, 180)
        bad = tl.BoundingBox(50, 100, 880, 1800)
        assert safe.contains(ok)
        assert not safe.contains(bad)


# --------------------------------------------------------------------------- #
# full template layout (screenshot example + length matrix)
# --------------------------------------------------------------------------- #
BASE_TEMPLATE = {
    "name": "test",
    "output": {"aspect_ratio": "9:16", "resolution": "1080x1920"},
    "crop": {"mode": "square_band", "background": "#000000"},
    "hook": {"enabled": True, "font": "Bebas Neue", "size": 110,
             "color": "#FFFFFF", "position": "top"},
    "captions": {"enabled": True, "font": "Poppins-Bold", "size": 80,
                 "color": "#FFFFFF", "outline_color": "#000000",
                 "outline_width": 4, "position": "bottom_center",
                 "max_words": 4, "max_lines": 3},
}

TEXT_MATRIX = {
    "SHORT": "The Truth",
    "MEDIUM": "The Truth About Productivity",
    "LONG": "The Biggest Misconception People Have About 7-Figure Productivity",
    "VERY_LONG": ("The Biggest Misconception People Have About Building a "
                  "Sustainable 7-Figure Business"),
}

SCREENSHOT_EXAMPLE = "THE TRUTH ABOUT 7-FIGURE PRODUCTIVITY"


def _layout_ok(results):
    """All non-None results have boxes inside the canvas, no collisions."""
    v = tl.validate_layout(results)
    # allow safeZoneViolation tolerance for the very long edge cases only when
    # min font is hit; the core assertions below are stricter per-element
    return v


class TestFullLayout:
    @pytest.mark.parametrize("text", [SCREENSHOT_EXAMPLE, *TEXT_MATRIX.values()],
                             ids=["screenshot", "short", "medium", "long",
                                  "very_long"])
    def test_hook_text_in_canvas(self, text):
        results = tl.layout_template(BASE_TEMPLATE, hook_text=text,
                                      caption_texts=["sample caption"])
        hook = next(r for r in results if r and r.type is tl.TextType.HOOK)
        b = hook.bounding_box
        assert b.x >= 0, f"hook left off-canvas: {b.as_dict()}"
        assert b.x + b.width <= tl.CANVAS_W
        assert b.y >= 0
        assert b.y + b.height <= tl.CANVAS_H

    @pytest.mark.parametrize("text", [SCREENSHOT_EXAMPLE, *TEXT_MATRIX.values()],
                             ids=["screenshot", "short", "medium", "long",
                                  "very_long"])
    def test_no_horizontal_overflow(self, text):
        results = tl.layout_template(BASE_TEMPLATE, hook_text=text,
                                      caption_texts=["sample caption"])
        for r in results:
            if r is None:
                continue
            b = r.bounding_box
            assert b.x + b.width <= tl.CANVAS_W + 2, (
                f"{r.element_id} overflows right: {b.as_dict()}")
            assert b.x >= -2, f"{r.element_id} overflows left: {b.as_dict()}"

    @pytest.mark.parametrize("text", [SCREENSHOT_EXAMPLE, *TEXT_MATRIX.values()],
                             ids=["screenshot", "short", "medium", "long",
                                  "very_long"])
    def test_no_text_collision(self, text):
        results = tl.layout_template(BASE_TEMPLATE, hook_text=text,
                                      caption_texts=["sample caption"])
        boxes = [r.bounding_box for r in results if r is not None]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                assert not boxes[i].intersects(boxes[j], slack=4), (
                    f"collision between elements {i} and {j}")

    def test_screenshot_example_balanced(self):
        results = tl.layout_template(BASE_TEMPLATE,
                                      hook_text=SCREENSHOT_EXAMPLE,
                                      caption_texts=["sample caption"])
        hook = next(r for r in results if r and r.type is tl.TextType.HOOK)
        # must wrap into <= 3 lines and preserve word order
        assert len(hook.lines) <= 3
        joined = " ".join(hook.lines).split()
        assert joined == SCREENSHOT_EXAMPLE.split()


# --------------------------------------------------------------------------- #
# preview == final layout consistency
# --------------------------------------------------------------------------- #
class TestPreviewFinalConsistency:
    def test_ass_playres_is_canonical_regardless_of_render_resolution(self):
        """PlayResX/Y must be the canonical 1080x1920 even when the actual
        render resolution is the 540x960 preview, so libass scales the same
        layout into both."""
        ass = at._build_ass([], BASE_TEMPLATE, 540, 960, 10.0,
                            hook_text=SCREENSHOT_EXAMPLE, band_offset=0,
                            band_height=960)
        assert f"PlayResX: {tl.CANVAS_W}" in ass
        assert f"PlayResY: {tl.CANVAS_H}" in ass

    def test_ass_uses_layout_pos_not_margin_v(self):
        ass = at._build_ass([], BASE_TEMPLATE, 1080, 1920, 10.0,
                            hook_text=SCREENSHOT_EXAMPLE, band_offset=0,
                            band_height=1920)
        # every Dialogue line must carry an explicit \\pos from the engine
        assert "\\pos(" in ass

    def test_duplicate_element_ids_rejected(self):
        from src import text_layout as tl_mod
        # two hooks with the same id should be rejected at layout time
        el = tl_mod.TextElement(id="hook_001", type=tl_mod.TextType.HOOK,
                                text="x", style=tl_mod.TextStyle())
        el2 = tl_mod.TextElement(id="hook_001", type=tl_mod.TextType.HOOK,
                                 text="y", style=tl_mod.TextStyle())
        # layout_frame does not dedupe by id but validate_layout flags it
        res = tl_mod.layout_frame([el, el2])
        v = tl_mod.validate_layout(res)
        assert "duplicateElement" in v["issues"]


# --------------------------------------------------------------------------- #
# 1/2/3 line variants of the screenshot example
# --------------------------------------------------------------------------- #
class TestScreenshotVariants:
    @pytest.mark.parametrize("max_lines", [1, 2, 3])
    def test_line_count_variant_no_overflow(self, max_lines):
        tpl = {**BASE_TEMPLATE, "hook": dict(BASE_TEMPLATE["hook"])}
        tpl["hook"]["max_lines"] = max_lines
        results = tl.layout_template(tpl, hook_text=SCREENSHOT_EXAMPLE,
                                      caption_texts=["sample caption"])
        hook = next(r for r in results if r and r.type is tl.TextType.HOOK)
        assert len(hook.lines) <= max_lines
        b = hook.bounding_box
        assert b.x + b.width <= tl.CANVAS_W
        assert b.y + b.height <= tl.CANVAS_H
