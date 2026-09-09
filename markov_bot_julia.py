from dotenv import load_dotenv  # reads secrets.env into environment variables
import os  # getenv
import json  # save/load memory.json
import asyncio  # concurrent channel scanning + progress ticker
import random  # all the "personality" randomness
from collections import deque  # bounded per-channel conversation history
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

# Learned state, keyed by guild id (as a string) so each server the bot is in
# has its own personas, mode and heat. Reloaded from disk in setup_hook.
# Each value looks like:
#   {"users": {uid: {...}}, "current_user_id": str|None,
#    "mode": "user"|"server", "server": dict|None, "heat": int}
guilds = {}

# Data saved by versions before per-guild state existed. It has no guild id, so
# it's adopted by the first guild whose name matches its server persona.
legacy_state = None


# Return (creating if needed) the state dict for a guild.
def state(guild):
    global legacy_state
    gid = str(guild.id)
    if gid not in guilds:
        if legacy_state is not None and (
            legacy_state["server"] is None
            or legacy_state["server"]["name"] == guild.name
        ):
            guilds[gid] = legacy_state
            legacy_state = None
        else:
            guilds[gid] = {
                "users": {},
                "current_user_id": None,
                "mode": "user",
                "server": None,
                "heat": 0,
            }
    return guilds[gid]


MEMORY_FILE = "memory.json"

# How many raw messages to keep per persona as style examples for /ask.
MAX_SAMPLES = 200

# Short-term memory for /ask and @mention replies: the last few exchanges per
# channel, as Claude message dicts. In-memory only; forgotten on restart.
HISTORY_TURNS = 8
histories = {}  # channel id -> deque of {"role", "content"}

# Claude integration for /ask: which model to call, overridable via secrets.env.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")


# Chain keys are (word, word) tuples; JSON only stores lists, so each tuple
# key is converted to a list here (and back in _chain_from_json).
def _chain_to_json(chain):
    return [[list(key), words] for key, words in chain.items()]


def _chain_from_json(data):
    return {tuple(key): words for key, words in data}


def _persona_to_json(info):
    return {
        "name": info["name"],
        "chain": _chain_to_json(info["chain"]),
        "media": info.get("media", []),
        "samples": info.get("samples", []),
    }


def _persona_from_json(info, default_name="the server"):
    return {
        "name": info.get("name", default_name),
        "chain": _chain_from_json(info.get("chain", [])),
        "media": info.get("media", []),
        # Raw example messages for /ask; older saves won't have them.
        "samples": info.get("samples", []),
    }


def _state_to_json(g):
    return {
        "mode": g["mode"],
        "server": None if g["server"] is None else _persona_to_json(g["server"]),
        "current_user_id": g["current_user_id"],
        "users": {uid: _persona_to_json(info) for uid, info in g["users"].items()},
    }


def _state_from_json(data):
    server_data = data.get("server")
    return {
        "users": {
            uid: _persona_from_json(info) for uid, info in data.get("users", {}).items()
        },
        "current_user_id": data.get("current_user_id"),
        "mode": data.get("mode", "user"),
        "server": None if server_data is None else _persona_from_json(server_data),
        "heat": 0,  # never persisted; always starts cold
    }


# Write every guild's state to memory.json.
def save_users():
    data = {
        "version": 2,
        "guilds": {gid: _state_to_json(g) for gid, g in guilds.items()},
    }
    if legacy_state is not None:
        data["legacy"] = _state_to_json(legacy_state)
    # Write to a temp file first, then atomically swap it in. A crash mid-write
    # therefore can't leave a truncated memory.json that wipes all learned state.
    tmp_file = MEMORY_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp_file, MEMORY_FILE)


# Read memory.json back. Returns (guilds, legacy_state).
# A missing or corrupt file yields an empty default state instead of crashing.
def load_users():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None

    # Version 1 files were a single flat state with no guild id.
    if "guilds" not in data:
        return {}, _state_from_json(data)

    loaded = {gid: _state_from_json(g) for gid, g in data["guilds"].items()}
    legacy = _state_from_json(data["legacy"]) if data.get("legacy") else None
    return loaded, legacy


# Runs exactly once, after login but before the gateway connects. Unlike
# on_ready (which re-fires on every reconnect), this is the right place for
# one-time work like loading memory and syncing slash commands (rate-limited).
async def setup_hook():
    global guilds, legacy_state
    # Restore everything learned in previous sessions from memory.json.
    guilds, legacy_state = load_users()
    # All state is per-guild, so none of the commands make sense in DMs.
    # Discord hides guild-only commands there entirely.
    for cmd in client.tree.get_commands():
        cmd.guild_only = True
    # Push the slash-command definitions to Discord so they appear in the UI.
    await client.tree.sync()


client.setup_hook = setup_hook


