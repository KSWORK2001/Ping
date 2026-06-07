"""Ping - a Discord-controlled agent for your PC.

Run:  python bot.py
Talk to it from the Discord mobile app in a private channel, e.g.:
    !status            !shot             !watch 10
    !sh Get-Date       !claude summarize the latest changes
    !open teams        !focus outlook    !type hello
"""
import asyncio
import ctypes
import io
import sys

# Per-monitor-v2 DPI awareness MUST be set before pyautogui/mss are imported so
# that UIA element rectangles and pyautogui click coordinates share one space.
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

import discord
from discord.ext import commands

from applog import logger

import config
import screen
import system
import apps
import automation
import live
import brain
import agentloop
import workflows

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=config.COMMAND_PREFIX,
    intents=intents,
    help_command=None,
)

# channel_id -> asyncio.Task for periodic screenshots
_watchers = {}
# channel_id -> asyncio.Task for a running vision agent task
_agent_tasks = {}
# channel_id -> {task, history, completed} of the last finished agent run, so a
# successful run can be saved as a reusable workflow with !save.
_last_run = {}

STOP_WORDS = {"stop", "abort", "cancel", "halt"}


# ---------- security gate ----------
@bot.check
async def _authorized(ctx):
    if config.ALLOWED_USER_IDS and ctx.author.id not in config.ALLOWED_USER_IDS:
        return False
    if config.COMMAND_CHANNEL_IDS and ctx.channel.id not in config.COMMAND_CHANNEL_IDS:
        return False
    return True


# ---------- output helpers ----------
async def send_text(channel, text):
    text = (text or "").strip() or "[empty]"
    if len(text) <= 1900:
        await channel.send(f"```\n{text}\n```")
    else:
        await channel.send(file=discord.File(io.BytesIO(text.encode()), filename="output.txt"))


async def send_shot(channel, monitor=0):
    buf = await asyncio.to_thread(screen.capture, monitor)
    await channel.send(file=discord.File(buf, filename="screen.png"))


# ---------- lifecycle ----------
@bot.event
async def on_ready():
    loop = asyncio.get_running_loop()
    logger.info("online as %s (id %s); event loop = %s", bot.user, bot.user.id, type(loop).__name__)
    logger.info("allowed users: %s", config.ALLOWED_USER_IDS or "ANYONE")
    print(f"[Ping] online as {bot.user}; loop={type(loop).__name__}", flush=True)


def _allowed(message):
    if config.ALLOWED_USER_IDS and message.author.id not in config.ALLOWED_USER_IDS:
        return False
    if config.COMMAND_CHANNEL_IDS and message.channel.id not in config.COMMAND_CHANNEL_IDS:
        return False
    return True


async def _drive(channel, task, coro):
    """Run a cancellable agent coroutine (live loop or replay) for this channel,
    record its outcome as the channel's last run, and return its result dict."""
    if channel.id in _agent_tasks and not _agent_tasks[channel.id].done():
        await channel.send("A task is already running here. Send `stop` first.")
        return None
    t = asyncio.create_task(coro)
    _agent_tasks[channel.id] = t
    result = None
    try:
        result = await t
    except asyncio.CancelledError:
        await channel.send("Task stopped.")
    except Exception as e:
        await channel.send(f"Task error: {e}")
    finally:
        _agent_tasks.pop(channel.id, None)
    if isinstance(result, dict) and result.get("history") is not None:
        _last_run[channel.id] = {
            "task": task,
            "history": result["history"],
            "completed": result.get("completed", False),
        }
    return result


async def run_agent(channel, task):
    """Start a cancellable vision agent loop for this channel."""
    await _drive(channel, task, agentloop.run(channel, task, send_shot))


async def run_workflow(channel, name):
    """Replay a saved workflow (hybrid) and log the run to the store."""
    wf = await asyncio.to_thread(workflows.get, name)
    if not wf:
        await channel.send(f"No workflow named **{name}**. See `!flows`.")
        return
    run_id = await asyncio.to_thread(workflows.start_run, wf["id"], "replay")
    result = await _drive(channel, wf["goal"], agentloop.replay(channel, wf, send_shot))
    if result is None:  # busy, cancelled, or errored
        await asyncio.to_thread(
            workflows.finish_run, run_id, wf["id"], "failed", "replay", 0, "stopped"
        )
        return
    status = "success" if result.get("completed") else "failed"
    await asyncio.to_thread(
        workflows.finish_run, run_id, wf["id"], status,
        result.get("mode", "replay"), len(result.get("history") or []),
        result.get("summary", ""),
    )


def stop_agent(channel_id):
    t = _agent_tasks.get(channel_id)
    if t and not t.done():
        t.cancel()
        return True
    return False


