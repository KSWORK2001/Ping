"""Natural-language brain: turn a plain-English message into one action.

Uses the Claude CLI (fast model) purely as an intent router. It returns a
single JSON action that bot.py then executes with its existing handlers, so
control stays in Python and actions are auditable.
"""
import json
import re

import config
import system
from applog import logger

INSTRUCTIONS = """You are the intent router for "Ping", an agent that controls the user's Windows PC through Discord.
Read the user's message and reply with ONLY one JSON object (no markdown fences, no prose) for the single best action.

Allowed actions:
{"action":"screenshot"}                       - capture and send the screen
{"action":"status"}                           - report CPU/RAM/active window
{"action":"shell","command":"<powershell>"}   - run a PowerShell command and return output
{"action":"open_app","name":"<app>"}          - launch an app (teams, outlook, claude, cowork, chrome, ...)
{"action":"focus_app","name":"<app>"}         - bring an app window to the foreground
{"action":"type","text":"<text>"}             - type text on the keyboard
{"action":"key","combo":"<ctrl+c>"}           - press a hotkey combo
{"action":"agent_task","task":"<the user's request, verbatim>"} - multi-step GUI task: a separate vision agent watches the screen and acts step by step
{"action":"run_workflow","name":"<exact saved name>"} - replay an ALREADY-SAVED workflow (see SAVED WORKFLOWS); only when the request clearly matches one
{"action":"claude_task","prompt":"<task>"}    - delegate code/file/terminal work on this PC to Claude Code
{"action":"reply","text":"<answer>"}          - just answer the user in chat; no PC action
{workflows_block}
Rules:
- If the request clearly matches a SAVED WORKFLOW by name or intent (e.g. user says "do my standup" and a "standup" workflow exists) -> run_workflow with that exact name. If there is no clear match, do NOT invent one.
- Multi-step things that involve using an app's UI (e.g. "open Outlook and reply to the latest email", "message my manager", "fill this form") -> agent_task. Set "task" to the user's request AS-IS. Do NOT name, guess, or add an app the user didn't mention - the vision agent decides the app by looking at the screen.
- Code, files, terminal, or project work (e.g. "fix the bug", "summarize the repo", "run the tests") -> claude_task.
- One quick desktop control (open/focus an app, a single screenshot, one keystroke, one shell command) -> the specific single action.
- A question or chit-chat -> reply.
- Output JSON only."""


def _extract_json(text):
    if not text:
        return None
    # Strip code fences if present, then grab the first {...} block.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = m.group(0) if m else None
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _workflows_block(names):
    if not names:
        return "SAVED WORKFLOWS: (none)"
    listed = "\n".join(f"  - {n}" for n in names)
    return f"SAVED WORKFLOWS (replayable by exact name via run_workflow):\n{listed}"


async def decide(message, workflow_names=None):
    instructions = INSTRUCTIONS.replace("{workflows_block}", _workflows_block(workflow_names))
    prompt = f"{instructions}\n\nUser message: {message}\n\nJSON:"
    raw = await system.run_claude_router(prompt)
    if not raw.strip():
        logger.warning("brain: router empty, retrying once")
        raw = await system.run_claude_router(prompt)
    action = _extract_json(raw)
    if not action or "action" not in action:
        if not raw.strip():
            logger.warning("brain: router returned EMPTY for %r -> claude_task fallback", message[:80])
        else:
            logger.warning("brain: unparseable router output %r", raw[:200])
        # Fall back to treating the whole thing as a Claude Code task.
        return {"action": "claude_task", "prompt": message}
    # The router is text-only and must not pick an app; let the vision loop (which
    # sees the screen) infer it. Always drive the GUI task from the raw request.
    if action.get("action") == "agent_task":
        action["task"] = message
    logger.info("brain: %r -> %s", message[:80], action)
    return action