# Fires when the bot finishes connecting (and again after any reconnect).
@client.event
async def on_ready():
    print(f"logged in as {client.user}")


@client.event
async def on_message(message):
    g = state(message.guild)

    # Ignore bots (prevents self-echo) and direct messages (no guild).
    if message.author.bot or message.guild is None:
        return

    # Nothing learned at all -> nothing to say.
    if not g["users"] and g["server"] is None:
        return

    # @mentioning the bot, or using Discord's reply feature on one of its
    # messages, asks the active persona directly (via Claude).
    ref = message.reference.resolved if message.reference else None
    replied_to_bot = isinstance(ref, discord.Message) and ref.author == client.user
    if client.user in message.mentions or replied_to_bot:
        persona = active_persona(message.guild)
        if persona is None:
            return
        # Strip the mention itself so Claude only sees the actual text.
        question = message.content.replace(client.user.mention, "").strip()
        if not question:
            question = "oi"
        async with message.channel.typing():
            answer = await persona_answer(
                persona, question, message.channel.id, message.author.display_name
            )
        reply = f"**{persona['name']}**: {answer}"
        await message.reply(
            reply[:2000], allowed_mentions=discord.AllowedMentions.none()
        )
        g["heat"] = 0
        return

    # Every real message raises the pressure...
    g["heat"] += 1

    # ...turning into a reply chance that caps out at 40%.
    chance = min(g["heat"] * 0.02, 0.40)

    if random.random() < chance:
        # We're speaking now, so release the pressure.
        g["heat"] = 0

        # In server mode, speak as the combined server personality.
        if g["mode"] == "server" and g["server"] is not None:
            media = g["server"].get("media", [])
            if media and random.random() < 0.3:
                await message.channel.send(random.choice(media))
                return
            await message.channel.send(generate_sentence(g["server"]["chain"]))
            return

        # Guard: no users learned (e.g. after /servermimic then /mode user).
        if not g["users"]:
            return

        # Pick a random learned user to impersonate.
        uid = random.choice(list(g["users"].keys()))

        # 30% chance to re-send one of their saved images/GIFs instead of text.
        media = g["users"][uid].get("media", [])
        if media and random.random() < 0.3:
            await message.channel.send(random.choice(media))
            return

        sentence = generate_sentence(g["users"][uid]["chain"])
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


# Keep a random subset of real messages (skipping one-word replies and bare
# links) to show Claude how this persona actually writes.
def pick_samples(messages, limit=MAX_SAMPLES):
    good = [
        m for m in messages
        if len(m.split()) >= 3 and not m.startswith("http")
    ]
    return random.sample(good, min(limit, len(good)))


# Scan channels, build one chain per target user, store + save, then reply.
async def run_mimic(interaction, targets, channels):
    # Post an editable "working..." message; updated live during the scan.
    # Sent as a normal channel message (not an interaction followup) because
    # interaction tokens expire after 15 minutes and a full history scan of a
    # big server can easily take longer than that.
    g = state(interaction.guild)
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
        g["users"][str(user.id)] = {
            "name": user.display_name,
            "chain": chain,
            "media": media_by_user[user.id],
            # A random subset of their real messages, so /ask can show Claude
            # genuine writing rather than Markov output.
            "samples": pick_samples(messages),
        }
        g["current_user_id"] = str(user.id)

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
    g = state(interaction.guild)
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
    g["server"] = {
        "name": interaction.guild.name,
        "chain": chain,
        "media": all_media,
        "samples": pick_samples(all_messages),
    }
    g["mode"] = "server"
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
    g = state(interaction.guild)

    # Can't switch to server mode if /servermimic has never been run.
    if new_mode == "server" and g["server"] is None:
        await interaction.response.send_message(
            "I haven't learned this server yet. Use /servermimic first!"
        )
        return

    g["mode"] = new_mode
    save_users()  # persist the choice so it survives a restart

    label = "the whole server" if g["mode"] == "server" else "the imitated user"
    await interaction.response.send_message(f"Switched! Now I talk as {label}.")


