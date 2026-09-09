# Line-by-Line Walkthrough

This document explains *every* line of *markov_bot_julia.py*, from the first
import to the final line that connects to Discord.

It is written for someone who has never written a Discord bot — or any Python —
before. If a term like "decorator" or "intent" means nothing to you yet, don't
worry: each one is explained the first time it appears.

> **Note:** this walkthrough was written for **v0.3.0** (754 lines). Version
> 0.4.0 added per-server state, `/persona`, `/what`, @mention replies and
> conversation memory, so line numbers and the storage sections (4–7) are out
> of date. See *CHANGELOG.md* and *HOW_IT_WORKS.md* for what changed.

Line numbers refer to v0.3.0 of the file (754 lines). To follow
along, open *markov_bot_julia.py* next to this document and read them together.

---

## 0. The essentials first

Before the code, here are the few ideas you need. Skip this section if you
already know Python.

### Python in one breath

- Python runs a file **top to bottom**, one line at a time.
- A `#` starts a **comment** — text Python ignores, written for humans.
- Indentation (leading spaces) is *meaningful*. Code indented under a `def` or
  an `if` belongs to that block.
- `def name():` defines a **function**: a named block of code you can call later.
- **Variables** hold values. You assign with `=`, like `x = 5`.
- Python has a handful of **types** you will meet repeatedly:
  - *str* — text, written in quotes: `"hello"`.
  - *int* — a whole number: `5`.
  - *float* — a decimal: `0.4`.
  - *bool* — `True` or `False`.
  - *None* — a special value meaning "nothing here".
  - *list* — an ordered collection: `["a", "b", "c"]`.
  - *tuple* — an ordered collection that can't be changed: `("a", "b")`.
  - *dict* — a lookup table of key → value pairs: `{"a": 1, "b": 2}`.
  - *set* — an unordered collection with no duplicates: `{"a", "b"}`.

### What "async" means

You will see `async def` and `await` everywhere. Discord bots spend most of
their time *waiting* — waiting for Discord's servers to answer a request. While
one task waits, Python can work on another. `async` functions ("coroutines")
are written so they can pause and let others run. `await` means "pause here
until this finishes, and let something else use the time meanwhile."

Don't worry about the deep theory. The rule of thumb is: functions that touch
Discord are `async`, and every call to one is prefixed with `await`.

### Discord vocabulary

- **Guild** — Discord's internal name for a "server".
- **Channel** — a text or voice room inside a guild.
- **Member** — a user account, as seen inside a specific guild.
- **Message** — one post in a channel.
- **Bot** — an automated account, controlled by code instead of a human.
- **Token** — the bot's secret password, used to log in.
- **Intent** — a permission flag for *what kinds of events* the bot may receive.
- **Slash command** — a `/command` you type in Discord, which Discord itself
  autocompletes and routes to the bot.
- **Interaction** — the object Discord hands your bot when someone uses a slash
  command or clicks a button.

---

## 1. Imports (lines 1–8)

```python
1:  from dotenv import load_dotenv
2:  import os
3:  import json
4:  import asyncio
5:  import random
6:  from typing import Literal
7:  import discord
8:  from discord.ext import commands
```

An **import** pulls in code written by someone else (a "library" or "module")
so this file can use it. Python ships with *os*, *json*, *asyncio*, *random*,
and *typing* built in; *discord* and *dotenv* are third-party libraries
installed with pip.

- **1** — `load_dotenv` reads a file of secrets (see line 11).
- **2** — `os` reads **environment variables**, one of which will hold the bot
  token (line 13).
- **3** — `json` saves and loads the learned data as JSON text (lines 49–113).
- **4** — `asyncio` runs channel scans in parallel and manages the progress
  ticker (lines 289–307).
- **5** — `random` supplies all the coin-flips and random choices the bot uses
  to sound human.
- **6** — `Literal` lets a slash command accept only a fixed set of values
  ("user" or "server"), which Discord then shows as a dropdown (line 559).
- **7** — `discord` is the whole point: it speaks Discord's protocol for us.
- **8** — `commands.Bot` is a more capable bot class (it supports slash
  commands and a command tree), imported from the *commands* extension.

Note the two syntaxes: `import os` brings in the whole module (used as
`os.getenv`), while `from X import Y` brings in just one name (used as
`load_dotenv`).

---

## 2. Loading the secret token (lines 11–18)

```python
11: load_dotenv("secrets.env")
```

This reads the file *secrets.env*, which looks like:

```
DISCORD_TOKEN=abc123...
```

After this line, the value is available as an environment variable.

```python
13: token = os.getenv("DISCORD_TOKEN")
```

`os.getenv("DISCORD_TOKEN")` returns the value of the *DISCORD_TOKEN* variable,
or `None` if it isn't set. The result is stored in the variable `token`.

```python
16: if token is None:
17:     print("ERROR: DISCORD_TOKEN is not set. Create a secrets.env file with the token.")
18:     raise SystemExit(1)
```

- **16** — `if` runs the indented block only when its condition is true. Here
  it checks whether the token is missing.
- **17** — `print` writes a message to the terminal so the owner sees *why*.
- **18** — `raise SystemExit(1)` stops the program immediately with exit code
  1 (the convention for "something went wrong"). Failing fast here is better
  than connecting with no token and getting a confusing error later.

---

## 3. Intents and the client (lines 22–27)

```python
22: intents = discord.Intents.default()
23: intents.message_content = True
```

An **intent** is a switch that controls whether the bot receives a category of
events from Discord. Discord restricts these for privacy and performance — a
bot must explicitly ask for what it needs.

- **22** — `Intents.default()` creates the standard set (guilds, members, and
  so on).
- **23** — Turns on **Message Content**, the intent required to read the actual
  *text* of messages (`message.content`). Without it, the bot could not see
  what anyone says. This intent must *also* be enabled in the Discord Developer
  Portal, or Discord rejects the connection.

```python
27: client = commands.Bot(command_prefix="!", intents=intents)
```

This creates the bot object and stores it in `client` — the central variable
almost every later function uses.

- `command_prefix="!"` — a leftover requirement of `commands.Bot`: even though
  this bot only uses slash commands, the class insists on a prefix for old-style
  `!command` text commands. `"!"` is harmless filler here.
- `intents=intents` — hands the bot the intent switches configured above.

---

## 4. The bot's memory (lines 29–45)

These are **global variables** — variables defined at the top level of the
file, so every function can read (and, where allowed, write) them.

```python
29: # Learned state, reloaded from disk in setup_hook.
30: users = {}
31: current_user_id = None
```

- **30** — `users` is a dictionary keyed by user id (as a string) mapping to
  everything learned about that person: their display name, their Markov
  chain, and their saved media. Starts empty.
- **31** — `current_user_id` remembers *who* `/speak` should talk as. `None`
  means "nobody yet".

```python
33: # "user" -> speak as the current user; "server" -> speak as the whole server.
34: mode = "user"
```

