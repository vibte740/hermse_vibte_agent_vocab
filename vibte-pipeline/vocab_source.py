#!/usr/bin/env python3
"""
Vocab Source — Oxford 3000/5000 CEFR-leveled word list + Free Dictionary API.

Chains two free sources to produce episode-ready dicts:
1. Oxford 5000 CSV (word, level, pos) from GitHub
2. Free Dictionary API (definition, example sentences)

Usage:
  from vocab_source import VocabSource
  vs = VocabSource()
  episode = vs.next_episode(exclude_ids=set(), target_level="B2")
"""

import csv
import io
import json
import logging
import os
import random
import time

import requests

log = logging.getLogger("vocab_source")

OXFORD_CSV_URL = "https://raw.githubusercontent.com/nalgeon/words/main/data/oxford-5k.csv"
LOCAL_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oxford_5k.csv")

DICT_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
DICT_API_BACKUP = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"

LEVEL_MAP = {
    "a1": "LEVEL A1", "a2": "LEVEL A2",
    "b1": "LEVEL B1", "b2": "LEVEL B2",
    "c1": "LEVEL C1", "c2": "LEVEL C2",
}

LEVEL_COLORS = {
    "LEVEL A1": "#4CAF50", "LEVEL A2": "#8BC34A",
    "LEVEL B1": "#FFC107", "LEVEL B2": "#FF9800",
    "LEVEL C1": "#F44336", "LEVEL C2": "#9C27B0",
}

LEVEL_ICONS = {
    "LEVEL A1": "🌱", "LEVEL A2": "🌿",
    "LEVEL B1": "🌳", "LEVEL B2": "🌲",
    "LEVEL C1": "🏔️", "LEVEL C2": "🗻",
}


