# How It Works

An under-the-hood guide to every mechanism in *markov_bot_julia.py*. This
document explains *why* each piece behaves the way it does; see
*LINE_BY_LINE.md* for a statement-by-statement breakdown of the code.

---

## 1. The big picture

The bot has one job: **read what a user writes, learn their style, and write
new messages in that style.** It does this with a statistical trick called a
*Markov chain*, plus supporting systems for media and persistence. One optional
command, /ask, additionally hands a question to Claude and uses the Markov
output as style examples (§10).

The full lifecycle looks like this:

1. Someone runs /mimic, targeting one user (or up to four at once) — or runs
   /servermimic to learn from *everyone* at once.
2. The bot asks *which channels* to scan (multi-select dropdown or "all").
3. It reads those channels' full history and keeps the relevant messages
   (one person's, or everyone's).
4. It builds a Markov chain from the words in those messages.
5. It saves the chain to *memory.json* so it survives restarts.
6. It generates a sample sentence and shows it.
7. Later, /speak, /converse, and spontaneous chat messages generate fresh
   text from the saved chains. The /mode command switches between speaking as
   the imitated user and speaking as the whole server. /ask answers a real
   question in the active persona's voice via Claude.

---

## 2. The Markov chain (the core idea)

A Markov chain answers one question: **"given the last two words, what word
usually comes next?"**

It learns this by sliding a 3-word window over every message:

```
"i like strong black coffee" produces three triples:

    (i, like)       →  strong
    (like, strong)  →  black
    (strong, black) →  coffee
```

Each observation is stored as:

```
(key)          -> (list of every next word ever seen)
("like","strong") -> ["coffee", "tea", "drinks", ...]
```

The chain is a dictionary whose keys are **pairs of words** (bigrams) and
whose values are **lists of follower words**. Bigrams (two words) are used
instead of single words because they give the output some grammar — "like
strong" almost always leads to a noun, which reads much better than a random
shuffle of words.

Human writing has strong local patterns: certain pairs of words are almost
always followed by a small set of next words. Remembering every pair and what
follows it lets the bot "walk" the chain and produce plausible sentences that
inherit the user's word choices, idioms, and rhythm.

---

## 3. Building the chain (*build_markov_chain*)

1. Split each message into words on whitespace.
2. For each overlapping triple *(w[i], w[i+1], w[i+2])*, record that
   *w[i+2]* can follow the pair *(w[i], w[i+1])*.

A message with *n* words produces *n − 2* triples. Messages with fewer than
three words produce nothing and are silently ignored — that is intentional,
because a 1- or 2-word message tells us nothing about word *order*.

A pair that appears many times accumulates repeated followers. Generation
deduplicates these (§5), so duplicates only contribute to the *set* of unique
followers — not to any word's weighting.

---

## 4. Generating a sentence (*generate_sentence*)

### Walking the chain

1. Pick a random starting pair *(a, b)* from the chain's keys.
2. Output starts with *[a, b]*.
3. Look up *(a, b)*'s follower list and pick a random next word *c*.
4. Append *c*; slide the window to *(b, c)* and repeat.

### Stopping conditions

The loop stops when either:

- The current pair has **no followers** (a "dead end" — the user never wrote
  that pair before), or
- the word limit is reached.

This guarantees the loop always terminates and never hangs.

### Entropy tricks (why output isn't repetitive)

A naive Markov generator reuses the user's most common phrases verbatim. Two
changes keep the output fresh:

1. **Unique followers.** The naive approach picks from the raw follower list,
   so a word that appears 100× is 100× more likely. Instead, the bot picks
   from the *set* of unique followers, making every distinct next word equally
   likely.

2. **Random vocabulary jump.** 40% of the time the bot ignores the chain and
   picks a word from the *entire* vocabulary at random. This breaks up
   over-learned patterns so sentences drift away from exact user quotes.

### Empty-chain guard

Generating from an empty chain returns an empty string instead of crashing —
important because a saved chain could theoretically be empty after a bad load.

---

## 5. Media — GIFs (*extract_media*)

The bot mimics not just *words* but *media habits*. Tenor/Giphy links often
appear as raw URLs in the message text; any "word" starting with "http" that
contains a known GIF host (tenor.com, giphy.com, media.tenor.com) is captured.

These URLs are saved with the user and later re-sent by /speak and the
spontaneous message handler (a 30% chance whenever the bot would otherwise say
something, falling back to text if the user has no saved media).

### Why direct uploads are *not* collected