# /persona — pick exactly who to talk as (any learned user, or the server).
# Uses a dropdown, so it also fixes the gap that /mode only toggles user/server
# and can't choose *which* user.
@client.tree.command(name="persona", description="Choose who I talk as")
async def persona_cmd(interaction: discord.Interaction):
    g = state(interaction.guild)
    if not g["users"] and g["server"] is None:
        await interaction.response.send_message(
            "I don't know anyone yet. Go run /mimic on somebody!"
        )
        return

    options = []
    for uid, info in g["users"].items():
        options.append(
            discord.SelectOption(
                label=info["name"][:100],
                value=uid,
                default=(g["mode"] == "user" and uid == g["current_user_id"]),
            )
        )
    if g["server"] is not None:
        options.append(
            discord.SelectOption(
                label=f"{g["server"]['name'][:90]} (server)",
                value="__server__",
                default=(g["mode"] == "server"),
            )
        )

    # Discord caps a dropdown at 25 options.
    options = options[:25]

    view = discord.ui.View(timeout=120)
    select = discord.ui.Select(placeholder="Pick a persona", options=options)

    async def on_pick(pick_interaction):
        choice = select.values[0]
        if choice == "__server__":
            g["mode"] = "server"
            label = f"**{g["server"]['name']}** (the whole server)"
        else:
            g["mode"] = "user"
            g["current_user_id"] = choice
            label = f"**{g["users"][choice]['name']}**"
        save_users()
        await pick_interaction.response.edit_message(
            content=f"Switched! Now I talk as {label}.", view=None
        )

    select.callback = on_pick
    view.add_item(select)
    await interaction.response.send_message("Who should I be?", view=view)


# /speak — produce one sentence on demand, obeying the current mode.
@client.tree.command(name="speak", description="Speak as the imitated user")
async def speak(interaction: discord.Interaction):
    # Server mode: talk as the combined server personality.
    g = state(interaction.guild)
    if g["mode"] == "server":
        if g["server"] is None:
            await interaction.response.send_message(
                "I haven't learned this server yet. Use /servermimic first!"
            )
            return

        # 30% chance to reply with a saved image/GIF instead of text.
        media = g["server"].get("media", [])
        if media and random.random() < 0.3:
            await interaction.response.send_message(random.choice(media))
            return

        await interaction.response.send_message(generate_sentence(g["server"]["chain"]))
        return

    # User mode: talk as the most recently /mimic'd user.
    if g["current_user_id"] is None or g["current_user_id"] not in g["users"]:
        await interaction.response.send_message(
            "I'm not pretending to be anyone yet. Use /mimic first!"
        )
        return

    sentence = generate_sentence(g["users"][g["current_user_id"]]["chain"])

    # Same 30% media chance as above, drawn from the user's own posts.
    media = g["users"][g["current_user_id"]].get("media", [])
    if media and random.random() < 0.3:
        await interaction.response.send_message(random.choice(media))
        return

    await interaction.response.send_message(sentence)


# /users — list everyone (and the server) the bot has learned, with a marker
# on whoever is currently active.
@client.tree.command(name="users", description="Who have I been spying on?")
async def list_users(interaction: discord.Interaction):
    g = state(interaction.guild)
    if not g["users"] and g["server"] is None:
        await interaction.response.send_message(
            "I don't know anyone yet. Go run /mimic on somebody!"
        )
        return

    lines = []
    for uid, info in g["users"].items():
        # The " *" suffix marks the user who is active in "user" mode.
        marker = " *" if (uid == g["current_user_id"] and g["mode"] == "user") else ""
        lines.append(f"**{info['name']}**{marker}")

    # The server personality is listed separately, marked when in server mode.
    if g["server"] is not None:
        server_marker = " *" if g["mode"] == "server" else ""
        lines.append(f"**{g["server"]['name']}** (server){server_marker}")

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
    g = state(interaction.guild)
    missing = []
    if str(user1.id) not in g["users"]:
        missing.append(user1.mention)
    if str(user2.id) not in g["users"]:
        missing.append(user2.mention)

    if missing:
        await interaction.response.send_message(
            "I haven't analyzed: " + ", ".join(missing) + ". Use /mimic first!"
        )
        return

    chain1 = g["users"][str(user1.id)]["chain"]
    chain2 = g["users"][str(user2.id)]["chain"]
    name1 = g["users"][str(user1.id)]["name"]
    name2 = g["users"][str(user2.id)]["name"]

    # Five back-and-forth rounds, each line freshly generated.
    lines = []
    for _ in range(5):
        lines.append(f"**{name1}**: {generate_sentence(chain1)}")
        lines.append(f"**{name2}**: {generate_sentence(chain2)}")

    await interaction.response.send_message("\n".join(lines))