The bot has two personalities: it can imitate a single person, or the entire
server's combined chatter. `mode` is a string that records which one is active.

```python
36: # The whole-server chain + media, built by /servermimic. None until learned.
37: server = None
```

`server` holds the combined chain built from *everyone's* messages. It stays
`None` until someone runs `/servermimic`.

```python
39: # Spontaneous-reply pressure; resets to 0 after the bot speaks.
40: heat = 0
```

`heat` is a counter that rises with each real message and makes the bot
increasingly likely to butt into the conversation on its own (see line 137).

```python
42: MEMORY_FILE = "memory.json"
```

The filename where all learned data is saved. A constant (written in
ALL_CAPS) so it's easy to find and change in one place.

```python
45: CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
```

Which Claude model `/ask` (section 21) should call. `os.getenv` with a second
argument returns that **default** if the variable isn't set, so the model can be
overridden from *secrets.env* without editing code.

---

## 5. Saving to disk — `save_users()` (lines 49–81)

```python
49: def save_users():
```

Defines a function called *save_users*. It takes no arguments and returns
nothing; its job is to write the current in-memory state to a JSON file.

```python
50:     data = {
51:         "mode": mode,
```

`data` is a dictionary being assembled to represent everything worth saving.
The key `"mode"` stores the current mode ("user" or "server").

```python
54:         "server": (
55:             None
56:             if server is None
57:             else {
58:                 "name": server["name"],
59:                 "chain": [
60:                     [list(key), words] for key, words in server["chain"].items()
61:                 ],
62:                 "media": server.get("media", []),
63:             }
64:         ),
```

This is a **conditional expression** (the `A if cond else B` form). It says:

- If `server` is `None`, save `None`.
- Otherwise, save a small dictionary describing the server: its name, its
  chain, and its media.

The tricky part is line 60. A Markov chain's keys are *(word, word)* tuples,
but JSON cannot store tuples — it only understands lists. So the
**list comprehension** on line 60 rewrites each tuple key as a list:

```python
[list(key), words] for key, words in server["chain"].items()
```

