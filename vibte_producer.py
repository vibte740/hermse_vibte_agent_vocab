#!/usr/bin/env python3
"""
Vibte Producer — picks next unproduced episode, generates standalone script
from template, renders video, uploads to MEGA Production, publishes to TikTok.

NO REPEATS: Supabase (table `vibte_videos`) is the source of truth for what's
already been produced, checked fresh at the start of every run — so this is
safe across server rebuilds and lets you query production history from
anywhere. A flock-based lock + local state.json fallback cache guard against
a concurrent run or a momentarily-unreachable Supabase.

Env vars required for Supabase:
  SUPABASE_URL          e.g. https://xxxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role key (bypasses RLS; do NOT use anon key)

Changes vs. the original:
  - Supabase table is the authoritative "already produced" ledger, queried
    before every pick and inserted into after every successful upload
  - flock-based lock file so concurrent invocations can't race on episode pick
  - local state.json kept only as an offline fallback (merged with whatever
    Supabase returns) — old titles.txt is imported into it once if found
  - atomic state writes (write to temp file + os.replace)
  - retry w/ backoff on the MEGA upload instead of failing the whole run
  - logging module (console + rotating file) instead of bare print()
  - CLI: --dry-run, --episode ID, --list-remaining, --level LEVEL
  - --vocab to generate episodes from Oxford 5000 + Free Dictionary API
  - explicit validation of required episode fields before generation, with a
    clear error + skip instead of a raw KeyError traceback
  - cleanup runs in a finally block so it happens even on unexpected errors
"""
import argparse
import contextlib
import json
import logging
import logging.handlers
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False  # Windows

import requests

try:
    from vocab_source import VocabSource, build_vocab_episodes
except ImportError:
    VocabSource = None
    build_vocab_episodes = None

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_TABLE = "vibte_videos"

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE, "generate_vibte_video.py")
EPISODES_FILE = os.path.join(BASE, "episodes.json")

HERMES_DIR = os.path.join(os.path.expanduser("~"), ".hermes")
STATE_FILE = os.path.join(HERMES_DIR, "vibte_producer_state.json")
LEGACY_TITLES_FILE = os.path.join(HERMES_DIR, "vibte_video_titles.txt")  # imported once, then unused
LOCK_FILE = os.path.join(HERMES_DIR, "vibte_producer.lock")
LOG_FILE = os.path.join(HERMES_DIR, "vibte_producer.log")

MEGA_BASE = "/Root/tiktok-english/Production"

# Level distribution weights (user-approved): B1 20%, B2 40%, C1 30%, C2 10%
LEVEL_WEIGHTS = {
    "LEVEL B1": 20,
    "LEVEL B2": 40,
    "LEVEL C1": 30,
    "LEVEL C2": 10,
}

REQUIRED_EPISODE_FIELDS = [
    "id", "level", "subtitle", "word1", "word1_color", "word2", "word2_color",
    "word1_pos", "word1_def", "word1_example", "word1_icon",
    "word2_pos", "word2_def", "word2_example", "word2_icon",
    "cards_header", "word1_card_sub", "word1_card_lines",
    "word2_card_sub", "word2_card_lines", "quiz_question",
    "result_line1", "result_line2", "promo_tagline", "tts",
]

os.makedirs(HERMES_DIR, exist_ok=True)

log = logging.getLogger("vibte_producer")


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console = logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
    )
    console.setFormatter(fmt)
    log.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)


# ── Locking (prevents two overlapping runs from picking the same episode) ──
@contextlib.contextmanager
def producer_lock():
    fd = open(LOCK_FILE, "w")
    if HAS_FCNTL:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("Another producer run is already in progress (lock held). Exiting.")
            fd.close()
            sys.exit(3)
    else:
        # Windows: check if lock file has a recent PID
        try:
            existing = open(LOCK_FILE).read().strip()
            if existing:
                pid = int(existing)
                # Check if process is still alive
                try:
                    os.kill(pid, 0)
                    log.error("Another producer run is already in progress (lock held). Exiting.")
                    fd.close()
                    sys.exit(3)
                except OSError:
                    pass  # Process dead, we can proceed
        except (ValueError, OSError):
            pass
        fd.write(str(os.getpid()))
        fd.flush()
    try:
        yield
    finally:
        if HAS_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


