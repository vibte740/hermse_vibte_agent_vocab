#!/usr/bin/env python3
"""One-shot render to demo_video.mp4 in the workspace."""
import sys, os, json, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from vocab_source import build_vocab_episodes
from vibte_producer import generate_script, run_script

episodes = build_vocab_episodes(count=1)
ep = episodes[0]
ep_id = ep["id"]
slug = ep_id.replace("_", "-")
print(f"Episode: {ep_id} ({ep['level']})")
print(f"Words: {ep['word1']} vs {ep['word2']}")

script = f"C:\\tmp\\vibte_{slug}.py"
video = f"C:\\tmp\\vibte_{slug}.mp4"

generate_script(ep, script)
rc = run_script(script)
print(f"Render exit: {rc}")

if os.path.exists(video):
    sz = os.path.getsize(video)
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_video.mp4")
    shutil.copy2(video, dest)
    print(f"Video: {video} ({sz} bytes)")
    print(f"Copied to: {dest}")
else:
    print("Video not found!")