For each *(key, words)* pair in the chain, it produces a two-element list:
`[ [word1, word2], [followers...] ]`. (Read `.items()` as "give me every
key→value pair in the dictionary.")

```python
65:         "current_user_id": current_user_id,
66:         "users": {
67:             uid: {
68:                 "name": info["name"],
70:                 "chain": [[list(key), words] for key, words in info["chain"].items()],
71:                 "media": info.get("media", []),
72:             }
73:             for uid, info in users.items()
74:         },
75:     }
```

- **65** — Remembers who `/speak` should talk as after a restart.
- **66–74** — A **dictionary comprehension** that walks every learned user and
  builds a matching JSON-safe dictionary for each. `uid` is the user id (a
  string); `info` is their `{"name", "chain", "media"}` entry. Line 67 repeats
  the same tuple→list conversion as line 60. Line 68 uses `info.get("media",
  [])`, which returns the media list, or `[]` if it's missing — a safety net
  for data saved before the media feature existed.

```python
78:     tmp_file = MEMORY_FILE + ".tmp"
79:     with open(tmp_file, "w", encoding="utf-8") as f:
80:         json.dump(data, f, ensure_ascii=False)
81:     os.replace(tmp_file, MEMORY_FILE)
```

- **78** — The name of a scratch file next to the real one: `memory.json.tmp`.
- **79** — `open(...)` opens that scratch file. The `"w"` mode means "write
  (overwrite)". `encoding="utf-8"` makes sure emoji and accented letters
  survive. The `with` block guarantees the file is closed afterward, even if
  something goes wrong. `f` is the file handle.
- **80** — `json.dump` converts `data` into JSON text and writes it. The
  `ensure_ascii=False` flag keeps non-English characters readable instead of
  escaping them into `\uXXXX`.
- **81** — `os.replace` renames the finished scratch file over *memory.json* in
  one **atomic** step: the operating system guarantees you end up with either
  the complete old file or the complete new file, never a half-written one.
  Without this, a crash or power cut in the middle of line 80 could leave a
  truncated *memory.json*, which the loader (section 6) would treat as corrupt
  and silently replace with an empty state — wiping everything learned.

---

## 6. Loading from disk — `load_users()` (lines 86–113)

This is the mirror image of *save_users*: it reads the file back into memory.

```python
86: def load_users():
87:     try:
88:         with open(MEMORY_FILE, "r", encoding="utf-8") as f:
89:             data = json.load(f)
90:     except (FileNotFoundError, json.JSONDecodeError):
91:         return {}, None, "user", None
```

- **87–89** — A `try` block attempts something that might fail. Here it opens
  the file in `"r"` (read) mode and parses the JSON with `json.load`.
- **90** — `except` catches two specific failures:
  - *FileNotFoundError* — the file doesn't exist yet (first ever run).
  - *JSONDecodeError* — the file exists but is corrupt (half-written).
- **91** — If either happens, return a clean empty state: no users, no current
  user, default "user" mode, and no server. A fresh or damaged install therefore
  never crashes the bot.

```python
93:     loaded = {}
94:     for uid, info in data.get("users", {}).items():
95:         loaded[uid] = {
96:             "name": info["name"],
98:             "chain": {tuple(key): words for key, words in info["chain"]},
99:             "media": info.get("media", []),
100:         }
```

- **93** — A fresh empty dictionary to fill.
- **94** — `data.get("users", {})` returns the saved users, or `{}` if the key
  is missing. `.items()` then lets the loop visit each (uid, info) pair. This is
  the reverse of the comprehension on lines 66–74.
- **98** — Converts each list key back into a tuple with `tuple(key)`, undoing
  the conversion from line 70.

```python
102:     server_data = data.get("server")
103:     loaded_server = None
104:     if server_data is not None:
105:         loaded_server = {
106:             "name": server_data.get("name", "the server"),
107:             "chain": {
108:                 tuple(key): words for key, words in server_data.get("chain", [])
109:             },
110:             "media": server_data.get("media", []),
111:         }
```

- **102** — Grabs the saved server section, or `None` if there wasn't one.
- **103** — Default: no server.
- **104–111** — If a server was saved, rebuild it, converting tuple keys back
  (line 108) and using `"the server"` as a fallback name if one is missing.

```python
113:     return loaded, data.get("current_user_id"), data.get("mode", "user"), loaded_server
```

`return` hands four values back to the caller, in order: the users dictionary,
the current user id, the mode, and the server. (See how *setup_hook* unpacks
these at line 122.)

---

## 7. Startup — `setup_hook` and `on_ready` (lines 119–133)

```python
119: async def setup_hook():
```

A plain `async def` — no decorator yet. discord.py has a special slot on the
bot called `setup_hook`: whatever coroutine is stored there runs **exactly
once**, right after the bot logs in but *before* it connects to the live event
stream. That makes it the right home for one-time startup work.

```python
120:     global users, current_user_id, mode, server
```

Because Python functions treat assigned variables as local by default, this
`global` statement declares "the names I'm assigning below refer to the
module-level globals, not to new local copies."

```python
122:     users, current_user_id, mode, server = load_users()
```

Calls *load_users* and **unpacks** its four return values into the four globals
in order. Whatever was learned before is now back in memory.

```python
124:     await client.tree.sync()
```

`client.tree` is the **command tree** — the registry of all slash commands.
`.sync()` uploads that list to Discord so the commands actually appear when
users type `/`. The `await` waits for that upload to finish. Discord
rate-limits this call, which is exactly why it lives here and not in
*on_ready* below: *on_ready* fires again after every reconnect, and re-syncing
on each one would eventually get the bot throttled.

```python
127: client.setup_hook = setup_hook
```

Plugs the function into the bot's `setup_hook` slot. Note there are no
parentheses after `setup_hook` — we're handing over the function itself, not
calling it. discord.py will call it at the right moment.

```python
131: @client.event
132: async def on_ready():
133:     print(f"logged in as {client.user}")
```

A **decorator** is a line starting with `@` that wraps the function below it.
`@client.event` tells discord.py: "when the *ready* event fires, call this
function." The *ready* event fires when the bot finishes connecting — and again
after any automatic reconnect, so this handler is kept side-effect-free.

Line 133 is an **f-string** (the `f` before the quote), which lets you embed
variables inside text using `{...}`. This prints something like
`logged in as o_mimico#9943` — the line you look for to confirm the bot is
online.

---

## 8. Reacting to chat — `on_message` (lines 136–181)

```python
136: @client.event
137: async def on_message(message):
```

Another event handler. This one fires for **every message the bot can see**.
`message` is the object describing that message.

```python
138:     global heat
```

Declares `heat` is the module global (it will be modified below).

```python
141:     if message.author.bot or message.guild is None:
142:         return
```

- **141** — Two reasons to ignore the message, joined by `or` (either one is
  enough to skip):
  - `message.author.bot` is true if the *sender* is a bot — the bot must never
    respond to itself or other bots, or it would trigger an infinite echo.
  - `message.guild is None` means the message is a **direct message** (DMs have
    no guild). The bot only talks in servers.
- **142** — `return` exits the function early, doing nothing.

```python
145:     if not users and server is None:
146:         return
```

`not users` is true when the users dictionary is empty. This says: if we have
learned *no one* and *no server*, there's nothing to imitate, so stay quiet.

```python
149:     heat += 1
```

`+=` means "add and store back" — same as `heat = heat + 1`. Every real message
nudges the pressure up by one.

```python
152:     chance = min(heat * 0.01, 0.25)
```

Computes the probability the bot will spontaneously reply: 1% per message of
pressure, capped at 25%. `min(a, b)` returns the smaller of the two, so no
matter how high `heat` climbs, `chance` never exceeds 0.25.

```python
154:     if random.random() < chance:
```

`random.random()` returns a random number between 0 and 1. Comparing it to
`chance` is a coin flip with that probability: it's true `chance`-fraction of
the time. If the flip fails, the function silently ends here.

```python
156:         heat = 0
```

The bot has decided to speak, so reset the pressure to zero — otherwise it
would keep firing on every message after a busy burst.

```python
159:         if mode == "server" and server is not None:
```

If the bot is in "server" mode *and* a server has actually been learned,
speak as the whole server...

```python
160:             media = server.get("media", [])
161:             if media and random.random() < 0.3:
162:                 await message.channel.send(random.choice(media))
163:                 return
164:             await message.channel.send(generate_sentence(server["chain"]))
165:             return
```

- **160** — Fetch the server's saved media list (or `[]` if none).
- **161** — 30% of the time (and only if there *is* media), send a random saved
  image/GIF URL instead of text.
- **162** — `channel.send` posts a message to the same channel. `await` pauses
  until it's sent.
- **163** — `return` ends the function after the media reply.
- **164–165** — Otherwise generate a sentence from the server's chain and send
  that, then return.

```python
168:         if not users:
169:             return
```

A safety guard: if we reach this point (not server mode) but there are no
learned users, do nothing. Without it, the next line would try to pick from an
empty dictionary and crash.

```python
172:         uid = random.choice(list(users.keys()))
```

`users.keys()` returns a view of every user id. `list(...)` turns that view
into a list (required, because `random.choice` needs a sequence). The result is
one random user id stored in `uid`.

```python
175:         media = users[uid].get("media", [])
176:         if media and random.random() < 0.3:
177:             await message.channel.send(random.choice(media))
178:             return
179:
180:         sentence = generate_sentence(users[uid]["chain"])
181:         await message.channel.send(sentence)
```

Exactly the same media-or-text logic as the server branch, but for the chosen
user: fetch their media, 30% chance to re-send one, otherwise generate and send
a sentence from their chain.

---

## 9. Building the chain — `build_markov_chain(messages)` (lines 184–200)

This is the heart of the whole bot. It takes a list of message texts and
returns a Markov chain.

```python
184: # Map each (word, word) pair to a list of words that follow it.
187: def build_markov_chain(messages):
188:     chain = {}
```

`chain` starts as an empty dictionary. Its final shape is:

```
("like", "strong")  ->  ["coffee", "tea", "drinks", ...]
```

```python
190:     for message in messages:
191:         words = message.split()
```

- **190** — Loop over every message text.
- **191** — `.split()` breaks a string into a list of words, splitting on
  whitespace (spaces, tabs, newlines). `"i like coffee"` becomes
  `["i", "like", "coffee"]`.

```python
195:         for i in range(len(words) - 2):
196:             key = (words[i], words[i + 1])
197:             next_word = words[i + 2]
198:             chain.setdefault(key, []).append(next_word)
```

- **195** — `len(words)` is the word count. `range(n)` produces the numbers
  `0, 1, ..., n-1`. So `range(len(words) - 2)` yields just enough starting
  positions for every overlapping three-word group. A 5-word message has 3 such
  positions; a 2-word message has 0, so the loop body never runs (short
  messages are naturally ignored).
- **196** — The **key** is a tuple of the current word and the next one.
- **197** — The word *after* that pair.
- **198** — `setdefault(key, [])` returns the list already stored under `key`,
  or creates and stores an empty list if the key is new. `.append(next_word)`
  then adds this occurrence to that list. The net effect: every time a pair is
  followed by a word, that word is recorded.

```python
200:     return chain
```

Hand the finished chain back to the caller.

---

## 10. Generating a sentence — `generate_sentence(chain, max_words=30)` (lines 203–235)

```python
203: # Walk the chain from a random start pair until a dead end or max_words.
207: def generate_sentence(chain, max_words=30):
```

`max_words=30` is a **default argument**: callers may omit it and get 30.

```python
208:     if not chain:
209:         return ""
```

An empty chain (no learned data) produces an empty string rather than crashing.

```python
211:     key = random.choice(list(chain.keys()))
212:     words = [key[0], key[1]]
```

- **211** — Pick a random starting pair from the chain's keys.
- **212** — Seed the output with that pair's two words. `key[0]` is the first
  word, `key[1]` the second.

```python
215:     vocabulary = {word for pair, followers in chain.items() for word in (pair[0], pair[1], *followers)}
```

A **set comprehension** that builds the set of every distinct word appearing
anywhere in the chain — as a key's first word, key's second word, or a follower.
The `*followers` "unpacks" the follower list into the surrounding tuple. This
set is used for the entropy jump at line 228.

```python
217:     while len(words) < max_words:
```

A `while` loop repeats *as long as* its condition holds. Here it keeps adding
words until the sentence reaches `max_words` (or the loop breaks early).

```python
218:         next_words = chain.get(key)
```

`chain.get(key)` returns the follower list for the current pair, or `None` if
that pair was never seen. (`get` is like square brackets but returns `None`
instead of raising an error when the key is missing.)

```python
221:         if not next_words:
222:             break
```

If there are no followers (a **dead end** — the user never wrote that pair),
`break` exits the loop. This is what guarantees the loop always terminates.

```python
224:         # Pick from unique followers so common words don't dominate.
225:         next_word = random.choice(list(set(next_words)))
```

`set(next_words)` removes duplicates, so a word the user wrote 100 times is no
more likely than one written once — every *distinct* follower is equally likely.
`list(...)` wraps it for `random.choice`.

```python
227:         # 40% of the time, jump to a random vocabulary word for extra entropy.
228:         if random.random() < 0.4:
229:             next_word = random.choice(list(vocabulary))
```

40% of the time, throw away the chain's suggestion and pick any word from the
whole vocabulary instead. This loosens the bot's mimicry so it drifts away from
exact quotes the user wrote, sounding fresher.

```python
231:         words.append(next_word)
233:         key = (key[1], next_word)
```

- **231** — Add the chosen word to the output.
- **233** — Slide the window: the new pair is *(old second word, new word)*.
  This is what "walks" the chain.

```python
235:     return " ".join(words)
```

`" ".join(list)` glues the words together with single spaces, producing the
final sentence string.

---

## 11. Media — `extract_media(message)` (lines 238–251)

```python
238: _GIF_HOSTS = ("tenor.com", "giphy.com", "media.tenor.com")
```

A tuple of website names that host GIFs. The leading underscore is a convention
meaning "internal — don't touch".

```python
244: def extract_media(message):
245:     urls = []
```

Starts with an empty list of found URLs.

Notice what is *not* here: the function never looks at `message.attachments`
(files uploaded directly to Discord). Attachment URLs are **signed and expire
after roughly a day**, so saving them to *memory.json* would leave the bot
re-posting dead links later. Only stable, public GIF links are kept.

```python
247:     for word in message.content.split():
248:         if word.startswith("http") and any(host in word for host in _GIF_HOSTS):
249:             urls.append(word)
```

- **247** — Split the message text into words and loop over them.
- **248** — Capture a word only if it starts with "http" **and** contains one of
  the known GIF hosts. `any(...)` returns `True` if at least one check passes —
  here, "does any host string appear inside this word?"
- **249** — Add that URL.

```python
251:     return urls
```

Return everything collected.

---

## 12. The user scan — `run_mimic(interaction, targets, channels)` (lines 254–365)

This is the big one. It scans the selected channels, learns one chain per
target user, saves them, and replies with a sample sentence each.

```python
254: # Scan channels, build one chain per target user, store + save, then reply.
255: async def run_mimic(interaction, targets, channels):
260:     progress = await interaction.channel.send("Snooping through messages...")
```

- **255** — `targets` is the list of users to learn; `channels` is the list of
  text channels to read.
- **260** — Posts an initial "working on it…" message and keeps a handle to it
  in `progress` so it can be updated later. It is sent as an ordinary message
  in the channel (`interaction.channel.send`) rather than as an interaction
  *followup*. The reason is a Discord rule: the token behind an interaction
  stops working **15 minutes** after the command was used. A full history scan
  of a busy server can easily take longer than that, and a followup sent after
  the deadline simply fails. Plain channel messages have no such expiry.

```python
262:     target_ids = {user.id for user in targets}
263:     messages_by_user = {user.id: [] for user in targets}
264:     media_by_user = {user.id: [] for user in targets}
265:     read_count = 0
```

- **262** — A set of the target users' ids, for fast membership checks.
- **263–264** — Two dictionaries, each keyed by user id, that will accumulate
  each target's text messages and media URLs separately.
- **265** — A shared counter for how many messages have been read, used by the
  progress display.

### The per-channel scanner

```python
267:     async def scan_channel(channel):
268:         nonlocal read_count
```

An inner `async def` — a helper defined inside *run_mimic* so it can see the
surrounding variables. `nonlocal read_count` says "the `read_count` I update is
the one in the enclosing function, not a local copy" (the async equivalent of
the `global` statement from line 120).

