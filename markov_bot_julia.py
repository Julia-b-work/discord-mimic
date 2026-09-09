from dotenv import load_dotenv  # reads secrets.env into environment variables
import os  # getenv
import json  # save/load memory.json
import asyncio  # concurrent channel scanning + progress ticker
import random  # all the "personality" randomness
from typing import Literal  # restricts the /mode argument to fixed choices
import discord  # the core library
from discord.ext import commands  # commands.Bot and command decorators

# Load DISCORD_TOKEN from secrets.env (kept out of git).
load_dotenv("secrets.env")

token = os.getenv("DISCORD_TOKEN")

# Fail loudly and immediately if the token is missing.
if token is None:
    print("ERROR: DISCORD_TOKEN is not set. Create a secrets.env file with the token.")
    raise SystemExit(1)

# Intents declare which events we want to receive. message_content is the one
# that lets us actually read message text (needed to learn how people talk).
intents = discord.Intents.default()
intents.message_content = True

# command_prefix is required by commands.Bot even though we only use slash
# commands; "!" is a harmless placeholder.
client = commands.Bot(command_prefix="!", intents=intents)

# Learned state, reloaded from disk in setup_hook.
users = {}
current_user_id = None

# "user" -> speak as the current user; "server" -> speak as the whole server.
mode = "user"

# The whole-server chain + media, built by /servermimic. None until learned.
server = None

# Spontaneous-reply pressure; resets to 0 after the bot speaks.
heat = 0

MEMORY_FILE = "memory.json"

# Claude integration for /ask: which model to call, overridable via secrets.env.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")


# Write the whole in-memory state (users, mode, server) to memory.json.
def save_users():
    data = {
        "mode": mode,
        # Chain keys are (word, word) tuples; JSON only stores lists, so each
        # tuple key is converted to a list here.
        "server": (
            None
            if server is None
            else {
                "name": server["name"],
                "chain": [
                    [list(key), words] for key, words in server["chain"].items()
                ],
                "media": server.get("media", []),
            }
        ),
        "current_user_id": current_user_id,
        "users": {
            uid: {
                "name": info["name"],
                # Same tuple -> list conversion, one entry per learned user.
                "chain": [[list(key), words] for key, words in info["chain"].items()],
                "media": info.get("media", []),
            }
            for uid, info in users.items()
        },
    }
    # Write to a temp file first, then atomically swap it in. A crash mid-write
    # therefore can't leave a truncated memory.json that wipes all learned state.
    tmp_file = MEMORY_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp_file, MEMORY_FILE)


# Read memory.json back. Returns (users, current_user_id, mode, server).
# A missing or corrupt file yields an empty default state instead of crashing.
def load_users():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None, "user", None

    loaded = {}
    for uid, info in data.get("users", {}).items():
        loaded[uid] = {
            "name": info["name"],
            # Reverse of save_users(): list keys back into tuples.
            "chain": {tuple(key): words for key, words in info["chain"]},
            "media": info.get("media", []),
        }

    server_data = data.get("server")
    loaded_server = None
    if server_data is not None:
        loaded_server = {
            "name": server_data.get("name", "the server"),
            "chain": {
                tuple(key): words for key, words in server_data.get("chain", [])
            },
            "media": server_data.get("media", []),
        }

    return loaded, data.get("current_user_id"), data.get("mode", "user"), loaded_server


# Runs exactly once, after login but before the gateway connects. Unlike
# on_ready (which re-fires on every reconnect), this is the right place for
# one-time work like loading memory and syncing slash commands (rate-limited).
async def setup_hook():
    global users, current_user_id, mode, server
    # Restore everything learned in previous sessions from memory.json.
    users, current_user_id, mode, server = load_users()
    # Push the slash-command definitions to Discord so they appear in the UI.
    await client.tree.sync()


client.setup_hook = setup_hook


# Fires when the bot finishes connecting (and again after any reconnect).
@client.event
async def on_ready():
    print(f"logged in as {client.user}")