class VocabSource:
    def __init__(self, csv_path=None, use_cache=True):
        self.csv_path = csv_path or LOCAL_CACHE
        self.use_cache = use_cache
        self.words = []
        self._load_words()

    def _load_words(self):
        if os.path.exists(self.csv_path):
            log.info(f"Loading cached Oxford CSV from {self.csv_path}")
            self._parse_csv(self.csv_path)
            return

        log.info(f"Downloading Oxford 5000 CSV...")
        try:
            r = requests.get(OXFORD_CSV_URL, timeout=30)
            r.raise_for_status()
            if self.use_cache:
                with open(self.csv_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                log.info(f"Cached to {self.csv_path}")
            self._parse_csv(io.StringIO(r.text))
        except requests.RequestException as e:
            log.error(f"Failed to download Oxford CSV: {e}")
            raise RuntimeError(f"Cannot load Oxford word list: {e}")

    def _parse_csv(self, source):
        reader = csv.DictReader(io.open(source, encoding="utf-8") if isinstance(source, str) else source)
        for row in reader:
            raw_level = (row.get("level") or "").strip().lower()
            if raw_level not in LEVEL_MAP:
                continue
            self.words.append({
                "word": row["word"].strip(),
                "level": LEVEL_MAP[raw_level],
                "pos": (row.get("pos") or "noun").strip(),
            })
        log.info(f"Loaded {len(self.words)} Oxford words with CEFR levels")

    def words_by_level(self, level=None):
        if level:
            return [w for w in self.words if w["level"] == level]
        return list(self.words)

    def fetch_definition(self, word, retries=2, delay=1.0):
        """Fetch definition + example from Free Dictionary API."""
        for attempt in range(retries + 1):
            try:
                r = requests.get(DICT_API_URL.format(word=word), timeout=10)
                if r.status_code == 200:
                    return self._parse_dict_response(r.json())
                if r.status_code == 404:
                    return None
                if r.status_code == 429:
                    wait = delay * (2 ** attempt)
                    log.warning(f"Rate limited on '{word}', waiting {wait}s")
                    time.sleep(wait)
                    continue
            except requests.RequestException:
                if attempt < retries:
                    time.sleep(delay)
                    continue
        return None

    def _parse_dict_response(self, data):
        if not isinstance(data, list) or not data:
            return None
        entry = data[0]
        word = entry.get("word", "")
        phonetics = ""
        for p in entry.get("phonetics", []):
            if p.get("text"):
                phonetics = p["text"]
                break

        for meaning in entry.get("meanings", []):
            pos = meaning.get("partOfSpeech", "")
            for d in meaning.get("definitions", []):
                definition = d.get("definition", "")
                example = d.get("example", "")
                if definition:
                    return {
                        "word": word,
                        "pos": pos,
                        "definition": definition,
                        "example": example,
                        "phonetics": phonetics,
                    }
        return None

    def generate_example_fallback(self, word, pos):
        """Simple fallback example sentence when API has none."""
        templates = {
            "noun": f"She showed great {word} in the interview.",
            "verb": f"They decided to {word} the old building.",
            "adjective": f"The result was quite {word}.",
            "adverb": f"He spoke {word} about the issue.",
            "preposition": f"The cat sat {word} the table.",
            "conjunction": f"I wanted to go, {word} it was too late.",
        }
        return templates.get(pos, f"This is an example with the word {word}.")

    def pick_pair(self, target_level=None, exclude_ids=None):
        """Pick two words at the same level for an episode pair."""
        exclude_ids = exclude_ids or set()
        pool = self.words_by_level(target_level)
        if len(pool) < 2:
            pool = self.words
        if len(pool) < 2:
            raise RuntimeError("Not enough words in Oxford list")

        attempts = 0
        while attempts < 50:
            w1, w2 = random.sample(pool, 2)
            ep_id = f"vocab_{w1['word']}_{w2['word']}"
            if ep_id not in exclude_ids and w1["word"] != w2["word"]:
                return w1, w2
            attempts += 1
        return pool[0], pool[1]

    def build_episode(self, w1_info, w2_info, existing_ids=None):
        """Build a full episode dict compatible with vibte_producer."""
        existing_ids = existing_ids or set()
        word1 = w1_info["word"]
        word2 = w2_info["word"]
        ep_id = f"vocab_{word1}_{word2}"

        if ep_id in existing_ids:
            return None

        d1 = self.fetch_definition(word1) or {
            "word": word1, "pos": w1_info["pos"],
            "definition": f"the word {word1}", "example": "",
        }
        d2 = self.fetch_definition(word2) or {
            "word": word2, "pos": w2_info["pos"],
            "definition": f"the word {word2}", "example": "",
        }

        example1 = d1.get("example") or self.generate_example_fallback(word1, d1["pos"])
        example2 = d2.get("example") or self.generate_example_fallback(word2, d2["pos"])

        level1 = w1_info["level"]
        level2 = w2_info["level"]
        display_level = level1 if level1 == level2 else f"{level1} / {level2}"

        subtitle = f"{word1} vs {word2}"
        quiz_q = f"Which word means \"{d1['definition'].split('.')[0]}\"?"
        correct = random.choice([word1, word2])
        wrong = word2 if correct == word1 else word1

        return {
            "id": ep_id,
            "level": display_level,
            "subtitle": subtitle,
            "word1": word1,
            "word1_color": LEVEL_COLORS.get(level1, "#FFFFFF"),
            "word2": word2,
            "word2_color": LEVEL_COLORS.get(level2, "#FFFFFF"),
            "word1_pos": d1["pos"],
            "word1_def": d1["definition"],
            "word1_example": example1,
            "word1_icon": LEVEL_ICONS.get(level1, "📚"),
            "word2_pos": d2["pos"],
            "word2_def": d2["definition"],
            "word2_example": example2,
            "word2_icon": LEVEL_ICONS.get(level2, "📚"),
            "cards_header": display_level,
            "word1_card_sub": f"{d1['pos']}",
            "word1_card_lines": [
                d1["definition"],
                example1,
            ],
            "word2_card_sub": f"{d2['pos']}",
            "word2_card_lines": [
                d2["definition"],
                example2,
            ],
            "quiz_question": quiz_q,
            "result_line1": f"The answer is: {correct}",
            "result_line2": f"{correct} — {d1['definition'] if correct == word1 else d2['definition']}",
            "promo_tagline": "Learn English with Vibte!",
            "tts": f"Let's learn two new words: {word1} and {word2}.",
        }


def build_vocab_episodes(count=1, level=None, exclude_ids=None):
    """Quick helper: generate N episode dicts."""
    vs = VocabSource()
    episodes = []
    exclude = set(exclude_ids or [])
    for _ in range(count):
        w1, w2 = vs.pick_pair(target_level=level, exclude_ids=exclude)
        ep = vs.build_episode(w1, w2, existing_ids=exclude)
        if ep:
            episodes.append(ep)
            exclude.add(ep["id"])
    return episodes


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5, help="Number of episodes to generate")
    parser.add_argument("--level", choices=["LEVEL A1", "LEVEL A2", "LEVEL B1", "LEVEL B2", "LEVEL C1", "LEVEL C2"],
                        help="Restrict to one CEFR level")
    parser.add_argument("--output", default="vocab_episodes.json", help="Output JSON file")
    parser.add_argument("--print-words", action="store_true", help="Print word counts by level")
    args = parser.parse_args()

    vs = VocabSource()

    if args.print_words:
        for lvl in ["LEVEL A1", "LEVEL A2", "LEVEL B1", "LEVEL B2", "LEVEL C1", "LEVEL C2"]:
            count = len(vs.words_by_level(lvl))
            print(f"  {lvl}: {count} words")
        print(f"  Total: {len(vs.words)}")
        exit(0)

    episodes = build_vocab_episodes(count=args.count, level=args.level)
    for ep in episodes:
        print(f"  {ep['id']}: {ep['word1']} ({ep['word1_pos']}) + {ep['word2']} ({ep['word2_pos']})")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(episodes, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {len(episodes)} episodes to {args.output}")
