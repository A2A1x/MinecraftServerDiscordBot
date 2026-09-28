import asyncio
import json
import os
import time
import urllib.request

import discord
from discord.ext import tasks
from mcstatus import JavaServer

from monitor import (
    ServerMonitor,
    bridge_line,
    format_duration,
    is_server_live,
    players_value,
    public_status,
    rcon_command,
    server_start_time,
)
from web import StatusPage


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
SERVER_IP = os.environ.get("SERVER_IP", "")    # address players join (set in .env)
MODPACK_URL = os.environ.get("MODPACK_URL", "")  # modpack download link (set in .env)
LOG_PATH = os.environ.get("LOG_PATH", "")            # server latest.log -> relay chat to Discord
RCON_PORT = int(os.environ.get("RCON_PORT", "25575"))
RCON_PASSWORD = os.environ.get("RCON_PASSWORD", "")  # relay Discord -> game via RCON tellraw
OWNER_ID = os.environ.get("OWNER_ID", "")            # your Discord user ID; approves /startserver
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8765")
WEB_PORT = int(os.environ.get("WEB_PORT") or 0)      # live status page; off when unset
STATUS_URL = os.environ.get("STATUS_URL", "")        # public URL of that page, linked in the embed
STATUS_MSG_FILE = "status_msg.json"                  # remembers the live embed across restarts

intents = discord.Intents.default()
if RCON_PASSWORD:  # reading messages to relay into the game needs the privileged intent
    intents.message_content = True
bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)
monitor = ServerMonitor()

GREEN = discord.Color.brand_green()
RED = discord.Color.brand_red()


def make_embed(title: str, color: discord.Color, description: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color,
                         timestamp=discord.utils.utcnow())


def add_join_info(e: discord.Embed) -> discord.Embed:
    if SERVER_IP:
        e.add_field(name="Server IP", value=f"`{SERVER_IP}`", inline=False)
    if MODPACK_URL:
        e.add_field(name="Modpack", value=f"[Download]({MODPACK_URL})", inline=False)
    return e


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
    _snapshot[0] = await snapshot(up)
    if event != "started":
        await refresh_status()  # on "stopped" this flips the live embed to offline
    if event is None:
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"channel {CHANNEL_ID} not found")
        return
    if event == "started":
        page.start()
        track_status(await channel.send(embed=overview_embed(_snapshot[0])))  # type: ignore
    else:
        page.stop()
        e = make_embed("🔴 Minecraft server went down", RED)
        if prev:
            e.add_field(name="Was up for", value=format_duration(prev))
        await channel.send(embed=e)  # type: ignore


@poll.before_loop
async def _before():
    await bot.wait_until_ready()


_log_pos = [0]


def _read_new_lines():
    """Lines appended to LOG_PATH since the last read (handles log rotation)."""
    try:
        size = os.path.getsize(LOG_PATH)
        if size < _log_pos[0]:
            _log_pos[0] = 0
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            f.seek(_log_pos[0])
            data = f.read()
            _log_pos[0] = f.tell()
        return data.splitlines()
    except OSError:
        return []


@tasks.loop(seconds=3)
async def chat_relay():
    """Game chat/joins/leaves -> Discord (reads the server log)."""
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return
    for line in await asyncio.to_thread(_read_new_lines):
        msg = bridge_line(line)
        if msg:
            await channel.send(msg, allowed_mentions=discord.AllowedMentions.none())  # type: ignore


@chat_relay.before_loop
async def _cr_before():
    await bot.wait_until_ready()
    try:
        _log_pos[0] = os.path.getsize(LOG_PATH)  # start at the end; don't replay history
    except OSError:
        _log_pos[0] = 0


@bot.event
async def on_message(message: discord.Message):
    """Discord -> game via RCON tellraw (needs the message-content intent)."""
    if message.author.bot or message.channel.id != CHANNEL_ID or not RCON_PASSWORD:
        return
    content = message.clean_content.strip()
    if not content:
        return
    cmd = "tellraw @a " + json.dumps(
        {"text": f"[Discord] {message.author.display_name}: {content}"}, ensure_ascii=False)
    try:
        await asyncio.to_thread(rcon_command, MC_HOST, RCON_PORT, RCON_PASSWORD, cmd)
    except Exception:
        pass


async def fetch_players():
    """(names, online, max, partial). Query gives the full list; status ping's
    sample is a fallback that servers may truncate (partial=True)."""
    server = JavaServer(MC_HOST, MC_PORT)
    try:
        q = await server.async_query()
        return q.players.list, q.players.online, q.players.max, False
    except Exception:
        s = await server.async_status()
        names = [p.name for p in (s.players.sample or [])]
        return names, s.players.online, s.players.max, True


def _dashboard_status() -> dict:
    """Dashboard's /api/server/status (blocking; run off-thread). Carries private
    data (console, host stats) — only pass it through public_fields()."""
    with urllib.request.urlopen(DASHBOARD_URL.rstrip("/") + "/api/server/status", timeout=5) as r:
        return json.loads(r.read().decode() or "{}")


async def snapshot(up: bool) -> dict:
    """Public-only server data behind both the live embed and the web page."""
    snap = {"online": up, "updated": time.time(), "server_ip": SERVER_IP, "modpack_url": MODPACK_URL}
    if not up:
        return snap
    try:
        snap.update(public_status(await asyncio.to_thread(_dashboard_status)))
    except Exception:
        pass  # dashboard not running; ping-only data
    ut = monitor.uptime(time.time())
    snap["uptime"] = format_duration(ut) if ut else "unknown"
    try:
        names, online, mx, partial = await fetch_players()
        snap["players"] = {"names": sorted(names), "online": online, "max": mx, "partial": partial}
    except Exception:
        pass  # reachable but ping failed; still report online + uptime
    return snap