@client.event
async def on_message(message):
    global heat

    # Ignore bots (prevents self-echo) and direct messages (no guild).
    if message.author.bot or message.guild is None:
        return

    # Nothing learned at all -> nothing to say.
    if not users and server is None:
        return

    # Every real message raises the pressure...
    heat += 1

    # ...turning into a reply chance that caps out at 25%.
    chance = min(heat * 0.01, 0.25)

    if random.random() < chance:
        # We're speaking now, so release the pressure.
        heat = 0

        # In server mode, speak as the combined server personality.
        if mode == "server" and server is not None:
            media = server.get("media", [])
            if media and random.random() < 0.3:
                await message.channel.send(random.choice(media))
                return
            await message.channel.send(generate_sentence(server["chain"]))
            return

        # Guard: no users learned (e.g. after /servermimic then /mode user).
        if not users:
            return

        # Pick a random learned user to impersonate.
        uid = random.choice(list(users.keys()))

        # 30% chance to re-send one of their saved images/GIFs instead of text.
        media = users[uid].get("media", [])
        if media and random.random() < 0.3:
            await message.channel.send(random.choice(media))
            return

        sentence = generate_sentence(users[uid]["chain"])
        await message.channel.send(sentence)


# Map each (word, word) pair to a list of words that follow it.
# A message of n words produces n-2 such entries; shorter messages contribute
# nothing, since they reveal no word ordering.
def build_markov_chain(messages):
    chain = {}

    for message in messages:
        words = message.split()

        # Slide a 3-word window: record that words[i+2] can follow
        # (words[i], words[i+1]).
        for i in range(len(words) - 2):
            key = (words[i], words[i + 1])
            next_word = words[i + 2]
            chain.setdefault(key, []).append(next_word)

    return chain


# Walk the chain from a random start pair until a dead end or max_words.
# Picks from unique followers (so rare words aren't drowned out) and, 40% of
# the time, jumps to a random vocabulary word to keep output from parroting
# the user's exact phrases.
def generate_sentence(chain, max_words=30):
    if not chain:
        return ""

    key = random.choice(list(chain.keys()))
    words = [key[0], key[1]]

    # Every distinct word in the chain, used for the random-jump entropy trick.
    vocabulary = {word for pair, followers in chain.items() for word in (pair[0], pair[1], *followers)}

    while len(words) < max_words:
        next_words = chain.get(key)

        # Dead end: the current pair was never followed by anything, so stop.
        if not next_words:
            break

        # Pick from unique followers so common words don't dominate.
        next_word = random.choice(list(set(next_words)))

        # 40% of the time, jump to a random vocabulary word for extra entropy.
        if random.random() < 0.4:
            next_word = random.choice(list(vocabulary))

        words.append(next_word)
        # Slide the window to (previous-second-word, new-word).
        key = (key[1], next_word)

    return " ".join(words)


_GIF_HOSTS = ("tenor.com", "giphy.com", "media.tenor.com")


# Collect GIF links from a message. Direct uploads are deliberately skipped:
# Discord CDN attachment URLs are signed and expire after ~24h, so saving them
# to memory.json would produce dead links later. Tenor/Giphy links are stable.
def extract_media(message):
    urls = []

    for word in message.content.split():
        if word.startswith("http") and any(host in word for host in _GIF_HOSTS):
            urls.append(word)

    return urls


