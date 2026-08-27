#!/usr/bin/env python3
"""Minimal video template for testing — generates a placeholder video."""
import json
import sys

# EPISODE is injected by vibte_producer.py
EPISODE = {}

OUT_ROOT = "/tmp/vibte_test_pipeline"
OUT_VIDEO = "/tmp/vibte_test.mp4"

if __name__ == "__main__":
    print(f"Template run OK — episode_id: {EPISODE.get('id', 'N/A')}")
    print(f"Words: {EPISODE.get('word1', '?')} + {EPISODE.get('word2', '?')}")
    print(f"Level: {EPISODE.get('level', '?')}")
    sys.exit(0)
