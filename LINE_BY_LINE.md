# Code Map

A function-by-function tour of `markov_bot_julia.py`. This replaces the old
line-by-line walkthrough, which went stale as soon as line numbers shifted.
Read this alongside `HOW_IT_WORKS.md`, which explains the *concepts*.

## 1. Imports and constants

- `load_dotenv("secrets.env")` reads the bot token and Anthropic API key.
- `DISCORD_TOKEN` must be set or the bot refuses to start.
- Intents enable `message_content`, which is required to read message text.
- `HISTORY_TURNS` / `histories` — short-term conversation memory for Claude
  (last few exchanges per channel+persona, in-memory only).
- `MAX_SAMPLES` — how many real messages are kept per persona as style examples.
- `HISTORY_LIMIT` — how much channel history a scan reads (`None` = all of it).
- `CLAUDE_MODEL` — which model the Claude calls use.
- `MEMORY_FILE` — path to `memory.json`.

## 2. State and persistence

- `guilds` / `legacy_state` — all learned data, keyed per guild.
- `state(guild)` — returns the guild's state dict, creating an empty one on
  first use.
- `save_users()` — writes everything to `memory.json` **atomically** (temp file
  then `os.replace`), so a crash mid-write can't corrupt the file.
- `load_users()` — reads `memory.json` back, migrating the old single-server
  format into the per-guild `version: 2` shape.
- `setup_hook` — runs once: loads memory, marks commands guild-only, syncs
  slash commands. (Uses `setup_hook`, not `on_ready`, because `on_ready` fires
  again on every reconnect and `tree.sync()` is rate-limited.)

## 3. The message handler (`on_message`)

- Ignores bots and DMs.
- If a message @mentions the bot or replies to one of its messages, the active
  persona answers via `persona_answer` (Claude).
- Otherwise it raises `heat` and, on a random roll, the bot chimes in as the
  selected persona via `spontaneous_reply`.

## 4. The Markov core

- `build_markov_chain(messages)` — records, for every adjacent word pair, the
  words that follow it.
- `generate_sentence(chain)` — walks the chain from a random pair, with two
  entropy tricks (unique-follower sampling and random vocabulary jumps) so it
  doesn't just parrot the source.
- `extract_media(message)` — pulls Tenor/Giphy GIF links out of a message.

## 5. Scanning (`/mimic`, `/servermimic`)

- `run_mimic` / `run_server_mimic` read channel history concurrently
  (`asyncio.gather`), with a live progress ticker, and learn one chain per
  target user (or one merged chain for the whole server).
- `ChannelPicker` — the multi-select channel dropdown + "All the channels"
  button that triggers a scan.

## 6. Commands

| Command | What it does |
| --- | --- |
| `/mimic` | Learn up to 4 users' styles. |
| `/servermimic` | Learn the whole server as one persona. |
| `/mode` | Quick toggle between user and server persona. |
| `/persona` | Dropdown to pick exactly who to talk as. |
| `/speak` | Emit one sentence as the active persona. |
| `/users` | List learned personas, marking the active one. |
| `/converse` | Simulate a chat between two learned users. |
| `/what` | Embed explaining every command. |
| `/ask` | Ask the active persona a question (Claude). |

## 7. Claude integration

- `active_persona(guild)` — resolves the currently selected persona dict
  (`name`, `chain`, `media`, `samples`).
- `persona_system_prompt(persona)` — builds the "stay in character" system
  prompt from real message samples (or Markov output as a fallback).
- `persona_answer(...)` — answers a question as the persona, keeping a
  per-channel+persona history so follow-ups make sense. Used by `/ask` and
  mention/reply handling.
- `spontaneous_reply(persona, message)` — reads the last 4 messages and asks
  Claude to continue the conversation in character; falls back to a Markov
  sentence if there's no context.
- `claude_reply(system_prompt, messages)` — the synchronous Anthropic API call,
  run in a worker thread (`asyncio.to_thread`) so the bot stays responsive. The
  `anthropic` import is lazy so the bot still starts without the package.

## 8. Error handling

`on_tree_error` is the global hook for slash-command failures: it logs the real
error to the terminal and replies with a friendly message, choosing between an
initial response and a followup based on `response.is_done()`.