# Scan channels, build one chain per target user, store + save, then reply.
async def run_mimic(interaction, targets, channels):
    # Post an editable "working..." message; updated live during the scan.
    # Sent as a normal channel message (not an interaction followup) because
    # interaction tokens expire after 15 minutes and a full history scan of a
    # big server can easily take longer than that.
    progress = await interaction.channel.send("Snooping through messages...")

    target_ids = {user.id for user in targets}
    messages_by_user = {user.id: [] for user in targets}
    media_by_user = {user.id: [] for user in targets}
    read_count = 0

    async def scan_channel(channel):
        nonlocal read_count
        channel_messages = {uid: [] for uid in target_ids}
        channel_media = {uid: [] for uid in target_ids}
        try:
            # limit=None reads the channel's entire history, oldest to newest.
            async for message in channel.history(limit=None):
                read_count += 1
                # Only collect messages from the target users.
                if message.author.id in target_ids:
                    if message.content.strip():
                        channel_messages[message.author.id].append(message.content)
                    # Media is collected even from text-less (image-only) posts.
                    channel_media[message.author.id].extend(extract_media(message))
        except discord.HTTPException as exc:
            # Missing "Read Message History" permission (Forbidden), a deleted
            # channel (NotFound), etc.: skip this channel, keep the others going.
            print(f"skipping #{channel.name}: {exc!r}")
        return channel_messages, channel_media

    # Refreshes the progress message once per second (not per message) to avoid
    # hammering the API, while still looking responsive.
    async def progress_loop():
        while True:
            await asyncio.sleep(1)
            await progress.edit(
                content=f"Snooping through messages... {read_count} read"
            )

    progress_task = asyncio.create_task(progress_loop())

    try:
        # Scan every channel concurrently, so total time ~ slowest channel.
        results = await asyncio.gather(*(scan_channel(ch) for ch in channels))
    finally:
        # Stop the ticker and swallow its cancellation to avoid a warning.
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

    # Merge each channel's per-user results into the master collections.
    for channel_messages, channel_media in results:
        for uid in target_ids:
            messages_by_user[uid].extend(channel_messages[uid])
            media_by_user[uid].extend(channel_media[uid])

    total_messages = sum(len(m) for m in messages_by_user.values())
    await progress.edit(
        content=(
            f"Snooping complete! {total_messages} messages.\n"
            f"Messages read: {read_count}"
        )
    )

    global users, current_user_id
    results = []

    # Learn + store each target, and collect a sample line for the reply.
    for user in targets:
        messages = messages_by_user[user.id]

        # No messages found -> nothing to learn from this person.
        if not messages:
            results.append(
                f"{user.mention}: not enough messages to imitate. Were they even online?"
            )
            continue

        chain = build_markov_chain(messages)

        # All their messages were too short to form any word triples.
        if not chain:
            results.append(
                f"{user.mention}: couldn't learn their style "
                "(messages too short)."
            )
            continue

        # Store under the user's id (as a string, since JSON keys must be strings).
        users[str(user.id)] = {
            "name": user.display_name,
            "chain": chain,
            "media": media_by_user[user.id],
        }
        current_user_id = str(user.id)

        # Generate a sample sentence as proof of learning.
        sentence = generate_sentence(chain)
        results.append(f"**{user.display_name}**: {sentence}")

    save_users()

    # channel.send (not followup) for the same 15-minute-token reason as above;
    # AllowedMentions.none() so the mentions in the text don't ping anyone.
    await interaction.channel.send(
        "\n".join(results), allowed_mentions=discord.AllowedMentions.none()
    )


# Scan channels, merge EVERYONE's messages into one chain, then switch mode.
# Same overall shape as run_mimic, but there's a single combined personality.
async def run_server_mimic(interaction, channels):
    # channel.send rather than followup: see run_mimic for why (15-minute tokens).
    progress = await interaction.channel.send("Snooping through messages...")

    all_messages = []
    all_media = []
    read_count = 0

    async def scan_channel(channel):
        nonlocal read_count
        channel_messages = []
        channel_media = []
        try:
            async for message in channel.history(limit=None):
                read_count += 1
                # Skip bot messages so the mimicry doesn't learn from other bots.
                if message.author.bot:
                    continue
                # Every human message goes into the shared pile, regardless of author.
                if message.content.strip():
                    channel_messages.append(message.content)
                channel_media.extend(extract_media(message))
        except discord.HTTPException as exc:
            print(f"skipping #{channel.name}: {exc!r}")
        return channel_messages, channel_media

    async def progress_loop():
        while True:
            await asyncio.sleep(1)
            await progress.edit(
                content=f"Snooping through messages... {read_count} read"
            )

    progress_task = asyncio.create_task(progress_loop())

    try:
        results = await asyncio.gather(*(scan_channel(ch) for ch in channels))
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

    for channel_messages, channel_media in results:
        all_messages.extend(channel_messages)
        all_media.extend(channel_media)

    total_messages = len(all_messages)
    await progress.edit(
        content=(
            f"Snooping complete! {total_messages} messages.\n"
            f"Messages read: {read_count}"
        )
    )

    global mode, server

    # Nothing but bots (or nothing at all) -> can't learn the server.
    if not all_messages:
        await interaction.channel.send(
            "Couldn't read anyone's messages. Is this server a ghost town?"
        )
        return

    chain = build_markov_chain(all_messages)

    # All messages too short to form any triples.
    if not chain:
        await interaction.channel.send(
            "Couldn't learn this server's style (messages too short)."
        )
        return

    # Store the combined personality and switch into server mode.
    server = {
        "name": interaction.guild.name,
        "chain": chain,
        "media": all_media,
    }
    mode = "server"
    save_users()

    sentence = generate_sentence(chain)
    await interaction.channel.send(f"**{interaction.guild.name}**: {sentence}")