```python
269:         channel_messages = {uid: [] for uid in target_ids}
270:         channel_media = {uid: [] for uid in target_ids}
271:         try:
273:             async for message in channel.history(limit=None):
```

- **269–270** — Local collectors for *this one* channel.
- **271** — A `try` block, so the permission failure on line 281 can be caught.
- **273** — `channel.history(limit=None)` is an **async iterator** that yields
  the channel's messages, oldest to newest. `limit=None` means "keep going to
  the very beginning — every message ever". The `async for` loop visits each
  message one at a time, `await`ing the network between pages automatically.

```python
274:                 read_count += 1
276:                 if message.author.id in target_ids:
277:                     if message.content.strip():
278:                         channel_messages[message.author.id].append(message.content)
280:                     channel_media[message.author.id].extend(extract_media(message))
```

- **274** — Bump the global progress counter by one for every message read.
- **276** — Only care if this message's author is one of the targets.
- **277** — `.strip()` removes leading/trailing whitespace. If anything remains
  (truthy), the message has real text.
- **278** — Save that text under the author's id.
- **280** — Collect any media from the message (this runs even for blank-text
  messages, since an image-only post still has a URL worth remembering).
  `.extend` appends a whole list at once.

```python
281:         except discord.HTTPException as exc:
284:             print(f"skipping #{channel.name}: {exc!r}")
285:         return channel_messages, channel_media
```

- **281** — If anything goes wrong talking to Discord for this channel, catch
  it here so one bad channel can't abort the whole scan. `HTTPException` is
  the parent of every Discord API error, so this covers a missing "Read
  Message History" permission (`Forbidden`), a channel deleted mid-scan
  (`NotFound`), and similar. The `as exc` keeps the error object in `exc`.
- **284** — Log which channel was skipped and why, then move on. (Anything
  the bot *did* manage to read from this channel before the error is kept.)
- **285** — Return this channel's collected data.

### The progress ticker

```python
289:     async def progress_loop():
290:         while True:
291:             await asyncio.sleep(1)
292:             await progress.edit(
293:                 content=f"Snooping through messages... {read_count} read"
294:             )
```

