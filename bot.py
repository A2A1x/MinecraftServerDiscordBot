import asyncio
import os
import time

import discord
from discord.ext import tasks
from mcstatus import JavaServer

from monitor import (
    ServerMonitor,
    format_duration,
    format_players,
    is_server_live,
    server_start_time,
)


def load_env(path=".env"):
    """Minimal .env loader so no python-dotenv dependency is needed."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


load_env()

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
MC_HOST = os.environ.get("MC_HOST", "127.0.0.1")
MC_PORT = int(os.environ.get("MC_PORT", "25565"))
POLL = float(os.environ.get("POLL_INTERVAL", "30"))
GUILD_ID = os.environ.get("GUILD_ID")  # optional: instant slash-command sync

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)
monitor = ServerMonitor()


async def check() -> bool:
    """Liveness via a Minecraft status ping, off the event-loop thread (blocks).

    A ping (not a bare TCP connect) so a stopped server behind a playit.gg
    tunnel — whose edge still accepts connections — is correctly seen as down.
    """
    return await asyncio.to_thread(is_server_live, MC_HOST, MC_PORT)


@tasks.loop(seconds=POLL)
async def poll():
    now = time.time()
    up = await check()
    prev = monitor.uptime(now)
    start = None
    if up and monitor.started_at is None:  # up-transition: get true OS start time
        start = await asyncio.to_thread(server_start_time, MC_HOST, MC_PORT)
    event = monitor.update(up, now, start)
    if event is None:
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"channel {CHANNEL_ID} not found")
        return
    if event == "started":
        await channel.send("🟢 **Minecraft server started**") # type: ignore
    else:
        tail = f" (was up for {format_duration(prev)})" if prev else ""
        await channel.send(f"🔴 **Minecraft server went down**{tail}") # type: ignore


@poll.before_loop
async def _before():
    await bot.wait_until_ready()


async def fetch_players():
    """(names, online, max, partial). Query gives the full list; status ping's
    sample is a fallback that servers may truncate (partial=True)."""
    server = JavaServer(MC_HOST, MC_PORT)
    try:
        q = await server.async_query()
        return q.players.names, q.players.online, q.players.max, False # type: ignore
    except Exception:
        s = await server.async_status()
        names = [p.name for p in (s.players.sample or [])]
        return names, s.players.online, s.players.max, True


@tree.command(description="Show server status, uptime, and online players")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    now = time.time()
    if not await check():
        await interaction.followup.send("🔴 Offline")
        return
    ut = monitor.uptime(now)
    line = f"🟢 Online — up for {format_duration(ut)}" if ut else "🟢 Online"
    try:
        names, online, mx, partial = await fetch_players()
        line += "\n" + format_players(names, online, mx, partial)
    except Exception:
        pass  # reachable but ping failed; still report online + uptime
    await interaction.followup.send(line)


@bot.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    now = time.time()
    up = await check()
    start = await asyncio.to_thread(server_start_time, MC_HOST, MC_PORT) if up else None
    monitor.prime(up, now, start)  # adopt current state silently, no false alert
    if not poll.is_running():
        poll.start()
    print(f"Logged in as {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)