# Multi-select channel dropdown + an "all channels" button.
class ChannelPicker(discord.ui.View):
    def __init__(self, users, server_mode=False):
        super().__init__(timeout=300)  # disable the view after 5 minutes
        self.users = users
        # server_mode=True routes callbacks to run_server_mimic instead of run_mimic.
        self.server_mode = server_mode

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=25,
        placeholder="Pick which channels to read",
    )
    async def on_select(self, interaction, select):
        # Defer so the (possibly long) scan doesn't hit Discord's timeout.
        await interaction.response.defer()

        # select.values holds lightweight channel objects with no .history();
        # resolve each to a full TextChannel via the guild lookup. get_channel
        # returns None for uncached/deleted channels, so drop those rather than
        # letting one bad pick crash the whole scan.
        channels = [
            interaction.guild.get_channel(channel.id) for channel in select.values
        ]
        channels = [ch for ch in channels if ch is not None]
        if not channels:
            await interaction.followup.send("I couldn't find any of those channels.")
            return
        if self.server_mode:
            await run_server_mimic(interaction, channels)
        else:
            await run_mimic(interaction, self.users, channels)

    @discord.ui.button(
        label="All the channels", style=discord.ButtonStyle.primary, row=1
    )
    async def all_channels(self, interaction, button):
        await interaction.response.defer()
        channels = list(interaction.guild.text_channels)
        if self.server_mode:
            await run_server_mimic(interaction, channels)
        else:
            await run_mimic(interaction, self.users, channels)

    # Exceptions inside view callbacks are NOT routed to client.tree.error, so
    # without this they'd only print a traceback and leave the user with a
    # "Snooping..." message that never finishes.
    async def on_error(self, interaction, error, item):
        print(f"ERROR in ChannelPicker ({item}): {error!r}")
        try:
            await interaction.channel.send(
                "Something went wrong while snooping. Blame the robot, not me."
            )
        except Exception:
            pass


# /mimic @user [@user2 @user3 @user4] — learn up to four people at once.
@client.tree.command(name="mimic", description="I'm about to become someone!!!")
async def mimic(
    interaction: discord.Interaction,
    user: discord.Member,
    user2: discord.Member = None,
    user3: discord.Member = None,
    user4: discord.Member = None,
):
    # Slash commands only make sense inside a server (we need guild channels).
    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.")
        return

    # Drop the optional slots the user left empty.
    targets = [u for u in (user, user2, user3, user4) if u is not None]

    # Ask which channels to scan; the ChannelPicker view does the actual work.
    names = ", ".join(u.mention for u in targets)
    await interaction.response.send_message(
        f"Which channels should I read to imitate {names}?",
        view=ChannelPicker(targets),
    )