# ── State (single source of truth) ──
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
                state.setdefault("completed", [])
                return state
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"State file unreadable ({e}); starting fresh but NOT deleting the old file.")
    state = {"completed": []}
    # One-time import from the legacy titles ledger, if present, so we never
    # lose no-repeat history from before this version.
    if os.path.exists(LEGACY_TITLES_FILE):
        with open(LEGACY_TITLES_FILE) as f:
            legacy = {line.strip() for line in f if line.strip()}
        state["completed"] = sorted(legacy)
        log.info(f"Imported {len(legacy)} titles from legacy ledger into state.json")
    return state


def save_state(state):
    """Atomic write: temp file + os.replace, so a crash mid-write can't corrupt state."""
    tmp = STATE_FILE + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


def mark_completed(state, ep_id):
    if ep_id not in state["completed"]:
        state["completed"].append(ep_id)
    save_state(state)


# ── Supabase (authoritative ledger of produced videos) ──
def supabase_configured():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def fetch_completed_from_supabase():
    """Return the set of episode_ids already recorded in Supabase, paginated.
    Returns None (not an empty set) if Supabase is unreachable/misconfigured,
    so callers can tell 'no rows yet' apart from 'couldn't check'."""
    if not supabase_configured():
        log.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — skipping Supabase check, using local state only.")
        return None

    ids = set()
    page_size = 1000
    offset = 0
    try:
        while True:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
                headers=_supabase_headers(),
                params={"select": "episode_id", "offset": offset, "limit": page_size},
                timeout=15,
            )
            r.raise_for_status()
            rows = r.json()
            ids.update(row["episode_id"] for row in rows)
            if len(rows) < page_size:
                break
            offset += page_size
    except (requests.RequestException, ValueError) as e:
        log.warning(f"Could not reach Supabase ({e}) — falling back to local state only.")
        return None
    return ids


def insert_video_record(episode, mega_path, mega_link, tiktok_published, render_seconds=None):
    """Insert the produced-video row. Uses on_conflict upsert on episode_id so
    a retry after a partial failure can't create a duplicate row."""
    if not supabase_configured():
        return False, "Supabase not configured"

    record = {
        "episode_id": episode["id"],
        "level": episode.get("level"),
        "word1": episode.get("word1"),
        "word2": episode.get("word2"),
        "subtitle": episode.get("subtitle"),
        "mega_path": mega_path,
        "mega_link": mega_link,
        "tiktok_published": bool(tiktok_published),
    }
    if render_seconds is not None:
        record["render_seconds"] = render_seconds

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
            headers={**_supabase_headers(), "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "episode_id"},
            json=record,
            timeout=15,
        )
        r.raise_for_status()
        return True, None
    except requests.RequestException as e:
        detail = getattr(e.response, "text", str(e))
        return False, detail


# ── Episode selection ──
def pick_next(episodes, made_ids, forced_level=None):
    """Weighted random pick by level. Skips any episode already completed."""
    remaining = [ep for ep in episodes if ep["id"] not in made_ids]
    if forced_level:
        remaining = [ep for ep in remaining if ep.get("level") == forced_level]
    if not remaining:
        return None

    by_level = {}
    for ep in remaining:
        by_level.setdefault(ep.get("level", "LEVEL B1"), []).append(ep)

    levels = [lvl for lvl in LEVEL_WEIGHTS if by_level.get(lvl)]
    if not levels:
        return random.choice(remaining)  # fallback for levels outside the weight table

    weights = [LEVEL_WEIGHTS[lvl] for lvl in levels]
    chosen_level = random.choices(levels, weights=weights, k=1)[0]
    return random.choice(by_level[chosen_level])


