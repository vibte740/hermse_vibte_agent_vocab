# Vibte Pipeline — Handoff Summary

## What This Does
Generates TikTok vocabulary learning videos automatically:
1. Picks random word pairs from Oxford 5000 CEFR word list
2. Fetches definitions/examples from Free Dictionary API
3. Renders 1080x1920 vertical video (~30s) with Pillow + moviepy
4. Uploads to MEGA storage → publishes to TikTok

## Files

| File | Purpose | Status |
|------|---------|--------|
| `vocab_source.py` | Oxford 5000 CSV + Free Dictionary API → episode dict | ✅ Working |
| `generate_vibte_video.py` | Pillow + moviepy video template (5 scenes) | ✅ Working |
| `vibte_producer.py` | Main pipeline: pick episode → render → upload → publish | ✅ Rendering works |
| `mega_auth.txt` | MEGA credentials (email + password) | ⚠️ Password wrong |
| `demo_video.mp4` | Test render: "emission vs repeat" LEVEL B2 | ✅ 347KB |

## What Works
- **Rendering**: ~30s per video, 1080x1920, H.264, 30fps
- **Word selection**: Weighted random (B1 20%, B2 40%, C1 30%, C2 10%)
- **Free Dictionary API**: Gets phonetics, definitions, examples
- **No ImageMask needed**: Text rendered via Pillow (arial.ttf from C:\Windows\Fonts)
- **State tracking**: Prevents duplicate episodes (local JSON + optional Supabase)
- **Git repo**: https://github.com/vibte740/hermse_vibte_agent_vocab

## What's Blocked
1. **MEGA upload** — password `Q@z123445` returns HTTP 402 on mega.co.nz API
   - `mega.py` library needs asyncio patch for Python 3.12: `asyncio.coroutine = lambda fn: fn`
   - Login flow: `us0` → get PBKDF2 salt → `us` with user_hash → 402 empty body
   - Fix: Update `mega_auth.txt` with correct password

2. **TikTok publish** — no `tiktok_uploader.py` exists yet

## Commands

```bash
# Render one video from Oxford 5000
python render_demo.py

# Full pipeline (render + upload + publish)
python vibte_producer.py --vocab --level "LEVEL B1"

# Dry run (generate script only)
python vibte_producer.py --vocab --dry-run --level "LEVEL B2"

# Generate episode JSON without rendering
python vocab_source.py --count 5 --output episodes.json
```

## Video Template Structure (generate_vibte_video.py)
- Title scene: level + subtitle + "Vibte English" (2.5s)
- Word 1 card: icon + word + pos + definition + example (5s)
- Word 2 card: same (5s)
- Quiz: question + both words (4s)
- Result: answer + promo tagline (4s)

## Episode Dict Schema (required fields)
```json
{
  "id": "vocab_word1_word2",
  "level": "LEVEL B1",
  "subtitle": "word1 vs word2",
  "word1": "...", "word1_color": "#FFC107",
  "word2": "...", "word2_color": "#FFC107",
  "word1_pos": "noun", "word1_def": "...", "word1_example": "...", "word1_icon": "...",
  "word2_pos": "verb", "word2_def": "...", "word2_example": "...", "word2_icon": "...",
  "cards_header": "LEVEL B1",
  "word1_card_sub": "noun", "word1_card_lines": ["..."],
  "word2_card_sub": "verb", "word2_card_lines": ["..."],
  "quiz_question": "Which word means ...?",
  "result_line1": "The answer is: word1",
  "result_line2": "word1 — definition",
  "promo_tagline": "Learn English with Vibte!",
  "tts": "Let's learn two new words: word1 and word2."
}
```

## To Finish
1. Fix MEGA password in `mega_auth.txt`
2. Build `tiktok_uploader.py` (TikTok API or browser automation)
3. Set up Supabase for production tracking (optional)
4. Commit updated files: `git add -A && git commit -m "..." && git push`
