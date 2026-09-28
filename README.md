# Minecraft Server Discord Bot

A small Discord bot for a locally hosted Minecraft (Java) server. It:

- posts an embed when the server **starts** or **goes down** (only on state changes, no spam),
- answers **`/status`** with a live embed (edited every poll) showing state, uptime, the online player list, and your join address / modpack link,
- lets anyone **`/startserver`** to request a start, which you approve from a DM (calls the dashboard),
- optionally runs a **two-way chat bridge** between the game and a Discord channel.

## How it works

Every `POLL_INTERVAL` seconds the bot does a Minecraft **status ping** of `MC_HOST:MC_PORT`
(not a bare TCP connect) to decide up/down — so a stopped server sitting behind a playit.gg
tunnel, whose edge still accepts TCP, is correctly seen as **down**. Uptime uses the server
process's real OS start time when the server is local (via `psutil`), else first-seen time.

The player list uses the Minecraft **Query** protocol for the complete roster. If query isn't
enabled it falls back to the status ping's sample, which servers may truncate (shown as
"showing N of M"). For the guaranteed-complete list, set `enable-query=true` in
`server.properties` and restart (`query.port` defaults to the server port).

## Chat bridge (optional)

If `LOG_PATH` and `RCON_PASSWORD` are set, the bot relays both ways:

- **Game → Discord**: tails the server log and posts chat and join/leave events to the channel.
- **Discord → Game**: messages in the channel are pushed in-game via RCON `tellraw` as `[Discord] name: …`.

The Discord → game direction reads message text, which needs the **Message Content**
privileged intent (Discord Developer Portal → your app → Bot → *Privileged Gateway
Intents* → enable **Message Content**). It also needs RCON enabled on the server
(`enable-rcon=true`, `rcon.password=…`). Without these two vars the bridge stays off and the
default intents are enough.

## Setup

1. Create a bot at <https://discord.com/developers/applications> → **Bot** → copy the token.
   Invite it with the `applications.commands` + `bot` scopes and permission to send messages.
   (Enable the **Message Content** intent only if you want the chat bridge.)
2. Get the channel's ID (Discord → Settings → Advanced → Developer Mode on, right-click the
   channel → Copy Channel ID).
3. Configure and install:

```bash
copy .env.example .env
py -m pip install -r requirements.txt
```

Edit `.env` with your `DISCORD_TOKEN` and `CHANNEL_ID` (set `GUILD_ID` for instant
slash-command sync — global sync can take up to an hour).

4. Run it alongside your Minecraft server:

```bash
py bot.py
```

## Config (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | — | Bot token (required) |
| `CHANNEL_ID` | — | Channel to post to (required) |
| `MC_HOST` | `127.0.0.1` | Minecraft server host |
| `MC_PORT` | `25565` | Minecraft server port |
| `POLL_INTERVAL` | `30` | Seconds between status pings |
| `GUILD_ID` | — | Optional; instant slash-command sync |
| `SERVER_IP` | — | Address players join, shown in `/status` |
| `MODPACK_URL` | — | Modpack download link, shown in `/status` |
| `LOG_PATH` | — | Server `logs/latest.log` — enables game → Discord chat relay |
| `RCON_PORT` | `25575` | RCON port for the Discord → game relay |
| `RCON_PASSWORD` | — | RCON password — enables the bridge + Message Content intent |
| `OWNER_ID` | — | Your Discord user ID; approves `/startserver` requests via DM |
| `DASHBOARD_URL` | `http://127.0.0.1:8765` | Dashboard the bot calls to start the server |

## `/startserver`

Anyone in the channel can run `/startserver`. The bot DMs the owner (`OWNER_ID`) with
**Start / Cancel** buttons; approving calls the dashboard's `/api/server/start-last` to launch
the last-used server (the dashboard and bot must run on the same machine, or `DASHBOARD_URL`
must point at it).

## Test

```bash
py test_monitor.py
```
