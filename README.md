# Minecraft Server Discord Bot

Reports your locally hosted Minecraft server's status to a Discord channel. It
polls the server's TCP port and posts when the server **starts** or **goes
down**, and answers `/status` with the current state, uptime, and the list of
online players.

The player list uses the Minecraft **Query** protocol for the full roster. If
Query isn't enabled it falls back to the status ping's player sample, which
servers may truncate for large player counts (shown as "showing N of M"). To
get the guaranteed-complete list, set `enable-query=true` in `server.properties`
and restart the server (`query.port` defaults to the server port).

## How it works

The bot connects to `MC_HOST:MC_PORT` every `POLL_INTERVAL` seconds. A successful
connection means the server is up. It only posts on a change of state, so no spam.
Uptime is measured from when the bot first saw the server come up.

## Setup

1. Create a bot at <https://discord.com/developers/applications> → **Bot** → copy
   the token. Under **Bot**, the default intents are enough (no privileged
   intents needed). Invite it with the `applications.commands` and `bot` scopes
   and permission to send messages in your channel.
2. Get the target channel's ID (Discord → User Settings → Advanced → Developer
   Mode on, then right-click the channel → Copy Channel ID).
3. Configure and install:

```bash
copy .env.example .env
py -m pip install -r requirements.txt
```

Edit `.env` with your `DISCORD_TOKEN` and `CHANNEL_ID`. Set `GUILD_ID` to your
server's ID for instant slash-command availability (global sync can take up to
an hour).

4. Run it (keep this running alongside your Minecraft server):

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
| `POLL_INTERVAL` | `30` | Seconds between checks |
| `GUILD_ID` | — | Optional, for instant slash-command sync |

## Test

```bash
py test_monitor.py
```
