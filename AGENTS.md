# AGENTS.md — Vibte Vocabulary Video Producer

## Project Overview

Automated vocabulary video producer for TikTok. Generates short English learning videos from Oxford 5000 CEFR-leveled word pairs with definitions from Free Dictionary API.

**Pipeline:** Oxford 5000 word list → Free Dictionary API → episode script → video render → MEGA upload → TikTok publish

## Directory Structure

```
hermse_vibte_agent_vocab/
├── vibte_producer.py          # Main producer (picks episode, renders, uploads, publishes)
├── vocab_source.py            # Oxford 5000 + Free Dictionary API source module
├── generate_vibte_video.py    # Video template (injected with EPISODE dict by producer)
├── oxford_5k.csv              # Cached Oxford 5000 CSV (auto-downloaded on first run)
├── .env.example               # Environment variable template
├── .env                       # Your secrets (git-ignored)
├── todo.txt                   # Original design notes
└── AGENTS.md                  # This file
```

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | Yes | e.g. `https://xxxxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Yes | service_role key (bypasses RLS) |

Supabase table: `vibte_videos` (columns: `episode_id`, `level`, `word1`, `word2`, `subtitle`, `mega_path`, `mega_link`, `tiktok_published`, `render_seconds`)

## Dependencies

```
pip install requests python-dotenv
```

Optional: `megatools` CLI for MEGA uploads, TikTok uploader script.

## Commands

### Generate vocab episodes (standalone)
```bash
# List word counts by level
python vocab_source.py --print-words

# Generate episodes to JSON
python vocab_source.py --count 5 --level "LEVEL B1" --output episodes.json
python vocab_source.py --count 10 --output all_episodes.json
```

### Run producer
```bash
# Dry-run (generate script only, no render/upload)
python vibte_producer.py --vocab --dry-run --level "LEVEL B2"

# Full run with vocab source
python vibte_producer.py --vocab --level "LEVEL B1"

# Full run from episodes.json
python vibte_producer.py

# Force specific episode
python vibte_producer.py --episode vocab_explore_glad

# List remaining episodes
python vibte_producer.py --list-remaining
```

### CLI Flags

| Flag | Description |
|---|---|
| `--vocab` | Use Oxford 5000 vocab source instead of episodes.json |
| `--vocab-count N` | Number of vocab episodes to generate (default: 1) |
| `--level LEVEL` | Restrict to one CEFR level (e.g. `LEVEL B1`) |
| `--dry-run` | Generate script only, skip render/upload/publish |
| `--episode ID` | Force a specific episode ID |
| `--list-remaining` | List not-yet-produced episodes and exit |

## CEFR Level Distribution

Producer uses weighted random selection:
- B1: 20%
- B2: 40%
- C1: 30%
- C2: 10%

Oxford 5000 word counts: A1 (1076), A2 (990), B1 (902), B2 (1571), C1 (1404)

## Episode Schema

Required fields for `episodes.json` or vocab_source output:

```json
{
  "id": "vocab_word1_word2",
  "level": "LEVEL B1",
  "subtitle": "word1 vs word2",
  "word1": "word1", "word1_color": "#FFC107",
  "word2": "word2", "word2_color": "#FFC107",
  "word1_pos": "noun", "word1_def": "...", "word1_example": "...", "word1_icon": "🌳",
  "word2_pos": "verb", "word2_def": "...", "word2_example": "...", "word2_icon": "🌳",
  "cards_header": "LEVEL B1",
  "word1_card_sub": "noun", "word1_card_lines": ["...", "..."],
  "word2_card_sub": "verb", "word2_card_lines": ["...", "..."],
  "quiz_question": "Which word means \"...\"?",
  "result_line1": "The answer is: word1",
  "result_line2": "word1 — definition",
  "promo_tagline": "Learn English with Vibte!",
  "tts": "Let's learn two new words: word1 and word2."
}
```

## Data Sources

1. **Oxford 5000 CSV** — `https://raw.githubusercontent.com/nalgeon/words/main/data/oxford-5k.csv`
   - Columns: `word`, `level` (a1–c1), `pos`, `definition_url`, `voice_url`
   - Cached locally to `oxford_5k.csv` after first download

2. **Free Dictionary API** — `https://api.dictionaryapi.dev/api/v2/entries/en/{word}`
   - No API key required
   - Returns: phonetics, part of speech, definitions, example sentences
   - Fallback: if no entry or no example, uses template-generated examples

## Supabase Schema

Table `vibte_videos` (created manually or via migration):

```sql
CREATE TABLE vibte_videos (
  id BIGSERIAL PRIMARY KEY,
  episode_id TEXT UNIQUE NOT NULL,
  level TEXT,
  word1 TEXT,
  word2 TEXT,
  subtitle TEXT,
  mega_path TEXT,
  mega_link TEXT,
  tiktok_published BOOLEAN DEFAULT FALSE,
  render_seconds REAL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## How No-Repeat Works

1. On startup, fetch all `episode_id` values from Supabase
2. Union with local `~/.hermes/vibte_producer_state.json` cache
3. Pick next episode from pool excluding completed IDs
4. After successful upload, insert into Supabase + mark local state
5. On next run, reconcile: retry any locally-completed but missing-from-Supabase entries

## Locking

- Unix: `fcntl.flock()` on `~/.hermes/vibte_producer.lock`
- Windows: PID-based lock file check
- Prevents concurrent runs from picking the same episode

## Logging

- Console (UTF-8) + rotating file at `~/.hermes/vibte_producer.log`
- Max 2MB, 3 backups