A loop that runs forever until cancelled: sleep one second (letting the scan do
its work), then edit the progress message to show the latest count. Editing a
Discord message is a network call, so doing it once a second (not once per
message) keeps the API happy while still looking alive.

```python
296:     progress_task = asyncio.create_task(progress_loop())
```

`create_task` schedules the ticker to run in the background, *concurrently*
with the scan. It returns a handle (`progress_task`) used later to stop it.

```python
298:     try:
300:         results = await asyncio.gather(*(scan_channel(ch) for ch in channels))
```

- **300** — `asyncio.gather` runs several coroutines at the same time and
  returns all their results in order. The `*(...)` "splats" the generator of
  `scan_channel(ch)` calls into separate arguments. The upshot: every channel is
  scanned in parallel, so the total time is about that of the *slowest* channel,
  not the sum of all of them.

```python
301:     finally:
303:         progress_task.cancel()
304:         try:
305:             await progress_task
306:         except asyncio.CancelledError:
307:             pass
```

- **301** — A `finally` block always runs, success or failure.
- **303** — Cancel the ticker once the scan is done.
- **304–307** — Await the cancelled task to "collect" it. Cancelling raises
  `asyncio.CancelledError` inside the task; catching and ignoring it here avoids
  an ugly "task was destroyed" warning in the console.

### Merging results

```python
310:     for channel_messages, channel_media in results:
311:         for uid in target_ids:
312:             messages_by_user[uid].extend(channel_messages[uid])
313:             media_by_user[uid].extend(channel_media[uid])
```

Each `results` entry is a `(messages, media)` pair from one channel. These
loops merge each channel's per-user data into the master dictionaries.

```python
315:     total_messages = sum(len(m) for m in messages_by_user.values())
```

`sum` adds up the length of every user's message list — the total number of
*target* messages found.

```python
316:     await progress.edit(
317:         content=(
318:             f"Snooping complete! {total_messages} messages.\n"
319:             f"Messages read: {read_count}"
320:         )
321:     )
```