def validate_episode(episode):
    missing = [f for f in REQUIRED_EPISODE_FIELDS if f not in episode]
    if missing:
        raise ValueError(f"episode '{episode.get('id', '?')}' missing fields: {missing}")


# ── Script generation ──
def generate_script(episode, output_path):
    """Read template, inject EPISODE dict + paths, write standalone script."""
    validate_episode(episode)

    with open(TEMPLATE) as f:
        template = f.read()

    ep_id = episode["id"]
    slug = ep_id.replace("_", "-")

    ep_dict_lines = ["EPISODE = {"]
    for key in [
        "level", "subtitle", "word1", "word1_color", "word2", "word2_color",
        "word1_pos", "word1_def", "word1_example", "word1_icon",
        "word2_pos", "word2_def", "word2_example", "word2_icon",
        "cards_header", "word1_card_sub", "word1_card_lines",
        "word2_card_sub", "word2_card_lines",
    ]:
        ep_dict_lines.append(f'    {json.dumps(key)}: {json.dumps(episode[key])},')
    ep_dict_lines.append('    "quiz_title": "Quick Quiz",')
    ep_dict_lines.append(f'    "quiz_question": {json.dumps(episode["quiz_question"])},')
    ep_dict_lines.append('    "quiz_prompt": "Which one fits?",')
    ep_dict_lines.append(f'    "result_line1": {json.dumps(episode["result_line1"])},')
    ep_dict_lines.append(f'    "result_line2": {json.dumps(episode["result_line2"])},')
    ep_dict_lines.append('    "promo_link": "vibte.com",')
    ep_dict_lines.append(f'    "promo_tagline": {json.dumps(episode["promo_tagline"])},')
    ep_dict_lines.append(f'    "tts": {json.dumps(episode["tts"])},')
    ep_dict_lines.append(f'    "episode_id_slug": {json.dumps(slug)},')
    ep_dict_lines.append(
        f'    "word_pair": [{json.dumps(episode["word1"].lower())}, '
        f'{json.dumps(episode["word2"].lower())}],'
    )
    ep_dict_lines.append("}")
    ep_dict_str = "\n".join(ep_dict_lines)

    template = re.sub(r'OUT_ROOT = .*', f'OUT_ROOT = "/tmp/vibte_{slug}_pipeline"', template)
    template = re.sub(r'OUT_VIDEO = .*', f'OUT_VIDEO = "/tmp/vibte_{slug}.mp4"', template)

    ep_start = template.find("EPISODE = {")
    if ep_start == -1:
        raise ValueError("Cannot find EPISODE = { in template")
    depth = 0
    ep_end = ep_start
    for i in range(ep_start, len(template)):
        if template[i] == '{':
            depth += 1
        elif template[i] == '}':
            depth -= 1
            if depth == 0:
                ep_end = i + 1
                break

    new_template = template[:ep_start] + ep_dict_str + template[ep_end:]
    with open(output_path, "w") as f:
        f.write(new_template)
    return output_path


def run_script(script_path):
    result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, timeout=600)
    if result.stdout:
        log.info(result.stdout.strip())
    if result.stderr:
        log.warning(result.stderr.strip()[-2000:])
    return result.returncode


def retry(fn, attempts=3, base_delay=5, what="operation"):
    """Retry fn() with exponential backoff. fn returns (ok: bool, detail: str)."""
    for attempt in range(1, attempts + 1):
        ok, detail = fn()
        if ok:
            return True, detail
        if attempt < attempts:
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(f"{what} failed (attempt {attempt}/{attempts}): {detail}. Retrying in {delay}s...")
            time.sleep(delay)
        else:
            log.error(f"{what} failed after {attempts} attempts: {detail}")
    return False, detail


