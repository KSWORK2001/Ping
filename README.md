# Ping — a Discord-controlled agent for your PC

Control and monitor your computer from your phone through a private Discord
channel. Ping is a Discord bot running on your PC that reads chat commands and
replies with screenshots, command output, and status.

## What works today (Phase 1 & 2)

| Command | What it does |
|---|---|
| `!status` / `!ping` | Health + CPU/RAM/active window |
| `!shot [mon]` | Screenshot now (`0`=all monitors, `1`=primary, `2`=second) |
| `!watch [sec] [mon]` / `!unwatch` | Post a screenshot every N seconds (poor-man's live view) |
| `!sh <powershell>` | Run a shell command, return output |
| `!claude <prompt>` | Run a headless Claude Code task in `WORKDIR` |
| `!open <app>` | Launch `teams` / `outlook` / `claude` / `cowork` / any exe |
| `!focus <app>` | Bring an app window to the foreground |
| `!type <text>` / `!key ctrl+c` / `!click x y` / `!screensize` | Desktop automation |
| `!golive` | Best-effort Discord screen-share (see Phase 3) |
| `!cmds` | List commands |

## Setup

1. `pip install -r requirements.txt`
2. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications)
   (see the walkthrough below), enable **Message Content Intent**, invite it to a
   server you own.
3. `copy .env.example .env` and fill in `DISCORD_TOKEN` and `ALLOWED_USER_IDS`
   (your own user ID — without it the bot would obey anyone).
4. `python bot.py`

## Discord Developer Portal walkthrough

1. **New Application** → name it `Ping` → Create.
2. Left sidebar → **Bot** → **Reset Token** → copy it into `DISCORD_TOKEN` in `.env`.
   (Never commit `.env` — it's gitignored.)
3. On the Bot page, scroll to **Privileged Gateway Intents** → turn ON
   **MESSAGE CONTENT INTENT** → Save. (Required so Ping can read your commands.)
4. Left sidebar → **OAuth2 → URL Generator**:
   - Scopes: check **bot**
   - Bot Permissions: **Send Messages**, **Attach Files**, **Read Message History**
   - Copy the generated URL at the bottom, open it, and add the bot to your server.
5. In Discord (desktop or phone): Settings → Advanced → enable **Developer Mode**.
   Right-click your own name → **Copy User ID** → put it in `ALLOWED_USER_IDS`.
   (Optional) Right-click the channel → **Copy Channel ID** → `COMMAND_CHANNEL_IDS`.

## Security notes

- Ping obeys only the user IDs in `ALLOWED_USER_IDS`. Set it.
- `!sh`, `!claude`, `!type`, `!click` are powerful — anyone with those IDs
  effectively has remote control of this PC. Use a private channel.
- `!claude` runs with normal permission prompts unless you add
  `--dangerously-skip-permissions` to `CLAUDE_EXTRA_ARGS` (full unattended autonomy).

## Phase 3 — live screen share

Discord **bots cannot screen-share** (no API). True "Go Live" requires driving
the real Discord desktop client. `live.py` is a best-effort stub; for reliable
monitoring prefer `!watch`. We can wire up the click path once we record your
exact Discord layout.
