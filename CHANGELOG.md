# Changelog

All notable changes to this project are recorded here. Versions follow
[Semantic Versioning](https://semver.org/): MAJOR.MINOR.PATCH.

## [0.5.0] - 2026-09-14

### Added
- **Context-aware spontaneous replies.** When the bot chimes in on its own, it
  now reads the last 4 messages in the channel and asks Claude to continue the
  conversation in the active persona's voice (falls back to a plain Markov
  sentence when there's no readable context or Claude is unavailable).
- Spontaneous replies now speak as the **selected persona** (the one `/persona`
  and `/speak` use), not a random learned user, and are prefixed with the
  persona's name.

### Changed
- Spontaneous-reply odds raised to **6% per message, capped at 80%** (was
  2% / 40%).
- `MAX_SAMPLES` raised to 500 (was 200); scans now read the full history via a
  `HISTORY_LIMIT` constant.
- Conversation memory is keyed per `(channel, persona)` instead of per channel,
  so switching personas starts fresh short-term context.

## [0.4.0] - 2026-09-09

### Added
- **Per-server state.** Every guild now has its own learned users, active
  persona, mode, server chain and heat. `memory.json` moved to a `version: 2`
  format keyed by guild id; old files are migrated automatically (kept as
  `legacy` and adopted by the guild whose name matches). All commands are
  guild-only.
- `/persona` — dropdown to pick exactly who to talk as (any learned user or the
  server). `/mode` remains as a quick toggle.
- `/what` — embed explaining every command.
- **@mention and reply triggers.** Mentioning the bot, or replying to one of
  its messages, gets a Claude answer in persona (same path as `/ask`).
- **Conversation memory.** The last 8 exchanges per channel are sent to Claude
  as prior turns, so follow-up questions work. In-memory only.

### Changed
- `/ask` replies were generic: Claude was only shown 3 Markov sentences. Scans
  now keep up to 200 real messages per persona (`samples`), and `/ask` sends 40
  of them with a strict stay-in-character prompt (match language, slang,
  casing, length; no assistant tone). Re-run `/mimic` or `/servermimic` to
  populate samples for existing personas.
- `/ask` and mention replies show `**Name**:` and echo the question.
- Spontaneous replies: 2% per message, capped at 40% (was 1% / 25%).
- Claude errors are logged to the terminal; chat just gets "...".
- `max_tokens` for `/ask` lowered to 200.

## [0.3.0] - 2026-09-09

### Added
- `/ask <question>` — answers a question in the active persona's voice using
  Claude. Three Markov-generated sentences are passed as style examples. Needs
  `ANTHROPIC_API_KEY` in `secrets.env`; the model is configurable through
  `CLAUDE_MODEL` (default `claude-sonnet-4-5`). The `anthropic` package is
  imported lazily, so the bot still starts without it.
- `__version__` constant, printed at startup.
- `CHANGELOG.md` (this file).

### Fixed
- **Expiring media links.** Direct image uploads are no longer saved: Discord's
  attachment URLs are signed and expire after ~24h, so re-sending them produced
  dead links. Only Tenor/Giphy links are kept. Existing `memory.json` files may
  still contain old CDN URLs until the user is re-mimicked.
- **Long scans lost their results.** Interaction tokens expire after 15
  minutes; scans of large servers took longer and the final followup silently
  failed. Progress and results are now sent as ordinary channel messages.
- **One bad channel aborted the whole scan.** `scan_channel` now catches any
  `discord.HTTPException` (not just `Forbidden`), logs the skipped channel, and
  continues. Channels that `get_channel()` cannot resolve are filtered out
  before scanning instead of crashing `asyncio.gather`.
- **Silent failures in the channel picker.** `ChannelPicker` gained an
  `on_error` hook; view callbacks are not covered by `tree.error`, so failures
  previously only appeared in the terminal.
- **Error handler could not reply.** `on_tree_error` now checks
  `interaction.response.is_done()` and uses `response.send_message` or
  `followup.send` accordingly.
- **Re-syncing on every reconnect.** Memory loading and `tree.sync()` moved
  from `on_ready` (fires on each reconnect, and sync is rate-limited) to
  `setup_hook` (runs once).
- **Data loss on crash during save.** `memory.json` is written to a temp file
  and swapped in with `os.replace`, so a crash mid-write can no longer leave a
  truncated file that the loader treats as empty state.

### Changed
- Result messages from `/mimic` use `AllowedMentions.none()` so listing the
  mimicked users does not ping them.
- `anthropic` pinned to `>=1.4,<2` in `requirements.txt`.
- `.env.example` documents `ANTHROPIC_API_KEY` and `CLAUDE_MODEL`.
- README, HOW_IT_WORKS.md and LINE_BY_LINE.md updated to match (line numbers,
  `/ask`, `setup_hook`, media policy, error handling, atomic saves).

## [0.2.0] - 2026-08-29

### Added
- Explanatory comments throughout `markov_bot_julia.py`.

### Fixed
- HOW_IT_WORKS.md: incorrect "three triples" example.
- Token error message now references `secrets.env`.
- `on_tree_error` hardened against a missing `interaction.command`.

### Changed
- LINE_BY_LINE.md renumbered against the commented source.

## [0.1.0] - 2026-08-29

Initial release: `/mimic` (up to 4 users), `/servermimic`, `/mode`, `/speak`,
`/users`, `/converse`; second-order Markov chain with entropy tricks; GIF and
image re-sending; spontaneous "heat"-driven replies; channel multi-select
picker with live progress; JSON persistence.