def upload_to_mega(video_path, slug):
    remote_name = f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    remote_path = f"{MEGA_BASE}/{remote_name}"

    def attempt():
        r = subprocess.run(["megaput", "--path", remote_path, video_path],
                            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, remote_path

    ok, detail = retry(attempt, attempts=3, base_delay=10, what="MEGA upload")
    if not ok:
        return False, None

    r2 = subprocess.run(["megals", "-e", remote_path], capture_output=True, text=True, timeout=30)
    link = r2.stdout.strip() if r2.returncode == 0 else remote_path
    return True, link


def publish_to_tiktok(video_path, ep_id):
    tt = subprocess.run(
        [sys.executable, os.path.join(BASE, "tiktok_uploader.py"), video_path, ep_id],
        capture_output=True, text=True, timeout=600,
    )
    tt_out = (tt.stdout or "") + (tt.stderr or "")
    log.info(f"TikTok: {tt_out.strip()[-400:]}")
    if tt.returncode == 2:
        log.warning("TikTok needs one-time authorization (see tiktok_uploader.py --help)")
        return None
    if tt.returncode == 0 and "publish_id" in tt_out:
        return "published"
    log.warning(f"TikTok upload skipped/failed (exit {tt.returncode})")
    return None


def cleanup(script_path, video_path, pipeline_dir):
    for p in [script_path, video_path]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError as e:
            log.warning(f"Cleanup failed for {p}: {e}")
    shutil.rmtree(pipeline_dir, ignore_errors=True)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Pick and generate the script but don't render/upload/publish.")
    p.add_argument("--episode", help="Force a specific episode ID instead of picking randomly.")
    p.add_argument("--level", choices=list(LEVEL_WEIGHTS), help="Restrict the weighted pick to one level.")
    p.add_argument("--list-remaining", action="store_true", help="List not-yet-produced episode IDs and exit.")
    p.add_argument("--vocab", action="store_true", help="Use Oxford 5000 vocab source instead of episodes.json.")
    p.add_argument("--vocab-count", type=int, default=1, help="Number of vocab episodes to generate (with --vocab).")
    return p.parse_args()


def main():
    setup_logging()
    args = parse_args()

    if args.vocab:
        if VocabSource is None:
            log.error("vocab_source module not found. Make sure vocab_source.py is in the same directory.")
            sys.exit(1)
        episodes = build_vocab_episodes(count=args.vocab_count, level=args.level)
        if not episodes:
            log.error("No vocab episodes generated.")
            sys.exit(1)
        log.info(f"Generated {len(episodes)} vocab episode(s) from Oxford 5000")
    else:
        if not os.path.exists(EPISODES_FILE):
            log.error(f"Episodes file not found: {EPISODES_FILE}")
            log.error("Use --vocab to generate from Oxford 5000, or create episodes.json")
            sys.exit(1)
        with open(EPISODES_FILE) as f:
            episodes = json.load(f)

    with producer_lock():
        state = load_state()
        supabase_ids = fetch_completed_from_supabase()
        if supabase_ids is None:
            # Supabase unreachable/misconfigured — fall back to local cache only.
            made_ids = set(state["completed"])
        else:
            # Supabase is authoritative; union with local cache in case a local
            # completion hasn't made it to Supabase yet (e.g. prior run's insert failed).
            made_ids = supabase_ids | set(state["completed"])

            # Reconcile: anything completed locally but missing from Supabase
            # means a prior insert failed. Retry it now (no re-render needed —
            # we still have the episode metadata; mega_path/link are lost for
            # a true backfill, so this records the episode with what we have).
            missing_from_supabase = set(state["completed"]) - supabase_ids
            if missing_from_supabase:
                by_id = {ep["id"]: ep for ep in episodes}
                for eid in missing_from_supabase:
                    ep = by_id.get(eid)
                    if not ep:
                        continue
                    ok, detail = insert_video_record(ep, None, None, tiktok_published=False)
                    if ok:
                        log.info(f"Backfilled Supabase record for previously-completed '{eid}'")
                    else:
                        log.warning(f"Backfill insert still failing for '{eid}': {detail}")

        if args.list_remaining:
            remaining = [ep["id"] for ep in episodes if ep["id"] not in made_ids]
            print(f"{len(remaining)} remaining:")
            for eid in remaining:
                print(f"  {eid}")
            return

        if args.episode:
            episode = next((ep for ep in episodes if ep["id"] == args.episode), None)
            if episode is None:
                log.error(f"No episode with id '{args.episode}' in {EPISODES_FILE}")
                sys.exit(1)
            if episode["id"] in made_ids:
                log.warning(f"'{episode['id']}' is already marked completed — producing anyway (forced).")
        else:
            episode = pick_next(episodes, made_ids, forced_level=args.level)

        if not episode:
            log.info("✅ All episodes completed! Nothing to produce.")
            return

        ep_id = episode["id"]
        slug = ep_id.replace("_", "-")
        script_path = f"/tmp/vibte_{slug}.py"
        video_path = f"/tmp/vibte_{slug}.mp4"
        pipeline_dir = f"/tmp/vibte_{slug}_pipeline"

        log.info(f"🎬 Selected: {ep_id} ({episode['level']})")

        try:
            generate_script(episode, script_path)
        except (ValueError, KeyError) as e:
            log.error(f"❌ Script generation failed for {ep_id}: {e}")
            sys.exit(1)

        if args.dry_run:
            log.info(f"Dry run — generated {script_path}, stopping before render.")
            return

        log.info(f"📽️  Running: {script_path}")
        render_start = time.monotonic()
        try:
            rc = run_script(script_path)
        except subprocess.TimeoutExpired:
            log.error(f"❌ {ep_id} render timed out")
            cleanup(script_path, video_path, pipeline_dir)
            sys.exit(1)

        if rc != 0:
            log.error(f"❌ {ep_id} failed (exit {rc})")
            cleanup(script_path, video_path, pipeline_dir)
            sys.exit(1)

        render_seconds = round(time.monotonic() - render_start, 1)

        uploaded = False
        mega_path = None
        mega_link = None
        tiktok_result = None
        try:
            if os.path.exists(video_path):
                log.info("📤 Uploading to MEGA...")
                uploaded, mega_link = upload_to_mega(video_path, slug)
                if uploaded:
                    mega_path = f"{MEGA_BASE}/{slug}_{datetime.now().strftime('%Y%m%d')}.mp4"
                    log.info(f"  🔗 {mega_link}")
                else:
                    log.warning("  ⚠️  MEGA upload failed after retries")
            else:
                log.warning(f"  ⚠️  Video not found at {video_path}")

            if uploaded:
                tiktok_result = publish_to_tiktok(video_path, ep_id)

            if uploaded:
                # Local cache first (cheap, always available), then Supabase
                # (authoritative). If Supabase insert fails, the episode stays
                # correctly excluded from Supabase-side dedup but IS excluded
                # locally, and the union check above will retry the Supabase
                # write's absence isn't fatal — next run's fetch will just
                # union it in from local state again until the insert lands.
                mark_completed(state, ep_id)
                ok, detail = insert_video_record(
                    episode, mega_path, mega_link,
                    tiktok_published=bool(tiktok_result), render_seconds=render_seconds,
                )
                if not ok:
                    log.error(f"  ⚠️  Supabase insert failed ({detail}) — recorded locally only, will retry insert next run.")
                tt_note = " + TikTok" if tiktok_result else ""
                log.info(f"✅ {ep_id} done!{tt_note} ({len(state['completed'])}/{len(episodes)} produced)")
            else:
                log.warning(f"⚠️ {ep_id} rendered but NOT uploaded — will retry next run")
        finally:
            cleanup(script_path, video_path, pipeline_dir)


if __name__ == "__main__":
    main()
