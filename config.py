"""Configuration loaded from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


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
CLAUDE_BIN = os.getenv("CLAUDE_BIN", r"C:\Users\karan\.local\bin\claude.exe")
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
# Skip MCP servers on internal router/vision calls (faster, can't hang on MCP).
CLAUDE_NO_MCP = os.getenv("CLAUDE_NO_MCP", "true").lower() in ("1", "true", "yes", "on")
WORKDIR = os.getenv("WORKDIR", os.getcwd())
# SQLite store for saved workflows (recorded successful agent runs + run history).
WORKFLOW_DB = os.getenv("WORKFLOW_DB", os.path.join(WORKDIR, "ping.db"))
DISCORD_UPDATE_EXE = os.getenv(
    "DISCORD_UPDATE_EXE",
    r"C:\Users\karan\AppData\Local\Discord\Update.exe",
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
    return problems
