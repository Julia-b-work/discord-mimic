# 🦜 User Mimic

A Discord bot that learns to talk *like you* — or like your friends. Feed it a
user's chat history and it builds a statistical model of how they write, then
generates brand-new messages in their voice. The core is a plain Markov chain
with no AI dependencies; Claude layers on top so the persona can answer real
questions (`/ask`), respond to mentions/replies, and even chime in on its own
with context from recent messages.

**Current version: 0.5.1** — see [CHANGELOG.md](CHANGELOG.md).

## What it does

- Reads a user's entire message history across the channels you pick, trains a
  Markov chain on their wording, and saves it to disk so it survives restarts.
- Can also learn from **every member at once** (`/servermimic`), turning into a
  persona built from the whole server's chatter.
- Speaks back in the learned style — including re-sending the GIFs they post.
- Optionally answers questions *as* the learned persona using Claude (`/ask`).
- Can even lurk in the chat and chime in spontaneously as the selected persona,
  reading recent messages for context.

## Commands

| Command                          | Description                                                          |
| -------------------------------- | -------------------------------------------------------------------- |
| `/mimic @user [@u2] [@u3] [@u4]` | Pick channels, learn up to **4 users at once**, save their styles. |
| `/servermimic`                   | Pick channels, learn from **everyone** at once, become the server.   |
| `/persona`                       | Dropdown to choose exactly who I talk as (any learned user or the server). |
| `/mode user\|server`             | Quick toggle between the current user and the server personality.   |
| `/speak`                         | Generate a new sentence as the active personality.                   |
| `/users`                         | List everyone learned, marking the active one.                       |
| `/converse @user1 @user2`        | Simulate a back-and-forth conversation between two learned users.    |
| `/ask <question>`                | Ask the active persona a question; answered by Claude in their voice (needs an Anthropic API key). |
| `/what`                          | Explain all commands.                                                |

You can also **@mention the bot** or **reply to one of its messages** to talk to
the persona directly; it remembers the last few exchanges per channel.

Everything the bot learns is **per server** — personas never leak between guilds.

When `/mimic` or `/servermimic` asks which channels to read, you get a native
multi-select dropdown — or just hit **"All the channels"** to scan the whole
server in one click.

## How it works

The bot is powered by a **Markov chain**: it looks at every pair of consecutive
words a user writes and records which words tend to follow that pair. To
generate a sentence it starts from a random pair and walks the chain, picking a
random follower each step. A few entropy tricks (unique-follower sampling and
random vocabulary jumps) keep the output fresh instead of parroting the user's
exact phrases.

Media mimicking works the same way: the bot remembers the Tenor/Giphy GIF links
a user posts and occasionally re-sends them, so the persona feels more complete.
(Direct image uploads are skipped on purpose — Discord's attachment URLs expire
after about a day.)

The Claude layer (used by `/ask`, mention/reply answers, and context-aware
spontaneous replies) is the one exception to "no AI": it samples real messages
as style examples and asks Claude to write in that voice.

## Tech stack

- **Python 3** + **discord.py** (slash commands, UI components, async)
- **Markov chain** — pure Python, no ML dependencies
- **anthropic** — Claude API client, used only by `/ask`
- **python-dotenv** — loads secrets from `secrets.env`
- **JSON** (`memory.json`) — persistence across restarts, written atomically

## Project structure

- `markov_bot_julia.py` — the entire bot: commands, Markov chain, scanning, and
  persistence.
- `requirements.txt` — pinned Python dependencies.
- `memory.json` — the learned styles, written automatically after each `/mimic`.
- `HOW_IT_WORKS.md` — conceptual explanation of every mechanism.
- `LINE_BY_LINE.md` — a function-by-function tour of the code.
- `CHANGELOG.md` — what changed in each version.
- `secrets.env` — your bot token and API key (never committed).
- `.env.example` — template for `secrets.env`.

## Setup

1. Create a bot app in the [Discord Developer Portal](https://discord.com/developers/applications)
   and enable the **Message Content** intent.
2. Copy `.env.example` to `secrets.env` and paste your bot token. To enable
   `/ask`, also add an `ANTHROPIC_API_KEY`; everything else works without it.
3. Create a virtualenv and install dependencies:
   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
4. Run the bot:
   ```bash
   .venv/bin/python -u markov_bot_julia.py
   ```

## Configuration

- `MEMORY_FILE` (in `markov_bot_julia.py`) — where learned styles are saved
  (`memory.json`). Delete it to reset all learned users.
- `generate_sentence(..., max_words=...)` — target sentence length (default 30).
- The spontaneous-reply odds are tuned by the `heat` logic in `on_message`.
- `CLAUDE_MODEL` (in `secrets.env`) — which Claude model `/ask` uses
  (default `claude-sonnet-4-5`).

## Skills demonstrated

- discord.py: slash commands, Message Content intent, channel history fetching
- Markov chains and procedural text generation
- Async programming (concurrent channel scanning with `asyncio.gather`)
- Interactive UI components (multi-select dropdowns + buttons)
- JSON persistence with atomic writes and graceful restart recovery
- Defensive Discord API handling (interaction-token expiry, per-channel error
  isolation, view-level error hooks)
- Calling a blocking third-party API (Anthropic) from async code

## Status

- [x] Project documented
- [x] Bot scaffold
- [x] `/mimic` — multi-user (up to 4), channel multi-select + "all channels"
- [x] `/servermimic` — learn from the whole server at once
- [x] `/mode` — switch between user and server personalities
- [x] `/users` — list all learned personalities
- [x] `/speak` and `/converse`
- [x] Markov chain training + entropy-tuned generation
- [x] Media (GIF) mimicking
- [x] Spontaneous in-chat personality replies
- [x] Live progress messages while scanning
- [x] Persistence to disk (survives restarts)
- [x] `/ask` — Claude-backed Q&A in the persona's voice
- [x] `/persona`, `/what`, @mention/reply answers with conversation memory
- [x] Context-aware spontaneous replies (reads recent messages, Claude in-character)
- [x] Per-server state
- [x] Robustness pass: long scans, expiring URLs, error handling, atomic saves

## Author

Built by **Julia Barrios** — a developer working primarily in C, C++, and Python.

- GitHub: [@Julia-b-work](https://github.com/Julia-b-work)
- Portfolio: [portfolio-6rx8.onrender.com](https://portfolio-6rx8.onrender.com)

## License

Distributed under the [MIT License](LICENSE).
