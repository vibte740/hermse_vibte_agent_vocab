#!/usr/bin/env python3
"""
Vibte Video Template — renders a vocabulary learning video from EPISODE dict.
Uses Pillow for text rendering + moviepy for video assembly (no ImageMagick needed).
"""
import json
import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip, concatenate_videoclips

EPISODE = {}

OUT_ROOT = "/tmp/vibte_pipeline"
OUT_VIDEO = "/tmp/vibte_output.mp4"

W, H = 1080, 1920
BG = (18, 18, 30)
ACCENT = (255, 193, 7)
WHITE = (255, 255, 255)
GRAY = (160, 160, 160)
QUIZ_BG = (30, 30, 50)
RESULT_BG = (20, 80, 40)

FRAME_DIR = os.path.join(tempfile.gettempdir(), "vibte_frames")


def _font(size, bold=False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + name, size)
        except OSError:
            return ImageFont.load_default()


def _draw(draw, text, xy, size, color, bold=False, center=False, max_width=None, line_spacing=1.2):
    font = _font(size, bold)
    if center and max_width:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = xy[0] + (max_width - tw) // 2
        draw.text((x, xy[1]), text, fill=color, font=font)
    else:
        draw.text(xy, text, fill=color, font=font)


def _draw_wrapped(draw, text, xy, size, color, max_width, bold=False, line_spacing=1.3):
    font = _font(size, bold)
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    y = xy[1]
    line_h = size * line_spacing
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = xy[0] + (max_width - tw) // 2
        draw.text((x, y), line, fill=color, font=font)
        y += line_h
    return y


def _render_frame(render_fn, duration):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    render_fn(draw)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f.name)
        clip = ImageClip(f.name).set_duration(duration)
    return clip


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def title_scene():
    level = EPISODE.get("level", "LEVEL B1")
    subtitle = EPISODE.get("subtitle", "word1 vs word2")

    def render(draw):
        draw.rectangle([(0, 0), (W, H)], fill=BG)
        _draw(draw, level, (0, int(H * 0.35)), 72, ACCENT, bold=True, center=True, max_width=W)
        _draw(draw, subtitle, (0, int(H * 0.48)), 54, WHITE, center=True, max_width=W)
        _draw(draw, "Vibte English", (0, int(H * 0.60)), 40, GRAY, center=True, max_width=W)

    return _render_frame(render, 2.5)


def word_card_scene(word, pos, definition, example, icon, color, card_sub, card_lines):
    color_rgb = _hex_to_rgb(color) if isinstance(color, str) else color

    def render(draw):
        draw.rectangle([(0, 0), (W, H)], fill=BG)
        _draw(draw, icon, (0, int(H * 0.10)), 100, WHITE, center=True, max_width=W)
        _draw(draw, word.upper(), (0, int(H * 0.25)), 80, color_rgb, bold=True, center=True, max_width=W)
        _draw(draw, f"({pos})", (0, int(H * 0.34)), 44, GRAY, center=True, max_width=W)
        _draw_wrapped(draw, definition, (50, int(H * 0.44)), 42, WHITE, W - 100)
        if example:
            ex_text = f'"{example}"'
            _draw_wrapped(draw, ex_text, (50, int(H * 0.62)), 34, (200, 200, 200), W - 120)

    return _render_frame(render, 5.0)


def quiz_scene():
    question = EPISODE.get("quiz_question", "Which word fits?")
    word1 = EPISODE.get("word1", "?")
    word2 = EPISODE.get("word2", "?")

    def render(draw):
        draw.rectangle([(0, 0), (W, H)], fill=QUIZ_BG)
        _draw(draw, "Quick Quiz", (0, int(H * 0.25)), 64, ACCENT, bold=True, center=True, max_width=W)
        _draw_wrapped(draw, question, (60, int(H * 0.42)), 44, WHITE, W - 120)
        _draw(draw, word1.upper(), (0, int(H * 0.62)), 56, ACCENT, bold=True, center=True, max_width=W)
        _draw(draw, word2.upper(), (0, int(H * 0.72)), 56, ACCENT, bold=True, center=True, max_width=W)

    return _render_frame(render, 4.0)


def result_scene():
    line1 = EPISODE.get("result_line1", "The answer is...")
    line2 = EPISODE.get("result_line2", "")
    promo = EPISODE.get("promo_tagline", "Learn English with Vibte!")

    def render(draw):
        draw.rectangle([(0, 0), (W, H)], fill=RESULT_BG)
        _draw(draw, line1, (0, int(H * 0.38)), 52, WHITE, bold=True, center=True, max_width=W)
        _draw_wrapped(draw, line2, (60, int(H * 0.50)), 40, (200, 230, 200), W - 100)
        _draw(draw, promo, (0, int(H * 0.65)), 36, ACCENT, center=True, max_width=W)

    return _render_frame(render, 4.0)


def main():
    if not EPISODE.get("id"):
        print("ERROR: EPISODE dict is empty — nothing to render.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUT_ROOT, exist_ok=True)
    os.makedirs(FRAME_DIR, exist_ok=True)

    scenes = [
        title_scene(),
        word_card_scene(
            EPISODE["word1"], EPISODE["word1_pos"], EPISODE["word1_def"],
            EPISODE["word1_example"], EPISODE["word1_icon"],
            EPISODE["word1_color"], EPISODE["word1_card_sub"], EPISODE["word1_card_lines"],
        ),
        word_card_scene(
            EPISODE["word2"], EPISODE["word2_pos"], EPISODE["word2_def"],
            EPISODE["word2_example"], EPISODE["word2_icon"],
            EPISODE["word2_color"], EPISODE["word2_card_sub"], EPISODE["word2_card_lines"],
        ),
        quiz_scene(),
        result_scene(),
    ]

    final = concatenate_videoclips(scenes, method="compose")
    final.write_videofile(
        OUT_VIDEO,
        fps=30,
        codec="libx264",
        audio=False,
        preset="ultrafast",
        threads=4,
    )
    for s in scenes:
        try:
            os.remove(s.filename)
        except:
            pass
    final.close()
    print(f"Rendered: {OUT_VIDEO}")


if __name__ == "__main__":
    main()
