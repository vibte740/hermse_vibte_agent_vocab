#!/usr/bin/env python3
"""Render a test video and save to video/ folder."""
import sys, os, shutil, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from vocab_source import build_vocab_episodes
from vibte_producer import generate_script, run_script

VIDEO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video")
TMP_DIR = "C:\\tmp"
os.makedirs(VIDEO_DIR, exist_ok=True)

episodes = build_vocab_episodes(count=1)
ep = episodes[0]
ep_id = ep["id"]
slug = ep_id.replace("_", "-")

print(f"Episode: {ep_id} ({ep['level']})")
print(f"Words: {ep['word1']} vs {ep['word2']}")

script = os.path.join(TMP_DIR, f"vibte_{slug}.py")
video_tmp = os.path.join(TMP_DIR, f"vibte_{slug}.mp4")

generate_script(ep, script)
rc = run_script(script)

if os.path.exists(video_tmp):
    sz = os.path.getsize(video_tmp)
    dest = os.path.join(VIDEO_DIR, f"{slug}.mp4")
    shutil.move(video_tmp, dest)
    print(f"Saved: {dest} ({sz:,} bytes)")
else:
    print("Video not found!")