def overview_embed(s: dict) -> discord.Embed:
    if not s["online"]:
        return add_join_info(make_embed("🔴 Server Offline", RED))
    e = make_embed(f"🟢 {s.get('name') or 'Server'} Online", GREEN,
                   f"[Live status page]({STATUS_URL})" if STATUS_URL else None)
    e.add_field(name="Uptime", value=s["uptime"])
    if s.get("version"):
        e.add_field(name="Version", value=s["version"])
    if s.get("tps") is not None:
        mspt = f" ({s['mspt']} ms/tick)" if s.get("mspt") is not None else ""
        e.add_field(name="TPS", value=f"{s['tps']}{mspt}")
    if s.get("motd"):
        e.add_field(name="MOTD", value=s["motd"], inline=False)
    p = s.get("players")
    if p:
        e.add_field(name=f"Players — {p['online']}/{p['max']}",
                    value=players_value(p["names"], p["online"], p["partial"]), inline=False)
    e.set_footer(text="Live — last updated")
    return add_join_info(e)


_snapshot: list[dict] = [{"online": False}]
page = StatusPage(WEB_PORT, lambda: _snapshot[0])  # no-op when WEB_PORT unset
_status_msg: list[discord.Message | discord.WebhookMessage | None] = [None]


def track_status(msg):
    """Make msg the live embed and remember it so a restart keeps updating it."""
    _status_msg[0] = msg
    try:
        with open(STATUS_MSG_FILE, "w") as f:
            json.dump({"channel": msg.channel.id, "message": msg.id}, f)
    except OSError:
        pass


async def load_status_msg():
    try:
        with open(STATUS_MSG_FILE) as f:
            ids = json.load(f)
        _status_msg[0] = await bot.get_channel(ids["channel"]).fetch_message(ids["message"])  # type: ignore
    except (OSError, ValueError, KeyError, AttributeError, discord.HTTPException):
        pass  # none saved, or the message/channel is gone


async def refresh_status():
    """Edit the live embed in place with the latest snapshot."""
    msg = _status_msg[0]
    if msg is None:
        return
    try:
        await msg.edit(embed=overview_embed(_snapshot[0]))
    except discord.HTTPException:  # deleted, or interaction webhook expired
        _status_msg[0] = None


@tree.command(description="Post the live server overview here (replaces the previous one)")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    _snapshot[0] = await snapshot(await check())
    msg = await interaction.followup.send(embed=overview_embed(_snapshot[0]), wait=True)
    # re-fetch as a channel message: interaction webhook edits expire after 15 min
    if interaction.channel is not None:
        try:
            msg = await interaction.channel.fetch_message(msg.id)  # type: ignore
        except discord.HTTPException:
            pass
    track_status(msg)


def _dashboard_start_last() -> dict:
    """Ask the dashboard to start the last-used server (blocking; run off-thread)."""
    req = urllib.request.Request(
        DASHBOARD_URL.rstrip("/") + "/api/server/start-last",
        data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode() or "{}")


class ConfirmStart(discord.ui.View):
    """Owner's DM approval buttons. Persistent (timeout=None + custom_ids) so they
    keep working after the view's 5-minute window and across bot restarts."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start server", style=discord.ButtonStyle.success,
                       custom_id="startserver:confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != str(OWNER_ID):
            await interaction.response.send_message("Only the owner can approve.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Starting the server…", view=None)
        try:
            res = await asyncio.to_thread(_dashboard_start_last)
        except Exception as e:
            await interaction.followup.send(f"Couldn't reach the dashboard: {e}")
            return
        if res.get("error"):
            await interaction.followup.send(f"Dashboard: {res['error']}")
        elif res.get("already_running"):
            await interaction.followup.send(f"ℹ️ {res.get('server','The server')} is already running.")
        else:
            await interaction.followup.send(f"✅ {res.get('server','Server')} is starting.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger,
                       custom_id="startserver:cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Start request cancelled.", view=None)


@tree.command(description="Request that the Minecraft server be started (owner approves via DM)")
async def startserver(interaction: discord.Interaction):
    if not OWNER_ID:
        await interaction.response.send_message(
            "Start requests aren't configured (no OWNER_ID set).", ephemeral=True)
        return
    if await check():
        await interaction.response.send_message("The server is already online.", ephemeral=True)
        return
    await interaction.response.send_message(
        "Sent a start request to the owner for approval. ✅", ephemeral=True)
    try:
        owner = await bot.fetch_user(int(OWNER_ID))
        await owner.send(
            f"🟢 **{interaction.user}** requested to start the Minecraft server.",
            view=ConfirmStart())
    except Exception as e:
        await interaction.followup.send(f"Couldn't DM the owner: {e}", ephemeral=True)


async def setup_hook():
    """Runs once at login (unlike on_ready, which fires again on every reconnect)."""
    bot.add_view(ConfirmStart())  # persistent: makes the buttons work after restarts
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()


bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    now = time.time()
    up = await check()
    start = await asyncio.to_thread(server_start_time, MC_HOST, MC_PORT) if up else None
    monitor.prime(up, now, start)  # adopt current state silently, no false alert
    _snapshot[0] = await snapshot(up)
    if up:
        page.start()
    await load_status_msg()
    if not poll.is_running():
        poll.start()
    if LOG_PATH and not os.path.exists(LOG_PATH):
        print(f"WARNING: LOG_PATH not found, chat relay disabled: {LOG_PATH}")
    elif LOG_PATH and not chat_relay.is_running():
        chat_relay.start()
    print(f"Logged in as {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)
