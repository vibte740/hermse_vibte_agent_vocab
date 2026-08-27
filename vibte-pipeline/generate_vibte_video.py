#!/usr/bin/env python3
"""
Vibte Video Template — renders a vocabulary learning video from EPISODE dict.
Uses Pillow for text + edge-tts for narration + moviepy for video assembly.
"""
import asyncio
import json
import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
import edge_tts

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

TTS_VOICE = "en-US-GuyNeural"


def _font(size, bold=False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + name, size)
        except OSError:
            return ImageFont.load_default()


def _draw(draw, text, xy, size, color, bold=False, center=False, max_width=None):
    font = _font(size, bold)
    if center and max_width:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = xy[0] + (max_width - tw) // 2
        draw.text((x, xy[1]), text, fill=color, font=font)
    else:
        draw.text(xy, text, fill=color, font=font)


def _draw_wrapped(draw, text, xy, size, color, max_width, bold=False):
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
    line_h = size * 1.3
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = xy[0] + (max_width - tw) // 2
        draw.text((x, y), line, fill=color, font=font)
        y += line_h
    return y


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


async def _tts(text, out_path):
    comm = edge_tts.Communicate(text, TTS_VOICE)
    await comm.save(out_path)


def tts(text, out_path):
    asyncio.run(_tts(text, out_path))


def _audio_duration(path):
    clip = AudioFileClip(path)
    dur = clip.duration
    clip.close()
    return dur


def _render_frame(render_fn):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    render_fn(draw)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    tmp.close()
    return tmp.name


def title_scene():
    level = EPISODE.get("level", "LEVEL B1")
    subtitle = EPISODE.get("subtitle", "word1 vs word2")
    word1 = EPISODE.get("word1", "")
    word2 = EPISODE.get("word2", "")

    narration = f"Let's learn two new words: {word1} and {word2}."
    audio = os.path.join(tempfile.gettempdir(), "vibte_title.mp3")
    tts(narration, audio)
    duration = _audio_duration(audio) + 0.3

    frame = _render_frame(lambda draw: (
        draw.rectangle([(0, 0), (W, H)], fill=BG),
        _draw(draw, level, (0, int(H * 0.35)), 72, ACCENT, bold=True, center=True, max_width=W),
        _draw(draw, subtitle, (0, int(H * 0.48)), 54, WHITE, center=True, max_width=W),
        _draw(draw, "Vibte English", (0, int(H * 0.60)), 40, GRAY, center=True, max_width=W),
    ))

    clip = ImageClip(frame).set_duration(duration)
    clip = clip.set_audio(AudioFileClip(audio))
    return clip


def word_card_scene(word, pos, definition, example, icon, color, card_sub, card_lines):
    color_rgb = _hex_to_rgb(color) if isinstance(color, str) else color

    narration = f"{word}. {pos}. {definition}."
    if example:
        narration += f" Example: {example}"
    audio = os.path.join(tempfile.gettempdir(), f"vibte_{word}.mp3")
    tts(narration, audio)
    duration = _audio_duration(audio) + 0.3

    def render(draw):
        draw.rectangle([(0, 0), (W, H)], fill=BG)
        _draw(draw, icon, (0, int(H * 0.10)), 100, WHITE, center=True, max_width=W)
        _draw(draw, word.upper(), (0, int(H * 0.25)), 80, color_rgb, bold=True, center=True, max_width=W)
        _draw(draw, f"({pos})", (0, int(H * 0.34)), 44, GRAY, center=True, max_width=W)
        _draw_wrapped(draw, definition, (50, int(H * 0.44)), 42, WHITE, W - 100)
        if example:
            _draw_wrapped(draw, f'"{example}"', (50, int(H * 0.62)), 34, (200, 200, 200), W - 120)

    frame = _render_frame(render)
    clip = ImageClip(frame).set_duration(duration)
    clip = clip.set_audio(AudioFileClip(audio))
    return clip


def quiz_scene():
    question = EPISODE.get("quiz_question", "Which word fits?")
    word1 = EPISODE.get("word1", "?")
    word2 = EPISODE.get("word2", "?")

    narration = f"Quick quiz! {question}"
    audio = os.path.join(tempfile.gettempdir(), "vibte_quiz.mp3")
    tts(narration, audio)
    duration = _audio_duration(audio) + 0.3

    def render(draw):
        draw.rectangle([(0, 0), (W, H)], fill=QUIZ_BG)
        _draw(draw, "Quick Quiz", (0, int(H * 0.25)), 64, ACCENT, bold=True, center=True, max_width=W)
        _draw_wrapped(draw, question, (60, int(H * 0.42)), 44, WHITE, W - 120)
        _draw(draw, word1.upper(), (0, int(H * 0.62)), 56, ACCENT, bold=True, center=True, max_width=W)
        _draw(draw, word2.upper(), (0, int(H * 0.72)), 56, ACCENT, bold=True, center=True, max_width=W)

    frame = _render_frame(render)
    clip = ImageClip(frame).set_duration(duration)
    clip = clip.set_audio(AudioFileClip(audio))
    return clip


def result_scene():
    line1 = EPISODE.get("result_line1", "The answer is...")
    line2 = EPISODE.get("result_line2", "")
    promo = EPISODE.get("promo_tagline", "Learn English with Vibte!")

    narration = f"{line1}. {line2}. {promo}"
    audio = os.path.join(tempfile.gettempdir(), "vibte_result.mp3")
    tts(narration, audio)
    duration = _audio_duration(audio) + 0.3

    def render(draw):
        draw.rectangle([(0, 0), (W, H)], fill=RESULT_BG)
        _draw(draw, line1, (0, int(H * 0.38)), 52, WHITE, bold=True, center=True, max_width=W)
        _draw_wrapped(draw, line2, (60, int(H * 0.50)), 40, (200, 230, 200), W - 100)
        _draw(draw, promo, (0, int(H * 0.65)), 36, ACCENT, center=True, max_width=W)

    frame = _render_frame(render)
    clip = ImageClip(frame).set_duration(duration)
    clip = clip.set_audio(AudioFileClip(audio))
    return clip


def main():
    if not EPISODE.get("id"):
        print("ERROR: EPISODE dict is empty — nothing to render.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUT_ROOT, exist_ok=True)

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
        audio_codec="aac",
        preset="ultrafast",
        threads=4,
    )

    for s in scenes:
        try:
            os.remove(s.filename)
        except:
            pass
        try:
            s.close()
        except:
            pass
    final.close()

    for name in ["vibte_title.mp3", "vibte_quiz.mp3", "vibte_result.mp3",
                  f"vibte_{EPISODE.get('word1', '')}.mp3",
                  f"vibte_{EPISODE.get('word2', '')}.mp3"]:
        try:
            os.remove(os.path.join(tempfile.gettempdir(), name))
        except:
            pass

    print(f"Rendered: {OUT_VIDEO}")


if __name__ == "__main__":
    main()