Images uploaded straight to Discord get a CDN URL that is **signed and expires
after roughly 24 hours**. Saving those to *memory.json* would mean the bot
happily re-posts dead links a day later. Earlier versions did this; it was
removed on purpose. Only stable public GIF links are kept. (Re-uploading the
image bytes would work, but means downloading and storing files — out of scope
for now.)

---

## 6. Persistence (*save_users*, *load_users*)

Everything learned must survive a restart, so chains are written to
*memory.json*.

### Tuple keys

The chain's keys are *(word, word)* tuples, but JSON can only store lists.
Saving converts every tuple key to a list, and loading converts them back:

```
in memory (tuple keys):  {("i", "like"): ["strong", "coffee"], ...}
on disk (list keys):     [[["i", "like"], ["strong", "coffee"]], ...]
```

### Per-server state

Everything below is stored **per guild**: `guilds` maps a guild id to its own
`{users, current_user_id, mode, server, heat}`. The file is `version: 2`; an
old flat file is loaded as `legacy` and adopted by the first guild whose name
matches its server persona. `/persona` picks the active persona within a guild.

### The file format

```json
{
  "mode": "user",
  "server": null,
  "current_user_id": "123...",
  "users": {
    "123...": {"name": "Alice", "chain": [...], "media": ["https://..."]},
    "456...": {"name": "Bob",   "chain": [...], "media": []}
  }
}
```

- *users* maps a user's Discord id (as a string) to their learned data.
- *current_user_id* remembers who /speak should talk as.
- *mode* records which personality is active: "user" (a single person) or
  "server" (everyone combined).
- *server* holds the whole-server chain and media, or *null* if /servermimic
  hasn't been run.
- The loader returns an empty state if the file is missing or corrupt, so a
  fresh install or a truncated file never crashes the bot.

### Atomic writes

Because a corrupt file is treated as "start from scratch", a crash *during* a
save could silently wipe everything learned. To prevent this the save writes to
*memory.json.tmp* first and then renames it over *memory.json* with
`os.replace`, which the operating system performs atomically: the real file is
always either the complete old version or the complete new one.

The save runs at the end of every /mimic, /servermimic, and /mode, and the load
runs once at startup (in the bot's *setup_hook*, which — unlike *on_ready* —
does not re-fire on reconnects), so /speak works immediately after a restart.
Slash-command registration (*tree.sync*) lives in the same hook for the same
reason: Discord rate-limits it, so it should run once, not on every reconnect.

---

## 7. The scan (*run_mimic*)

Scanning is the expensive part: reading a channel's full history pages 100
messages per API request, so a busy server means many requests. Several design
choices make this tolerable:

### Concurrency

All selected channels are scanned **at the same time** with a single gather
call. Each channel gets its own coroutine, so the whole scan finishes in
roughly the time of the single slowest channel rather than the sum of all of
them.

### A shared, smoothly-climbing counter

The read counter is a plain integer shared across all scan tasks. Because
asyncio runs in a single thread, incrementing it is safe — no locks needed.
It is bumped once per message so the progress readout climbs smoothly.

### Progress without API spam

A separate background task refreshes the progress message **once per second**,
reading the shared counter. Editing a message is an API call, so doing it every
second (rather than every few messages) keeps Discord happy while still looking
responsive. When the scan finishes, the background task is cancelled and its
cancellation is awaited so no "task was destroyed" warning is printed.

### Error resilience

If the bot can see a channel but lacks "Read Message History", reading its
history raises a permission error; a channel deleted mid-scan raises a
not-found error. Each channel is wrapped in a handler that catches any Discord
API error (`HTTPException`, the parent of both), logs which channel was skipped,
and keeps going, so one bad channel can't abort the whole scan.

### Outliving the interaction token

Discord lets a bot reply to a slash command for only **15 minutes** after it
was used; after that, followup messages fail. A full-history scan of a busy
server can take longer than that. So the progress message and the final
results are sent as *ordinary channel messages* (`channel.send`), which have
no expiry, rather than as interaction followups. The results message also uses
`AllowedMentions.none()` so the mentions in it don't ping the people being
mimicked.

---

## 8. Slash commands and the picker UI

### Command flow

/mimic takes 1–4 member parameters (Discord's app commands don't support
variable-length lists, so the last three are optional and default to none). It
collects the filled-in ones and responds with a **view** (see below). The actual
scan runs only after the user picks channels.

### The channel picker (the view)

A view with two components:

1. **A channel-select dropdown** (up to 25 text channels). The text-channel
   filter hides categories and voice channels. Its callback resolves each
   selection to a real text channel and runs the scan.

2. **A "All the channels" button** that grabs every text channel at once.