# /servermimic — learn everyone at once and become the whole server.
@client.tree.command(name="servermimic", description="Become the whole server")
async def servermimic(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.")
        return

    # server_mode=True makes the picker route to run_server_mimic.
    await interaction.response.send_message(
        "Which channels should I read to imitate this server?",
        view=ChannelPicker([], server_mode=True),
    )


# /mode user|server — choose who /speak and spontaneous replies talk as.
@client.tree.command(name="mode", description="Switch who I talk as")
async def set_mode(
    interaction: discord.Interaction,
    new_mode: Literal["user", "server"],
):
    global mode

    # Can't switch to server mode if /servermimic has never been run.
    if new_mode == "server" and server is None:
        await interaction.response.send_message(
            "I haven't learned this server yet. Use /servermimic first!"
        )
        return

    mode = new_mode
    save_users()  # persist the choice so it survives a restart

    label = "the whole server" if mode == "server" else "the imitated user"
    await interaction.response.send_message(f"Switched! Now I talk as {label}.")


# /speak — produce one sentence on demand, obeying the current mode.
@client.tree.command(name="speak", description="Speak as the imitated user")
async def speak(interaction: discord.Interaction):
    # Server mode: talk as the combined server personality.
    if mode == "server":
        if server is None:
            await interaction.response.send_message(
                "I haven't learned this server yet. Use /servermimic first!"
            )
            return

        # 30% chance to reply with a saved image/GIF instead of text.
        media = server.get("media", [])
        if media and random.random() < 0.3:
            await interaction.response.send_message(random.choice(media))
            return

        await interaction.response.send_message(generate_sentence(server["chain"]))
        return

    # User mode: talk as the most recently /mimic'd user.
    if current_user_id is None or current_user_id not in users:
        await interaction.response.send_message(
            "I'm not pretending to be anyone yet. Use /mimic first!"
        )
        return

    sentence = generate_sentence(users[current_user_id]["chain"])

    # Same 30% media chance as above, drawn from the user's own posts.
    media = users[current_user_id].get("media", [])
    if media and random.random() < 0.3:
        await interaction.response.send_message(random.choice(media))
        return

    await interaction.response.send_message(sentence)


# /users — list everyone (and the server) the bot has learned, with a marker
# on whoever is currently active.
@client.tree.command(name="users", description="Who have I been spying on?")
async def list_users(interaction: discord.Interaction):
    if not users and server is None:
        await interaction.response.send_message(
            "I don't know anyone yet. Go run /mimic on somebody!"
        )
        return

    lines = []
    for uid, info in users.items():
        # The " *" suffix marks the user who is active in "user" mode.
        marker = " *" if (uid == current_user_id and mode == "user") else ""
        lines.append(f"**{info['name']}**{marker}")

    # The server personality is listed separately, marked when in server mode.
    if server is not None:
        server_marker = " *" if mode == "server" else ""
        lines.append(f"**{server['name']}** (server){server_marker}")

    await interaction.response.send_message("\n".join(lines))


# /converse @user1 @user2 — print five alternating lines between two people.
@client.tree.command(
    name="converse", description="Simulate a conversation between two users"
)
async def converse(
    interaction: discord.Interaction,
    user1: discord.Member,
    user2: discord.Member,
):
    # Collect whichever participants haven't been /mimic'd yet.
    missing = []
    if str(user1.id) not in users:
        missing.append(user1.mention)
    if str(user2.id) not in users:
        missing.append(user2.mention)

    if missing:
        await interaction.response.send_message(
            "I haven't analyzed: " + ", ".join(missing) + ". Use /mimic first!"
        )
        return

    chain1 = users[str(user1.id)]["chain"]
    chain2 = users[str(user2.id)]["chain"]
    name1 = users[str(user1.id)]["name"]
    name2 = users[str(user2.id)]["name"]

    # Five back-and-forth rounds, each line freshly generated.
    lines = []
    for _ in range(5):
        lines.append(f"**{name1}**: {generate_sentence(chain1)}")
        lines.append(f"**{name2}**: {generate_sentence(chain2)}")

    await interaction.response.send_message("\n".join(lines))


# /ask <question> — have Claude answer in the active persona's voice.
# Persona style comes from a few Markov samples, so Claude imitates the vibe.
@client.tree.command(name="ask", description="Ask the active persona anything (Claude)")
async def ask(interaction: discord.Interaction, question: str):
    # Resolve the active persona (same logic as /speak).
    if mode == "server" and server is not None:
        name, chain = server["name"], server["chain"]
    elif mode == "user" and current_user_id in users:
        name, chain = users[current_user_id]["name"], users[current_user_id]["chain"]
    else:
        await interaction.response.send_message(
            "I'm not pretending to be anyone yet. Use /mimic or /servermimic first!"
        )
        return

    # The Claude call takes a few seconds; defer so Discord doesn't time out.
    await interaction.response.defer()

    # Sample the Markov chain a few times to show Claude how this person writes.
    samples = "\n".join(
        f"- {generate_sentence(chain)}" for _ in range(3)
    )
    system_prompt = (
        f"You are {name}, a member of this Discord server. "
        f"Answer in first person, briefly, matching {name}'s tone and vocabulary. "
        f"Sample messages {name} has written, to copy the style:\n{samples}"
    )

    # Blocking HTTP call, so run it in a worker thread to keep the bot responsive.
    try:
        answer = await asyncio.to_thread(claude_reply, system_prompt, question)
    except Exception as exc:
        answer = f"Claude call failed: {exc}"

    await interaction.followup.send(f"**{name}**: {answer}")


# Synchronous Claude call (runs inside asyncio.to_thread). Kept lazy-imported so
# the bot still starts even if the `anthropic` package isn't installed.
def claude_reply(system_prompt: str, question: str) -> str:
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "ANTHROPIC_API_KEY is not set. Add it to secrets.env to use /ask."

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": question}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


# Global safety net for any slash-command error that isn't handled locally.
@client.tree.error
async def on_tree_error(interaction: discord.Interaction, error):
    print(f"ERROR in /{getattr(interaction.command, 'name', 'unknown')}: {error!r}")
    text = "Something went wrong. Blame the robot, not me."
    try:
        # followup only works after an initial response (or defer); otherwise
        # the interaction must be answered via response.send_message.
        if interaction.response.is_done():
            await interaction.followup.send(text)
        else:
            await interaction.response.send_message(text)
    except Exception:
        pass  # interaction token may have expired; nothing left to do


# Bumped on each release; see CHANGELOG.md for what changed.
__version__ = "0.3.0"


# Only connect to Discord when run directly (not when imported by tests).
if __name__ == "__main__":
    print(f"markov_bot_julia v{__version__}")
    client.run(token)