# /what — explain every command. Uses an embed so it reads cleanly in Discord.
@client.tree.command(name="what", description="What can this bot do?")
async def what(interaction: discord.Interaction):
    embed = discord.Embed(
        title="o_mimico — what I do",
        description=(
            "I read people's messages, learn how they write, and then talk like "
            "them. Learning is per server: what I learn here stays here."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Learning",
        value=(
            "`/mimic @user [@u2 @u3 @u4]` — pick channels, learn up to 4 people.\n"
            "`/servermimic` — learn from everyone at once and become the server."
        ),
        inline=False,
    )
    embed.add_field(
        name="Choosing who I am",
        value=(
            "`/persona` — dropdown of everyone I've learned (and the server).\n"
            "`/mode user|server` — quick toggle between a person and the server.\n"
            "`/users` — list everyone I've learned; `*` marks who I am right now."
        ),
        inline=False,
    )
    embed.add_field(
        name="Talking",
        value=(
            "`/speak` — one sentence in the current persona's style.\n"
            "`/converse @a @b` — a fake 5-round conversation between two people.\n"
            "`/ask <question>` — ask the persona anything; answered by Claude in "
            "their voice.\n"
            "**@mention me** or **reply to my messages** — same as `/ask`, and I "
            "remember the last few exchanges in the channel."
        ),
        inline=False,
    )
    embed.add_field(
        name="On my own",
        value=(
            "The busier the chat, the more likely I butt in with a sentence or a "
            "GIF as the current persona (up to a 40% chance per message)."
        ),
        inline=False,
    )
    embed.set_footer(text=f"v{__version__} · /what shows this again")
    await interaction.response.send_message(embed=embed)


# Return the active persona dict (server or current user), or None.
def active_persona(guild):
    g = state(guild)
    if g["mode"] == "server" and g["server"] is not None:
        return g["server"]
    if g["mode"] == "user" and g["current_user_id"] in g["users"]:
        return g["users"][g["current_user_id"]]
    return None


# Ask Claude to answer `question` as `persona`. Returns the answer text.
# Shared by /ask and by @mention replies in on_message.
async def persona_answer(persona, question, channel_id, asker=None):
    name = persona["name"]

    # Prefer real messages saved at scan time; fall back to Markov output for
    # personas learned before samples were stored.
    raw = persona.get("samples") or []
    if raw:
        examples = random.sample(raw, min(40, len(raw)))
    else:
        examples = [generate_sentence(persona["chain"]) for _ in range(8)]
    samples = "\n".join(f"- {line}" for line in examples)

    system_prompt = (
        f"You are roleplaying as {name}, a regular member of a Discord server. "
        "You are NOT an assistant. Never offer help, never explain yourself, "
        "never mention being an AI. Reply with ONE short Discord message, the "
        "way this person would type it in chat.\n\n"
        "Copy their style exactly from the examples below: same language, "
        "same slang, same casing and punctuation habits, similar message "
        "length, same use (or absence) of emoji. If the examples are in "
        "Portuguese, answer in Portuguese. Stay in character even if the "
        "question is odd.\n\n"
        f"Real messages {name} has written:\n{samples}"
    )

    # Prior turns in this channel, so follow-up questions make sense. Each
    # user turn is prefixed with who said it, since several people may chat.
    history = histories.setdefault(channel_id, deque(maxlen=2 * HISTORY_TURNS))
    user_turn = f"{asker}: {question}" if asker else question
    messages = list(history) + [{"role": "user", "content": user_turn}]

    # Blocking HTTP call, so run it in a worker thread to keep the bot responsive.
    try:
        answer = await asyncio.to_thread(claude_reply, system_prompt, messages)
    except Exception as exc:
        # Log the real error; don't leak API/auth details into chat.
        print(f"ERROR calling Claude: {exc!r}")
        return "..."

    # Remember this exchange (the deque drops the oldest turns automatically).
    history.append({"role": "user", "content": user_turn})
    history.append({"role": "assistant", "content": answer})
    return answer


# /ask <question> — have Claude answer in the active persona's voice.
@client.tree.command(name="ask", description="Ask the active persona anything (Claude)")
async def ask(interaction: discord.Interaction, question: str):
    g = state(interaction.guild)
    persona = active_persona(interaction.guild)
    if persona is None:
        await interaction.response.send_message(
            "I'm not pretending to be anyone yet. Use /mimic or /servermimic first!"
        )
        return

    # The Claude call takes a few seconds; defer so Discord doesn't time out.
    await interaction.response.defer()

    answer = await persona_answer(
        persona, question, interaction.channel_id, interaction.user.display_name
    )

    # Echo the question (slash-command input is otherwise invisible to others),
    # then the answer. Trimmed to stay under Discord's 2000-character limit.
    reply = f"> {interaction.user.display_name}: {question}\n**{persona['name']}**: {answer}"
    await interaction.followup.send(
        reply[:2000], allowed_mentions=discord.AllowedMentions.none()
    )


# Synchronous Claude call (runs inside asyncio.to_thread). Kept lazy-imported so
# the bot still starts even if the `anthropic` package isn't installed.
def claude_reply(system_prompt: str, messages: list) -> str:
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "ANTHROPIC_API_KEY is not set. Add it to secrets.env to use /ask."

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        system=system_prompt,
        messages=messages,
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
__version__ = "0.4.0"


# Only connect to Discord when run directly (not when imported by tests).
if __name__ == "__main__":
    print(f"markov_bot_julia v{__version__}")
    client.run(token)