A subtle but important detail: the dropdown returns lightweight channel
objects that **do not** have a history method. Each one must be resolved with
the guild's channel lookup to get the full text-channel object before scanning.
That lookup returns nothing for channels the bot doesn't know about (deleted,
or not cached), so those are filtered out — otherwise a single bad pick would
crash the whole concurrent scan.

The view also defines its own error hook (*on_error*). Errors raised inside
component callbacks are **not** delivered to the global slash-command error
handler (§10), so without it a failure mid-scan would only appear in the
terminal while the user stared at a "Snooping..." message that never finished.

### /speak

Generates a sentence from the currently active personality, with a 30% chance
of re-sending a saved media URL instead. In "user" mode that's the most recently
mimicked user; in "server" mode it's the whole-server chain.

### /servermimic

The server-wide sibling of /mimic. It asks the same channel question, but instead
of separating messages by author, it merges *everyone's* (non-bot) messages into
a single combined chain, stores it in the *server* slot, and switches the mode
to "server". Everything afterwards — /speak and spontaneous replies — then
draws from that combined personality.

### /mode

Switches between "user" and "server" personalities without re-scanning. It only
allows "server" if a server has actually been learned, and it saves the choice
so it survives a restart.

### /users

Lists every learned personality, marking the currently active one with an
asterisk. The server (if learned) appears too, labelled "(server)".

### /converse

Checks that both users have been analyzed, then alternates 5 rounds of
generated sentences between them.

### /ask

The only command that talks to an AI model. It resolves the active persona
(same rules as /speak), generates three Markov sentences as *style examples*,
and sends Claude a system prompt of the form "You are *Name*... here is how
they write: ...", followed by the user's question. Claude's answer is posted
as `**Name**: answer`.

Two implementation details:

- The Anthropic call is a blocking HTTP request, so it runs in a worker thread
  (`asyncio.to_thread`) and the bot stays responsive meanwhile.
- The `anthropic` package is imported lazily inside the call, so the bot still
  starts if it isn't installed; only /ask would fail, with a clear message.

Claude is shown up to 40 **real messages** saved at scan time (`samples`, max
200 per persona), not Markov output, with a strict stay-in-character prompt.
@mentioning the bot or replying to it goes through the same path, and the last
8 exchanges per channel are sent as prior turns so follow-ups make sense.

Configuration: `ANTHROPIC_API_KEY` (required) and `CLAUDE_MODEL` (optional,
defaults to `claude-sonnet-4-5`), both read from *secrets.env*.

---

## 9. Spontaneous messages and "heat"

The bot also talks on its own. Every non-bot message in the server raises a
counter called *heat*:

```python
heat += 1
chance = min(heat * 0.02, 0.40)   # 2% per message, capped at 40%
```

If a random roll is under the chance, the bot picks a random learned personality
and says something (30% media, else text), then resets *heat* to 0. In "server"
mode it draws from the whole-server chain instead of a single user; if the
active mode has nothing to draw from, it stays quiet rather than crashing.

The design intent:

- **Quiet chat stays quiet.** With heat at 1, the chance is only 2%.
- **Busy chat eventually gets a reply.** After 20 messages the chance is capped
  at 40%, so the bot reliably interrupts after a burst of activity.
- **No self-triggering.** The bot's own messages are ignored (and it never runs
  in DMs or servers where nothing has been learned yet).

---

## 10. Error handling

A global error hook catches any exception thrown by a slash command and does
two things: prints the real error to the console (for debugging) and replies to
the user with a friendly message ("Something went wrong. Blame the robot, not me.").
Without this, Discord silently shows "application did not respond" and the
error is invisible.

The reply has to be sent the right way: if the command hasn't answered yet, the
handler must use the initial response; if it already answered (or deferred), it
must use a followup. The hook checks `response.is_done()` and picks accordingly,
so the user actually sees the message in both cases.

View callbacks (the channel picker) have a separate hook of their own — see §8.

---

## 11. Tuning parameters

| Setting | What it controls | Default |
| --- | --- | --- |
| sentence word limit (*generate_sentence*) | target sentence length | 30 |
| *random.random() < 0.4* | entropy jump probability | 0.4 |
| *random.random() < 0.3* | media re-send probability | 0.3 |
| heat × 0.02, capped at 0.40 | spontaneous-reply odds | 2% per message, max 40% |
| *MEMORY_FILE* | where learned data is saved | memory.json |
| channel-picker timeout | how long the picker stays open | 300 s (5 min) |
| *CLAUDE_MODEL* (env) | which Claude model /ask calls | claude-sonnet-4-5 |
| *max_tokens* in *claude_reply* | length cap on /ask answers | 300 |