Replaces the progress message with the final tally. `\n` is a newline. Note
`total_messages` counts only target messages, while `read_count` counts every
message scanned (including other people's).

### Learning and storing each target

```python
323:     global users, current_user_id
324:     results = []
```

- **323** — Declares the two globals that will be written below.
- **324** — Reuses the name `results` for the output lines (the previous
  `results` is no longer needed).

```python
327:     for user in targets:
328:         messages = messages_by_user[user.id]
329:
331:         if not messages:
332:             results.append(
333:                 f"{user.mention}: not enough messages to imitate. Were they even online?"
334:             )
335:             continue
```

- **327** — Handle each target one at a time.
- **331–334** — If the user had no messages, add a teasing note to the output
  and `continue` (skip straight to the next target).
- **333** — `user.mention` renders as a clickable `@Name` in Discord.

```python
337:         chain = build_markov_chain(messages)
338:
340:         if not chain:
341:             results.append(
342:                 f"{user.mention}: couldn't learn their style "
343:                 "(messages too short)."
344:             )
345:             continue
```

- **337** — Build the user's chain.
- **340–345** — If the chain is empty (all their messages were too short to
  form any three-word group), report it and skip.

```python
348:         users[str(user.id)] = {
349:             "name": user.display_name,
350:             "chain": chain,
351:             "media": media_by_user[user.id],
352:         }
353:         current_user_id = str(user.id)
```

- **348** — Store the learned data under the user's id. `str(user.id)` converts
  the numeric id to a string, because JSON object keys must be strings (and
  keeping them consistent avoids subtle bugs).
- **349** — Save their display name (how they appear in the server).
- **353** — Mark this user as the "current" one that `/speak` will imitate.

```python
356:         sentence = generate_sentence(chain)
357:         results.append(f"**{user.display_name}**: {sentence}")
```

- **356** — Generate a sample sentence as proof of learning.
- **357** — Format it as `**Name**: sentence` (the `**` makes the name bold in
  Discord's Markdown).

```python
359:     save_users()
360:
363:     await interaction.channel.send(
364:         "\n".join(results), allowed_mentions=discord.AllowedMentions.none()
365:     )
```

- **359** — Persist everything to disk.
- **363–365** — Send one line per target, joined by newlines, as a single
  message. Again a plain channel message rather than a followup, because the
  scan may have outlived the 15-minute interaction token. Some of the result
  lines contain `user.mention` (line 333); `AllowedMentions.none()` tells
  Discord to render those as `@Name` **without** actually pinging anyone.

---

## 13. The server scan — `run_server_mimic(interaction, channels)` (lines 368–454)

Nearly identical to *run_mimic*, but instead of separating messages by author,
it merges *everyone's* messages into a single combined chain.

```python
368: # Scan channels, merge EVERYONE's messages into one chain, then switch mode.
370: async def run_server_mimic(interaction, channels):
372:     progress = await interaction.channel.send("Snooping through messages...")
373:
374:     all_messages = []
375:     all_media = []
376:     read_count = 0
```

- **372** — The same plain-channel-message progress post as *run_mimic* (see
  section 12 for why it isn't a followup).
- **374–375** — Unlike *run_mimic*, just two flat lists, since there is only
  one "personality" being built: the whole server.

```python
378:     async def scan_channel(channel):
379:         nonlocal read_count
380:         channel_messages = []
381:         channel_media = []
382:         try:
383:             async for message in channel.history(limit=None):
384:                 read_count += 1
386:                 if message.author.bot:
387:                     continue
389:                 if message.content.strip():
390:                     channel_messages.append(message.content)
391:                 channel_media.extend(extract_media(message))
392:         except discord.HTTPException as exc:
393:             print(f"skipping #{channel.name}: {exc!r}")
394:         return channel_messages, channel_media
```

Lines 392–393 are the same broad `HTTPException` catch as *run_mimic*'s
scanner. The only real difference is line 386: messages from
bots are skipped entirely (we don't want the mimicry to learn from other bots),
and every *human* message is appended to the shared list regardless of who
wrote it.

```python
396:     async def progress_loop():
397:         while True:
398:             await asyncio.sleep(1)
399:             await progress.edit(
400:                 content=f"Snooping through messages... {read_count} read"
401:             )
402:
403:     progress_task = asyncio.create_task(progress_loop())
404:
405:     try:
406:         results = await asyncio.gather(*(scan_channel(ch) for ch in channels))
407:     finally:
408:         progress_task.cancel()
409:         try:
410:             await progress_task
411:         except asyncio.CancelledError:
412:             pass
```

Identical progress-ticker and concurrent-scan machinery to lines 289–307.

```python
414:     for channel_messages, channel_media in results:
415:         all_messages.extend(channel_messages)
416:         all_media.extend(channel_media)
```

Merge every channel's messages and media into the two flat lists.

```python
418:     total_messages = len(all_messages)
419:     await progress.edit(
420:         content=(
421:             f"Snooping complete! {total_messages} messages.\n"
422:             f"Messages read: {read_count}"
423:         )
424:     )
```

Report the final tally.

```python
426:     global mode, server
```

Declare the two globals that will be written below.

```python
429:     if not all_messages:
430:         await interaction.channel.send(
431:             "Couldn't read anyone's messages. Is this server a ghost town?"
432:         )
433:         return
```

If no human messages were found (empty server, or only bots), say so and stop.

```python
435:     chain = build_markov_chain(all_messages)
436:
438:     if not chain:
439:         await interaction.channel.send(
440:             "Couldn't learn this server's style (messages too short)."
441:         )
442:         return
```

Build the combined chain; bail out if it's empty (all messages too short).

```python
445:     server = {
446:         "name": interaction.guild.name,
447:         "chain": chain,
448:         "media": all_media,
449:     }
450:     mode = "server"
451:     save_users()
```

- **445–449** — Package the server's name (the guild's name), chain, and media
  into the global `server` dictionary.
- **450** — Switch the bot into "server" mode.
- **451** — Save everything, including the new mode and server, to disk.

```python
453:     sentence = generate_sentence(chain)
454:     await interaction.channel.send(f"**{interaction.guild.name}**: {sentence}")
```

Generate a sample sentence and reply with the guild name in bold.

---

## 14. The channel picker — `ChannelPicker` (lines 457–513)

A **View** is Discord's name for a container of interactive components
(buttons, dropdowns) attached to a message. This class is a custom View with a
channel dropdown and an "all channels" button.

```python
457: # Multi-select channel dropdown + an "all channels" button.
458: class ChannelPicker(discord.ui.View):
```

`class` defines a new type. `discord.ui.View` in parentheses means "this class
**inherits** from View" — it starts with all of View's behavior and adds its
own. A class is a blueprint; it isn't used until you create an *instance* of it
(see line 537).

```python
459:     def __init__(self, users, server_mode=False):
460:         super().__init__(timeout=300)
461:         self.users = users
463:         self.server_mode = server_mode
```

- **459** — `__init__` is the **constructor**, called automatically when an
  instance is created. It takes the list of target users and an optional
  `server_mode` flag (default `False`).
- **460** — `super().__init__(...)` calls the *parent* View's constructor, which
  must run first. `timeout=300` means "if no one interacts within 300 seconds
  (5 minutes), disable the buttons".
- **461–463** — Store the arguments on `self` so the other methods below can
  reach them. `self` refers to "this particular instance".

### The dropdown

```python
465:     @discord.ui.select(
466:         cls=discord.ui.ChannelSelect,
467:         channel_types=[discord.ChannelType.text],
468:         min_values=1,
469:         max_values=25,
470:         placeholder="Pick which channels to read",
471:     )
```

`@discord.ui.select(...)` is a decorator that turns the function below into a
**dropdown component**. Its arguments configure that dropdown:

- **466** — `ChannelSelect` makes it a native channel picker (Discord shows a
  searchable list of channels).
- **467** — Restrict choices to *text* channels only (no voice channels).
- **468–469** — Require 1 to 25 selections.
- **470** — The grey hint text shown before anything is selected.

```python
472:     async def on_select(self, interaction, select):
```

This function runs when the user *picks* channels. Discord passes a fresh
`interaction` and a `select` object describing the selection.

```python
474:         await interaction.response.defer()
```

**Deferring** tells Discord "I got your input, give me time to think." It
prevents the interaction from timing out while the (potentially long) scan runs.
Without this, Discord shows the dreaded "application did not respond".

```python
480:         channels = [
481:             interaction.guild.get_channel(channel.id) for channel in select.values
482:         ]
```

A subtle but crucial detail: `select.values` holds *lightweight* channel
objects that lack a `.history()` method. This list comprehension maps each one
through `interaction.guild.get_channel(...)`, which returns the full text-channel
object that *does* support history. The square brackets produce a list of those
full channels.

```python
483:         channels = [ch for ch in channels if ch is not None]
484:         if not channels:
485:             await interaction.followup.send("I couldn't find any of those channels.")
486:             return
```

- **483** — `get_channel` returns `None` when it doesn't know the channel
  (deleted a moment ago, or simply not in the bot's cache). This comprehension
  keeps only the real channels. Without it, a single `None` would crash
  *scan_channel*, and because all channels are scanned together with `gather`,
  that one failure would abort the *entire* scan.
- **484–486** — If nothing survived the filter, tell the user and stop. A
  followup is fine here because we're still well inside the 15-minute window.

```python
487:         if self.server_mode:
488:             await run_server_mimic(interaction, channels)
489:         else:
490:             await run_mimic(interaction, self.users, channels)
```

Branch on the stored flag: run the server scan or the user scan accordingly.

### The button

```python
492:     @discord.ui.button(
493:         label="All the channels", style=discord.ButtonStyle.primary, row=1
494:     )
495:     async def all_channels(self, interaction, button):
```

`@discord.ui.button` turns this function into a clickable button. `label` is its
text, `style` colors it (primary = the bold accent color), and `row=1` puts it
on the second row, below the dropdown.

```python
496:         await interaction.response.defer()
497:         channels = list(interaction.guild.text_channels)
498:         if self.server_mode:
499:             await run_server_mimic(interaction, channels)
500:         else:
501:             await run_mimic(interaction, self.users, channels)
```

Same as the dropdown callback, but it simply grabs *every* text channel in the
guild at once (`guild.text_channels`).

### The view's own error handler

```python
506:     async def on_error(self, interaction, error, item):
507:         print(f"ERROR in ChannelPicker ({item}): {error!r}")
508:         try:
509:             await interaction.channel.send(
510:                 "Something went wrong while snooping. Blame the robot, not me."
511:             )
512:         except Exception:
513:             pass
```

The global slash-command error hook (section 23) does **not** see errors that
happen inside a View's callbacks — Views have their own hook, and overriding
`on_error` is how you use it. `item` is the component (dropdown or button)
that was being handled.

- **507** — Print the real error for the owner.
- **508–513** — Try to tell the user; if even that fails (channel gone,
  permissions changed), give up quietly rather than crash the error handler.

Without this, a crash mid-scan would only show up in the terminal, and the
user would be left staring at a "Snooping through messages…" post that never
finishes.

---

## 15. Slash command — `/mimic` (lines 517–538)

```python
517: @client.tree.command(name="mimic", description="I'm about to become someone!!!")
```

`@client.tree.command(...)` registers the function below as a slash command
named `mimic`. The `description` is the help text Discord shows next to the
command name.

```python
518: async def mimic(
519:     interaction: discord.Interaction,
520:     user: discord.Member,
521:     user2: discord.Member = None,
522:     user3: discord.Member = None,
523:     user4: discord.Member = None,
524: ):
```

The command's parameters. Discord turns each parameter into a field the user
fills in:

- **519** — `interaction` is always provided by Discord and describes who ran
  the command and where. The `: discord.Interaction` part is a **type hint** —
  documentation only, not enforced at runtime.
- **520–523** — One required `user` parameter, then three optional ones
  defaulting to `None`. This is the standard workaround for a Discord
  limitation: app commands can't take a variable-length list of members, so we
  ask for up to four named slots instead.

```python
526:     if interaction.guild is None:
527:         await interaction.response.send_message("This command only works in a server.")
528:         return
```

Reject direct-message usage: there's no guild (and no channels) to scan in a DM.

```python
531:     targets = [u for u in (user, user2, user3, user4) if u is not None]
```

A **list comprehension** that collects the non-`None` members. If someone ran
`/mimic @Alice @Bob`, this yields `[@Alice, @Bob]` (the unused slots are `None`
and filtered out).

```python
534:     names = ", ".join(u.mention for u in targets)
```

Builds a string like `@Alice, @Bob` by joining each target's mention.

```python
535:     await interaction.response.send_message(
536:         f"Which channels should I read to imitate {names}?",
537:         view=ChannelPicker(targets),
538:     )
```

Sends the prompt, attaching a freshly created *ChannelPicker* (with the targets)
as the `view`. This is the message that shows the dropdown and button. The scan
itself happens later, inside the picker's callbacks.

---

## 16. Slash command — `/servermimic` (lines 542–552)

```python
542: @client.tree.command(name="servermimic", description="Become the whole server")
543: async def servermimic(interaction: discord.Interaction):
544:     if interaction.guild is None:
545:         await interaction.response.send_message("This command only works in a server.")
546:         return
547:
549:     await interaction.response.send_message(
550:         "Which channels should I read to imitate this server?",
551:         view=ChannelPicker([], server_mode=True),
552:     )
```

Almost identical to `/mimic`, but:

- It has no user parameter — it learns from everyone.
- Line 510 creates a *ChannelPicker* with an **empty** users list and
  `server_mode=True`, which flips every callback into server-scan mode.

---

## 17. Slash command — `/mode` (lines 556–574)

```python
556: @client.tree.command(name="mode", description="Switch who I talk as")
557: async def set_mode(
558:     interaction: discord.Interaction,
559:     new_mode: Literal["user", "server"],
560: ):
```

- **559** — `Literal["user", "server"]` tells Discord this parameter accepts
  only those two exact strings, which Discord renders as a neat two-option
  dropdown rather than a free-text box.

```python
561:     global mode
562:
564:     if new_mode == "server" and server is None:
565:         await interaction.response.send_message(
566:             "I haven't learned this server yet. Use /servermimic first!"
567:         )
568:         return
```

- **561** — Allow writing the global `mode`.
- **564–568** — If the user tries to switch to "server" before any server has
  been learned, stop them with a helpful hint.

```python
570:     mode = new_mode
571:     save_users()
```

Set the new mode and persist it, so the choice survives a restart.

```python
573:     label = "the whole server" if mode == "server" else "the imitated user"
574:     await interaction.response.send_message(f"Switched! Now I talk as {label}.")
```

Pick a human-friendly label and confirm the switch. (This conditional expression
is the same `A if cond else B` form seen at line 54.)

---

## 18. Slash command — `/speak` (lines 578–612)

```python
578: @client.tree.command(name="speak", description="Speak as the imitated user")
579: async def speak(interaction: discord.Interaction):
```

```python
581:     if mode == "server":
```

If the bot is in server mode, take the server path first.

```python
582:         if server is None:
583:             await interaction.response.send_message(
584:                 "I haven't learned this server yet. Use /servermimic first!"
585:             )
586:             return
```

Guard: server mode with no learned server (shouldn't happen normally, but be
safe).

```python
589:         media = server.get("media", [])
590:         if media and random.random() < 0.3:
591:             await interaction.response.send_message(random.choice(media))
592:             return
593:
594:         await interaction.response.send_message(generate_sentence(server["chain"]))
595:         return
```

30% media re-send, otherwise a generated sentence — the same pattern as the
spontaneous handler.

```python
598:     if current_user_id is None or current_user_id not in users:
599:         await interaction.response.send_message(
600:             "I'm not pretending to be anyone yet. Use /mimic first!"
601:         )
602:         return
```

User-mode guard: if there's no current user (or the id somehow isn't in `users`),
tell the user how to start.

```python
604:     sentence = generate_sentence(users[current_user_id]["chain"])
605:
607:     media = users[current_user_id].get("media", [])
608:     if media and random.random() < 0.3:
609:         await interaction.response.send_message(random.choice(media))
610:         return
611:
612:     await interaction.response.send_message(sentence)
```

Generate a sentence, then 30% of the time swap it for a random saved media URL.

---

## 19. Slash command — `/users` (lines 617–636)

```python
617: @client.tree.command(name="users", description="Who have I been spying on?")
618: async def list_users(interaction: discord.Interaction):
```

Note the function is named *list_users*, not *users* — a deliberate choice, so
it doesn't shadow (hide) the global `users` dictionary.

```python
619:     if not users and server is None:
620:         await interaction.response.send_message(
621:             "I don't know anyone yet. Go run /mimic on somebody!"
622:         )
623:         return
```

If nothing has been learned at all, say so.

```python
625:     lines = []
626:     for uid, info in users.items():
628:         marker = " *" if (uid == current_user_id and mode == "user") else ""
629:         lines.append(f"**{info['name']}**{marker}")
```

- **625** — A list to hold one display line per entry.
- **626** — Loop over every learned user.
- **628** — Append a `" *"` marker only to the current user — and only when in
  "user" mode (so the server line gets the marker instead when in server mode).
- **629** — Format as a bold name, plus the optional marker.

```python
632:     if server is not None:
633:         server_marker = " *" if mode == "server" else ""
634:         lines.append(f"**{server['name']}** (server){server_marker}")
```

If a server has been learned, list it too, labelled "(server)" and marked when
it's the active mode.

```python
636:     await interaction.response.send_message("\n".join(lines))
```

Send the whole list as one message, one name per line.

---

## 20. Slash command — `/converse` (lines 640–672)

```python
640: @client.tree.command(
641:     name="converse", description="Simulate a conversation between two users"
642: )
643: async def converse(
644:     interaction: discord.Interaction,
645:     user1: discord.Member,
646:     user2: discord.Member,
647: ):
```

A command taking exactly two members.

```python
649:     missing = []
650:     if str(user1.id) not in users:
651:         missing.append(user1.mention)
652:     if str(user2.id) not in users:
653:         missing.append(user2.mention)
654:
655:     if missing:
656:         await interaction.response.send_message(
657:             "I haven't analyzed: " + ", ".join(missing) + ". Use /mimic first!"
658:         )
659:         return
```

- **649–653** — Check whether each user has been learned; collect the mentions
  of any that haven't.
- **655–659** — If either is missing, list them and stop.

```python
661:     chain1 = users[str(user1.id)]["chain"]
662:     chain2 = users[str(user2.id)]["chain"]
663:     name1 = users[str(user1.id)]["name"]
664:     name2 = users[str(user2.id)]["name"]
```

Fetch each user's chain and display name. `users[str(id)]["chain"]` reads two
nested keys: first the user id, then `"chain"`.

```python
667:     lines = []
668:     for _ in range(5):
669:         lines.append(f"**{name1}**: {generate_sentence(chain1)}")
670:         lines.append(f"**{name2}**: {generate_sentence(chain2)}")
671:
672:     await interaction.response.send_message("\n".join(lines))
```

- **668** — `range(5)` runs the loop five times. The loop variable is `_`, a
  convention meaning "I don't actually need this value."
- **669–670** — Each round, generate one line for each user.
- **672** — Send all ten lines (5 rounds × 2 users) at once.

---

## 21. Slash command — `/ask` (lines 677–709)

The one command that *does* use an AI model. It asks Claude a question and has
it answer in the voice of whoever the bot is currently imitating. The Markov
chain still matters: a few generated sentences are shown to Claude as style
examples.

```python
677: @client.tree.command(name="ask", description="Ask the active persona anything (Claude)")
678: async def ask(interaction: discord.Interaction, question: str):
```

One required parameter, `question`, typed as `str` — Discord shows it as a
free-text field.

```python
680:     if mode == "server" and server is not None:
681:         name, chain = server["name"], server["chain"]
682:     elif mode == "user" and current_user_id in users:
683:         name, chain = users[current_user_id]["name"], users[current_user_id]["chain"]
684:     else:
685:         await interaction.response.send_message(
686:             "I'm not pretending to be anyone yet. Use /mimic or /servermimic first!"
687:         )
688:         return
```

Resolve the active persona using the same rules as `/speak` (section 18).
`name, chain = a, b` assigns two variables at once. If neither branch applies,
say so and stop.

```python
691:     await interaction.response.defer()
```

Talking to Claude takes a few seconds, longer than Discord's 3-second limit for
a first reply — so defer (the same trick as line 474).

```python
694:     samples = "\n".join(
695:         f"- {generate_sentence(chain)}" for _ in range(3)
696:     )
```

Generate three sentences from the chain and join them into a bulleted block.
`_` is the conventional name for a loop variable you don't use.

```python
697:     system_prompt = (
698:         f"You are {name}, a member of this Discord server. "
699:         f"Answer in first person, briefly, matching {name}'s tone and vocabulary. "
700:         f"Sample messages {name} has written, to copy the style:\n{samples}"
701:     )
```

The **system prompt** — standing instructions for the model. Adjacent string
literals inside parentheses are glued together by Python, so this is one long
string. It names the persona, asks for brevity and first person, and pastes in
the samples.

```python
704:     try:
705:         answer = await asyncio.to_thread(claude_reply, system_prompt, question)
706:     except Exception as exc:
707:         answer = f"Claude call failed: {exc}"
```

- **705** — *claude_reply* (next section) is an ordinary, **blocking** function:
  while it waits on the network, nothing else could run. `asyncio.to_thread`
  runs it in a background thread and `await`s the result, so the bot stays
  responsive to everything else meanwhile.
- **706–707** — Any failure (bad key, network down, model name wrong) becomes
  the answer text instead of a crash.

```python
709:     await interaction.followup.send(f"**{name}**: {answer}")
```

Reply with the persona's name in bold, followed by Claude's answer.

---

## 22. Talking to Claude — `claude_reply` (lines 714–728)

```python
714: def claude_reply(system_prompt: str, question: str) -> str:
715:     import anthropic
```

A regular (non-async) function. The `-> str` after the parentheses is a
**return annotation**: a note that it returns a string. Line 715 imports the
*anthropic* library *inside* the function rather than at the top of the file —
a **lazy import** — so the bot still starts even if that package isn't
installed; only `/ask` would fail.

```python
717:     api_key = os.getenv("ANTHROPIC_API_KEY")
718:     if not api_key:
719:         return "ANTHROPIC_API_KEY is not set. Add it to secrets.env to use /ask."
```

Read the API key from the environment (loaded from *secrets.env* on line 11)
and return a helpful message if it's missing.

```python
721:     client = anthropic.Anthropic(api_key=api_key)
722:     message = client.messages.create(
723:         model=CLAUDE_MODEL,
724:         max_tokens=300,
725:         system=system_prompt,
726:         messages=[{"role": "user", "content": question}],
727:     )
```

- **721** — Create an API client. (This local `client` shadows the global
  Discord `client` *inside this function only* — a little confusing, but
  harmless because the function never needs the Discord one.)
- **722–727** — The actual request: which model (line 45), a cap of 300 tokens
  (roughly 200 words), the system prompt, and the conversation — here just one
  user message containing the question.

```python
728:     return "".join(block.text for block in message.content if block.type == "text")
```

Claude's reply arrives as a list of content *blocks*. Keep only the text ones
and join them into a single string.

---

## 23. Error handling (lines 732–744)

```python
732: @client.tree.error
733: async def on_tree_error(interaction: discord.Interaction, error):
```

`@client.tree.error` registers a catch-all for any exception a slash command
throws. Discord hands the failed `interaction` and the `error` object.

```python
734:     print(f"ERROR in /{getattr(interaction.command, 'name', 'unknown')}: {error!r}")
735:     text = "Something went wrong. Blame the robot, not me."
```

- **734** — Prints the real error to the terminal, including the command name,
  so the owner can debug. The `getattr(..., 'unknown')` keeps this from
  crashing when an error fires before a command was identified (a rare edge
  case). The `!r` makes the error print with its exact type.
- **735** — The friendly message shown to the user, stored once so both
  branches below can use it.

```python
736:     try:
739:         if interaction.response.is_done():
740:             await interaction.followup.send(text)
741:         else:
742:             await interaction.response.send_message(text)
743:     except Exception:
744:         pass
```

An interaction must be answered in one of two ways, and picking the wrong one
fails: **before** any reply, you must use `response.send_message`; **after** a
reply (or a `defer`), you must use `followup.send`. `response.is_done()` says
which situation we're in, so the user gets the message either way. The outer
`try`/`except` swallows anything left (for example, the interaction token has
already expired), so the error handler itself never crashes.

---

## 24. Version and entry point (lines 748–754)

```python
748: __version__ = "0.3.0"
```

A plain string recording which release of the bot this is. The double
underscores follow a Python convention for module metadata. It is bumped on
each release, and *CHANGELOG.md* explains what changed between versions.

```python
752: if __name__ == "__main__":
753:     print(f"markov_bot_julia v{__version__}")
754:     client.run(token)
```

- **752** — `__name__` is a special variable Python sets automatically. It
  equals `"__main__"` only when the file is *run directly* (`python
  markov_bot_julia.py`), not when it is *imported* by another script. This guard
  means importing the file (for tests, for example) doesn't immediately try to
  connect to Discord.
- **753** — Print the version, so a terminal log always shows which build is
  running.
- **754** — `client.run(token)` logs in with the token and starts the bot's
  event loop. It **blocks** — the program sits here, handling events, until the
  bot is stopped.
