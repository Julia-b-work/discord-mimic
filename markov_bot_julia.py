from dotenv import load_dotenv
import os
import json
import asyncio
import random
from typing import Literal
import discord
from discord.ext import commands

load_dotenv("secrets.env")

token = os.getenv("DISCORD_TOKEN")

if token is None:
    print("ERROR: DISCORD_TOKEN is not set. Create a .env file with the token.")
    raise SystemExit(1)

intents = discord.Intents.default()
intents.message_content = True

client = commands.Bot(command_prefix="!", intents=intents)

# Learned state, reloaded from disk in on_ready.
users = {}
current_user_id = None

# "user" -> speak as the current user; "server" -> speak as the whole server.
mode = "user"

# The whole-server chain + media, built by /servermimic. None until learned.
server = None

# Spontaneous-reply pressure; resets to 0 after the bot speaks.
heat = 0

MEMORY_FILE = "memory.json"


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
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


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


@client.event
async def on_ready():
    global users, current_user_id, mode, server
    print(f"logged in as {client.user}")
    users, current_user_id, mode, server = load_users()
    await client.tree.sync()


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


# Collect image/GIF URLs from a message (direct uploads + embedded links).
def extract_media(message):
    urls = []

    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            urls.append(attachment.url)

    for word in message.content.split():
        if word.startswith("http") and any(host in word for host in _GIF_HOSTS):
            urls.append(word)

    return urls


# Scan channels, build one chain per target user, store + save, then reply.
async def run_mimic(interaction, targets, channels):
    # Post an editable "working..." message; updated live during the scan.
    progress = await interaction.followup.send("Snooping through messages...")

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
        except discord.Forbidden:
            # No "Read Message History" permission: skip this channel, keep going.
            pass
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

    await interaction.followup.send("\n".join(results))


# Scan channels, merge EVERYONE's messages into one chain, then switch mode.
# Same overall shape as run_mimic, but there's a single combined personality.
async def run_server_mimic(interaction, channels):
    progress = await interaction.followup.send("Snooping through messages...")

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
        except discord.Forbidden:
            pass
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
        await interaction.followup.send(
            "Couldn't read anyone's messages. Is this server a ghost town?"
        )
        return

    chain = build_markov_chain(all_messages)

    # All messages too short to form any triples.
    if not chain:
        await interaction.followup.send(
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
    await interaction.followup.send(f"**{interaction.guild.name}**: {sentence}")


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
        # resolve each to a full TextChannel via the guild lookup.
        channels = [
            interaction.guild.get_channel(channel.id) for channel in select.values
        ]
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


@client.tree.command(name="mimic", description="I'm about to become someone!!!")
async def mimic(
    interaction: discord.Interaction,
    user: discord.Member,
    user2: discord.Member = None,
    user3: discord.Member = None,
    user4: discord.Member = None,
):
    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.")
        return

    targets = [u for u in (user, user2, user3, user4) if u is not None]

    names = ", ".join(u.mention for u in targets)
    await interaction.response.send_message(
        f"Which channels should I read to imitate {names}?",
        view=ChannelPicker(targets),
    )


@client.tree.command(name="servermimic", description="Become the whole server")
async def servermimic(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.")
        return

    await interaction.response.send_message(
        "Which channels should I read to imitate this server?",
        view=ChannelPicker([], server_mode=True),
    )


@client.tree.command(name="mode", description="Switch who I talk as")
async def set_mode(
    interaction: discord.Interaction,
    new_mode: Literal["user", "server"],
):
    global mode

    if new_mode == "server" and server is None:
        await interaction.response.send_message(
            "I haven't learned this server yet. Use /servermimic first!"
        )
        return

    mode = new_mode
    save_users()

    label = "the whole server" if mode == "server" else "the imitated user"
    await interaction.response.send_message(f"Switched! Now I talk as {label}.")


@client.tree.command(name="speak", description="Speak as the imitated user")
async def speak(interaction: discord.Interaction):
    if mode == "server":
        if server is None:
            await interaction.response.send_message(
                "I haven't learned this server yet. Use /servermimic first!"
            )
            return

        media = server.get("media", [])
        if media and random.random() < 0.3:
            await interaction.response.send_message(random.choice(media))
            return

        await interaction.response.send_message(generate_sentence(server["chain"]))
        return

    if current_user_id is None or current_user_id not in users:
        await interaction.response.send_message(
            "I'm not pretending to be anyone yet. Use /mimic first!"
        )
        return

    sentence = generate_sentence(users[current_user_id]["chain"])

    media = users[current_user_id].get("media", [])
    if media and random.random() < 0.3:
        await interaction.response.send_message(random.choice(media))
        return

    await interaction.response.send_message(sentence)


@client.tree.command(name="users", description="Who have I been spying on?")
async def list_users(interaction: discord.Interaction):
    if not users and server is None:
        await interaction.response.send_message(
            "I don't know anyone yet. Go run /mimic on somebody!"
        )
        return

    lines = []
    for uid, info in users.items():
        marker = " *" if (uid == current_user_id and mode == "user") else ""
        lines.append(f"**{info['name']}**{marker}")

    if server is not None:
        server_marker = " *" if mode == "server" else ""
        lines.append(f"**{server['name']}** (server){server_marker}")

    await interaction.response.send_message("\n".join(lines))


@client.tree.command(
    name="converse", description="Simulate a conversation between two users"
)
async def converse(
    interaction: discord.Interaction,
    user1: discord.Member,
    user2: discord.Member,
):
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

    lines = []
    for _ in range(5):
        lines.append(f"**{name1}**: {generate_sentence(chain1)}")
        lines.append(f"**{name2}**: {generate_sentence(chain2)}")

    await interaction.response.send_message("\n".join(lines))


@client.tree.error
async def on_tree_error(interaction: discord.Interaction, error):
    print(f"ERROR in /{interaction.command.name}: {error!r}")
    try:
        await interaction.followup.send("Something went wrong. Blame the robot, not me.")
    except Exception:
        pass


if __name__ == "__main__":
    client.run(token)