async def dispatch(channel, action):
    """Execute a structured action chosen by the natural-language brain."""
    a = (action.get("action") or "").lower()
    logger.info("dispatch: %s", a)
    if a == "agent_task":
        await run_agent(channel, action.get("task") or action.get("prompt", ""))
        return
    if a == "run_workflow":
        await run_workflow(channel, action.get("name", ""))
        return
    if a == "screenshot":
        await send_shot(channel)
    elif a == "status":
        await channel.send(system.status())
    elif a == "shell":
        out = await system.run_shell(action.get("command", ""))
        await send_text(channel, out)
    elif a == "open_app":
        await channel.send(await asyncio.to_thread(apps.launch, action.get("name", "")))
    elif a == "focus_app":
        await channel.send(await asyncio.to_thread(apps.focus, action.get("name", "")))
    elif a == "type":
        await channel.send(await asyncio.to_thread(automation.type_text, action.get("text", "")))
    elif a == "key":
        await channel.send(await asyncio.to_thread(automation.press, action.get("combo", "")))
    elif a == "claude_task":
        await channel.send("Working on it via Claude Code...")
        out = await system.run_claude(action.get("prompt", ""))
        await send_text(channel, out)
    elif a == "reply":
        await send_text(channel, action.get("text", "(no reply)"))
    else:
        await channel.send(f"Unknown action: {action}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    ctx = await bot.get_context(message)
    if ctx.valid:  # it's a !command -> normal command handling (with its own auth check)
        await bot.process_commands(message)
        return
    # Not a command: route plain English through the brain (auth-gated).
    content = message.content.strip()
    if not config.NL_ENABLED or not content or not _allowed(message):
        return
    # Fast-path: a bare "stop"/"abort" cancels a running agent task.
    if content.lower() in STOP_WORDS:
        if stop_agent(message.channel.id):
            await message.channel.send("Stopping the running task...")
        else:
            await message.channel.send("Nothing running to stop.")
        return
    known = await asyncio.to_thread(workflows.names)
    async with message.channel.typing():
        action = await brain.decide(content, known)
    await dispatch(message.channel, action)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return  # silently ignore unauthorized / wrong-channel
    if isinstance(error, commands.CommandNotFound):
        return
    await send_text(ctx.channel, f"Error: {error}")


# ---------- monitoring ----------
@bot.command()
async def ping(ctx):
    await ctx.send(f"pong - latency {round(bot.latency * 1000)} ms")


@bot.command()
async def status(ctx):
    await ctx.send(system.status())


@bot.command()
async def shot(ctx, monitor: int = 0):
    """!shot [monitor]  - screenshot now (0=all, 1=primary, 2=second...)"""
    async with ctx.typing():
        await send_shot(ctx.channel, monitor)


@bot.command()
async def watch(ctx, seconds: int = 15, monitor: int = 0):
    """!watch [seconds] [monitor]  - post a screenshot every N seconds."""
    seconds = max(3, seconds)
    old = _watchers.pop(ctx.channel.id, None)
    if old:
        old.cancel()

    async def loop():
        try:
            while True:
                await send_shot(ctx.channel, monitor)
                await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            pass

    _watchers[ctx.channel.id] = asyncio.create_task(loop())
    await ctx.send(f"Watching this screen every {seconds}s. Stop with !unwatch.")


@bot.command()
async def unwatch(ctx):
    task = _watchers.pop(ctx.channel.id, None)
    if task:
        task.cancel()
        await ctx.send("Stopped watching.")
    else:
        await ctx.send("Not watching here.")


# ---------- execution ----------
@bot.command(name="sh")
async def sh(ctx, *, command):
    """!sh <powershell>  - run a shell command and return output."""
    async with ctx.typing():
        out = await system.run_shell(command)
    await send_text(ctx.channel, out)


@bot.command()
async def claude(ctx, *, prompt):
    """!claude <prompt>  - run a headless Claude Code task in WORKDIR."""
    await ctx.send("Running Claude Code... (this can take a while)")
    async with ctx.typing():
        out = await system.run_claude(prompt)
    await send_text(ctx.channel, out)


# ---------- apps ----------
@bot.command(name="open")
async def open_app(ctx, *, name):
    """!open <app>  - launch teams / outlook / claude / cowork / <exe>."""
    await ctx.send(await asyncio.to_thread(apps.launch, name))


@bot.command()
async def focus(ctx, *, name):
    """!focus <app>  - bring an app window to the foreground."""
    await ctx.send(await asyncio.to_thread(apps.focus, name))


# ---------- automation ----------
@bot.command(name="type")
async def type_cmd(ctx, *, text):
    await ctx.send(await asyncio.to_thread(automation.type_text, text))


@bot.command()
async def key(ctx, *, combo):
    """!key ctrl+c  - press a hotkey combo."""
    await ctx.send(await asyncio.to_thread(automation.press, combo))


@bot.command()
async def click(ctx, x: int, y: int):
    await ctx.send(await asyncio.to_thread(automation.click, x, y))


@bot.command()
async def screensize(ctx):
    await ctx.send(await asyncio.to_thread(automation.screen_size))


# ---------- vision agent loop ----------
@bot.command(name="do")
async def do_cmd(ctx, *, task):
    """!do <goal>  - multi-step task; Claude watches the screen after each step."""
    await run_agent(ctx.channel, task)


@bot.command(name="stop")
async def stop_cmd(ctx):
    """!stop  - abort the running agent task in this channel."""
    if stop_agent(ctx.channel.id):
        await ctx.send("Stopping the running task...")
    else:
        await ctx.send("Nothing running to stop.")


# ---------- workflows (saved, replayable processes) ----------
@bot.command(name="save")
async def save_cmd(ctx, *, name):
    """!save <name>  - save this channel's last completed task as a workflow."""
    last = _last_run.get(ctx.channel.id)
    if not last or not last.get("history"):
        await ctx.send("No finished task to save here yet. Run a task first, then `!save <name>`.")
        return
    if not last.get("completed"):
        await ctx.send("Note: the last run didn't report completion - saving it anyway.")
    await asyncio.to_thread(workflows.save, name, last["task"], last["history"])
    await ctx.send(
        f"Saved workflow **{name}** ({len(last['history'])} steps). "
        f"Replay with `!runflow {name}`."
    )


@bot.command(name="flows")
async def flows_cmd(ctx):
    """!flows  - list saved workflows."""
    rows = await asyncio.to_thread(workflows.list_all)
    if not rows:
        await ctx.send("No saved workflows yet. Finish a task, then `!save <name>`.")
        return
    lines = []
    for r in rows:
        status = r.get("last_status") or "never run"
        lines.append(
            f"- **{r['name']}** - {r['step_count']} steps, {r['run_count']} runs ({status})\n"
            f"   {(r['goal'] or '')[:80]}"
        )
    await ctx.send("**Saved workflows**\n" + "\n".join(lines))


@bot.command(name="runflow")
async def runflow_cmd(ctx, *, name):
    """!runflow <name>  - replay a saved workflow (with live-vision fallback)."""
    await run_workflow(ctx.channel, name)


@bot.command(name="flow")
async def flow_cmd(ctx, sub, *, name=""):
    """!flow show <name> | !flow rm <name>  - inspect or delete a workflow."""
    sub = sub.lower()
    if sub in ("rm", "del", "delete"):
        ok = await asyncio.to_thread(workflows.delete, name)
        await ctx.send(f"Deleted **{name}**." if ok else f"No workflow named **{name}**.")
    elif sub == "show":
        wf = await asyncio.to_thread(workflows.get, name)
        if not wf:
            await ctx.send(f"No workflow named **{name}**.")
            return
        lines = [
            f"{wf['name']} - {wf['goal']}",
            f"runs: {wf['run_count']}, last status: {wf.get('last_status') or 'never run'}",
            "",
        ]
        for s in wf["steps"]:
            tag = f' "{s["element"]["name"]}"' if s.get("element") else ""
            lines.append(f"{s['idx']}. {s['action'].get('type', '?')}{tag}")
        await send_text(ctx.channel, "\n".join(lines))
    else:
        await ctx.send("Usage: `!flow show <name>` or `!flow rm <name>`.")


# ---------- live screen share (best-effort) ----------
@bot.command()
async def golive(ctx):
    await ctx.send("Attempting live screen-share...")
    await ctx.send(await asyncio.to_thread(live.go_live))


# ---------- help ----------
@bot.command(name="cmds")
async def cmds(ctx):
    await ctx.send(
        "**Ping commands**  (or just type plain English - the brain figures it out)\n"
        "`!status` `!ping` - health\n"
        "`!shot [mon]` - screenshot now\n"
        "`!watch [sec] [mon]` / `!unwatch` - live screenshots\n"
        "`!sh <powershell>` - run a command\n"
        "`!claude <prompt>` - run Claude Code (code/files)\n"
        "`!do <goal>` / `stop` - multi-step task, Claude watches the screen each step\n"
        "`!save <name>` - save the last finished task as a workflow\n"
        "`!flows` / `!runflow <name>` / `!flow show|rm <name>` - saved workflows\n"
        "`!open <app>` / `!focus <app>` - teams, outlook, claude, cowork...\n"
        "`!type <text>` `!key ctrl+c` `!click x y` `!screensize` - automation\n"
        "`!golive` - best-effort Discord screen-share"
    )


def main():
    # On Windows, asyncio subprocesses require the Proactor loop. Force it so a
    # different default policy can never silently break claude/shell calls.
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception as e:  # pragma: no cover
            logger.warning("could not set Proactor policy: %s", e)
    setup_logging = __import__("applog").setup
    setup_logging()
    workflows.init()  # create the SQLite workflow store if it doesn't exist
    problems = config.validate()
    for p in problems:
        logger.warning(p)
        print(f"[Ping] WARNING: {p}", flush=True)
    if not config.DISCORD_TOKEN:
        print("[Ping] No DISCORD_TOKEN - edit .env first. Exiting.", flush=True)
        return
    logger.info("starting bot")
    bot.run(config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
