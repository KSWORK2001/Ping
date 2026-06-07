"""Configuration loaded from environment / .env file.

Paths auto-detect per device (no hardcoded user/home) so the same checkout runs
on any machine; every value can still be overridden via .env.
"""
import os
import shutil

from dotenv import load_dotenv

# Anchor everything to the project folder (where this file lives), so the bot
# finds ping.db / shots / logs regardless of the working directory it's launched
# from. .env is loaded from there too.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_DIR, ".env"))


def _find_claude():
    """Locate the Claude CLI on this device: explicit env, then PATH, then the
    usual install locations (resolved from this user's env, not a fixed path)."""
    env = os.getenv("CLAUDE_BIN")
    if env:
        return env
    onpath = shutil.which("claude")
    if onpath:
        return onpath
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
    for cand in (
        os.path.join(home, ".local", "bin", "claude.exe"),
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(local, "Programs", "claude", "claude.exe"),
        os.path.join(local, "AnthropicClaude", "claude.exe"),
    ):
        if os.path.exists(cand):
            return cand
    return "claude"  # last resort: rely on PATH resolution at call time


def _ids(name):
    raw = os.getenv(name, "")
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
ALLOWED_USER_IDS = _ids("ALLOWED_USER_IDS")
COMMAND_CHANNEL_IDS = _ids("COMMAND_CHANNEL_IDS")  # empty = any channel
COMMAND_PREFIX = (os.getenv("COMMAND_PREFIX", "!").strip() or "!")

SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "60"))
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "600"))
CLAUDE_BIN = _find_claude()
CLAUDE_EXTRA_ARGS = os.getenv("CLAUDE_EXTRA_ARGS", "").split()

# Natural-language brain: plain-English messages are routed through Claude CLI.
NL_ENABLED = os.getenv("NL_ENABLED", "true").lower() in ("1", "true", "yes", "on")
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "claude-haiku-4-5-20251001")
ROUTER_TIMEOUT = int(os.getenv("ROUTER_TIMEOUT", "60"))

# Vision agent loop: Claude sees a screenshot after every step and decides the next.
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "15"))
AGENT_STEP_DELAY = float(os.getenv("AGENT_STEP_DELAY", "1.2"))
AGENT_STEP_TIMEOUT = int(os.getenv("AGENT_STEP_TIMEOUT", "120"))
# Width (px) of the screenshot shown to Claude. ~1280 gives the best click
# accuracy (high-res actually hurts vision targeting). Clicks are scaled back up.
AGENT_IMAGE_WIDTH = int(os.getenv("AGENT_IMAGE_WIDTH", "1280"))
AGENT_GRID = int(os.getenv("AGENT_GRID", "100"))  # grid spacing in display px
# Precise clicking via the Windows UI Automation tree (Set-of-Marks): list real
# on-screen elements so the model clicks one by id instead of guessing pixels.
AGENT_USE_UIA = os.getenv("AGENT_USE_UIA", "true").lower() in ("1", "true", "yes", "on")
AGENT_MAX_ELEMENTS = int(os.getenv("AGENT_MAX_ELEMENTS", "55"))
# Click-accuracy debugging: when on, every agent click also posts an annotated
# full-desktop screenshot with a marker at the COMPUTED click point, so you can
# visually confirm the capture->predict->click coordinate transform.
AGENT_DEBUG_CLICKS = os.getenv("AGENT_DEBUG_CLICKS", "false").lower() in ("1", "true", "yes", "on")
# Skip MCP servers on internal router/vision calls (faster, can't hang on MCP).
CLAUDE_NO_MCP = os.getenv("CLAUDE_NO_MCP", "true").lower() in ("1", "true", "yes", "on")
WORKDIR = os.getenv("WORKDIR", PROJECT_DIR)
# SQLite store for saved workflows (recorded successful agent runs + run history).
WORKFLOW_DB = os.getenv("WORKFLOW_DB", os.path.join(WORKDIR, "ping.db"))


def _flag(name, default="true"):
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


# Local status dashboard (a small web page served in the bot's own event loop).
DASHBOARD_ENABLED = _flag("DASHBOARD_ENABLED", "true")
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")  # localhost only by default
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
DASHBOARD_REFRESH = int(os.getenv("DASHBOARD_REFRESH", "30"))   # status poll, seconds
DASHBOARD_SHOT = int(os.getenv("DASHBOARD_SHOT", "300"))        # screenshot refresh, s (0=off)
DASHBOARD_SHOT_MONITOR = int(os.getenv("DASHBOARD_SHOT_MONITOR", "1"))  # 1=primary, 0=all
DISCORD_UPDATE_EXE = os.getenv(
    "DISCORD_UPDATE_EXE",
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local")),
                 "Discord", "Update.exe"),
)


def validate():
    problems = []
    if not DISCORD_TOKEN:
        problems.append("DISCORD_TOKEN is empty (set it in .env).")
    if not ALLOWED_USER_IDS:
        problems.append(
            "ALLOWED_USER_IDS is empty - the bot would accept commands from anyone. "
            "Set at least your own user ID."
        )
    # Claude CLI is required for the brain + vision loop; warn if we can't find it.
    if not (os.path.isabs(CLAUDE_BIN) and os.path.exists(CLAUDE_BIN)) and not shutil.which(CLAUDE_BIN):
        problems.append(
            f"Claude CLI not found ({CLAUDE_BIN!r}). Install it or set CLAUDE_BIN in .env."
        )
    return problems
