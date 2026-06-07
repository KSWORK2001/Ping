"""System actions: shell, Claude Code, status."""
import asyncio
import os
import platform
import time

import psutil

import config
from applog import logger

START = time.time()

# Strip the outer Claude Code session's identity from the child env (defensive;
# proven harmless, prevents any future nested-session behavior change).
_STRIP_ENV = (
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_TMPDIR", "CLAUDE_CODE_EXECPATH", "AI_AGENT", "CLAUDE_EFFORT",
)


def _child_env():
    return {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}


async def _exec(args, timeout, label, cwd=None):
    """Run a subprocess, return (rc, stdout, stderr). Always closes stdin so a
    child (e.g. the claude CLI) never blocks waiting on inherited stdin. Logs
    args, timing, exit code, and stderr/stdout lengths for diagnosis."""
    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or config.WORKDIR,
            env=_child_env(),
        )
    except NotImplementedError as e:
        logger.error("%s: subprocess unsupported on this event loop: %s", label, e)
        return -1, "", f"event-loop-unsupported: {e}"
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        logger.warning("%s: TIMED OUT after %ss", label, timeout)
        return -2, "", f"[timed out after {timeout}s]"
    out = out_b.decode("utf-8", errors="replace").strip()
    err = err_b.decode("utf-8", errors="replace").strip()
    dt = time.time() - t0
    logger.info("%s: rc=%s %.1fs out=%dB err=%dB", label, proc.returncode, dt, len(out), len(err))
    if not out:
        logger.warning("%s: EMPTY stdout. stderr=%r", label, err[:1000])
    return proc.returncode, out, err


async def run_shell(command, timeout=None):
    rc, out, err = await _exec(
        ["powershell", "-NoProfile", "-Command", command],
        timeout or config.SHELL_TIMEOUT, "shell",
    )
    return out or err or "[no output]"


async def run_claude(prompt, timeout=None):
    args = [config.CLAUDE_BIN, *config.CLAUDE_EXTRA_ARGS, "-p", prompt]
    rc, out, err = await _exec(args, timeout or config.CLAUDE_TIMEOUT, "claude_task")
    if out:
        return out
    return f"[claude returned nothing] rc={rc}; {err[:500] or 'no stderr'}"


# Skip all MCP servers for internal inference calls: they're not needed for
# routing/vision and their cold-start connection can hang past our timeouts.
_NO_MCP = ["--strict-mcp-config"] if config.CLAUDE_NO_MCP else []


async def run_claude_router(prompt, model=None, timeout=None):
    """Fast, plain Claude call used to interpret intent. Returns stdout (may be empty)."""
    args = [config.CLAUDE_BIN, *_NO_MCP, "--model", model or config.ROUTER_MODEL, "-p", prompt]
    _, out, _ = await _exec(args, timeout or config.ROUTER_TIMEOUT, "router")
    return out


async def run_claude_vision(prompt, model=None, timeout=None):
    """Vision call: Claude may Read the referenced screenshot file to see the screen.
    Retries once on empty output (transient cold starts / MCP load) before giving up."""
    args = [
        config.CLAUDE_BIN,
        *_NO_MCP,
        "--model", model or config.AGENT_MODEL,
        "--allowedTools", "Read",
        "-p", prompt,
    ]
    t = timeout or config.AGENT_STEP_TIMEOUT
    rc, out, err = await _exec(args, t, "vision")
    if not out:
        logger.warning("vision: empty, retrying once")
        rc, out, err = await _exec(args, t, "vision-retry")
    if out:
        return out
    # Empty: return an informative marker so the loop reports timeout vs nothing.
    return f"[timed out after {t}s]" if rc == -2 else f"[no output] {err[:200]}"


def list_windows(limit=25):
    """Visible window titles (deduped). Lets the agent know what's open."""
    try:
        import pygetwindow as gw
        seen, out = set(), []
        for w in gw.getAllWindows():
            t = (w.title or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def active_window_title():
    try:
        import pygetwindow as gw
        a = gw.getActiveWindow()
        return a.title if a and a.title else ""
    except Exception:
        return ""


def status():
    up = int(time.time() - START)
    h, rem = divmod(up, 3600)
    m, s = divmod(rem, 60)
    cpu = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    try:
        import pygetwindow as gw
        active = gw.getActiveWindow()
        win = active.title if active and active.title else "(none)"
    except Exception:
        win = "(unknown)"
    return (
        "**Ping status**\n"
        f"Host: {platform.node()} ({platform.system()} {platform.release()})\n"
        f"Bot uptime: {h}h {m}m {s}s\n"
        f"CPU: {cpu}%  |  RAM: {mem.percent}% "
        f"({mem.used // (1024**2)}/{mem.total // (1024**2)} MB)\n"
        f"Active window: {win}"
    )
