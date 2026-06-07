"""In-process live regression tests, triggered from Discord with `!selftest`.

No second bot and no network round-trip: each scenario is driven through Ping's
REAL handlers (the same brain + dispatch + agent loop a user message hits), so
actual PC actions happen. Progress streams to the channel you ran it in, so you
watch it live from your phone.

- Deterministic scenarios (status/shell/screenshot/!claude) are asserted on what
  the handlers send back (reply text, or that an image attachment was posted).
- Non-deterministic GUI scenarios (DM someone in Teams, post in Discord, drive
  the Claude app) are graded by a SEPARATE Claude instance: after the task ends
  we grab a fresh screenshot and ask `system.run_claude_vision` a pass/fail
  question about the goal. That's the "different claude does the judging" idea.

Outward sends are real but tagged (SELFTEST_TAG, default "[ping-selftest]") and
go to a configurable person/channel so you control who gets pinged.

Usage from Discord:
    !selftest                 run all scenarios
    !selftest ping,status     run only these (comma list)
    !selftest nogui           run only the deterministic scenarios
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass

import config
import system
import agentloop

TARGET_PERSON = os.getenv("SELFTEST_TARGET_PERSON", "Karan Shrivastava")
SERVER_CHAT = os.getenv("SELFTEST_SERVER_CHAT", "general")
TAG = os.getenv("SELFTEST_TAG", "[ping-selftest]")
ARTIFACTS = os.path.join(config.WORKDIR, "tests_artifacts")


@dataclass
class Scenario:
    name: str
    action: dict = None        # drive via dispatch(action) - exact handler under test
    nl: str = None             # OR drive via the brain: decide(nl) -> dispatch(action)
    custom: object = None      # OR a coroutine fn(channel, dispatch, decide, art_path)
    expect_text: str = None    # regex that must appear in what Ping sent back
    expect_file: bool = False  # an image/file attachment must have been sent
    judge: str = None          # vision pass/fail question (GUI scenarios)
    timeout: int = 180         # safety cap; agent loops are already step-bounded


def _scenarios(only):
    det = [
        Scenario("status", action={"action": "status"},
                 expect_text=r"status|CPU|uptime"),
        Scenario("shell", action={"action": "shell", "command": "echo ping-ok-7913"},
                 expect_text=r"ping-ok-7913"),
        Scenario("screenshot", nl="send me a screenshot", expect_file=True),
        Scenario("claude_code_cmd",
                 action={"action": "claude_task",
                         "prompt": "Reply with exactly one word: PONG"},
                 expect_text=r"PONG", timeout=config.CLAUDE_TIMEOUT),
    ]
    gui = [
        Scenario("open_teams", action={"action": "open_app", "name": "teams"},
                 judge="Is the Microsoft Teams app open and visible on screen?",
                 timeout=60),
        Scenario(
            "teams_message",
            nl=f"open teams and send {TARGET_PERSON} the message: {TAG} hello",
            judge=(f'Does the screen show a Microsoft Teams chat with "{TARGET_PERSON}" '
                   f'where a message containing the word "hello" was just sent?'),
            timeout=300),
        Scenario(
            "discord_message",
            nl=f"open discord and post this in the {SERVER_CHAT} channel: {TAG} hello",
            judge=(f'Does Discord show a message containing the word "hello" posted '
                   f'in the {SERVER_CHAT} channel?'),
            timeout=300),
        Scenario(
            "claude_client",
            nl=f"open the Claude desktop app and start a new chat that says: {TAG} say hi",
            judge="Is the Claude desktop app open with a chat where a prompt was typed or sent?",
            timeout=300),
        Scenario("workflow_roundtrip", custom=_workflow_roundtrip, timeout=600),
    ]
    only = (only or "").strip().lower()
    if only == "nogui":
        chosen = det
    elif only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        chosen = [s for s in det + gui if s.name in wanted]
    else:
        chosen = det + gui
    return chosen


JUDGE_PROMPT = """You are grading a desktop UI automation test from a screenshot.

The screenshot file is at: {path}
FIRST use your Read tool to open and look at that image.

PASS CRITERION: {question}

Reply with ONLY one JSON object, no prose, no markdown fences:
{{"pass": true or false, "reason": "<one short sentence describing what you see>"}}"""


def _extract_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class _Tap:
    """Wraps the real channel: forwards every send (so the user sees it live)
    while recording reply text and whether any attachment was posted, so the
    runner can assert on what the handlers produced."""

    def __init__(self, channel):
        self._ch = channel
        self.id = channel.id
        self.texts = []
        self.files = 0

    async def send(self, content=None, *, file=None, **kwargs):
        if content is not None:
            self.texts.append(str(content))
        if file is not None:
            self.files += 1
        return await self._ch.send(content=content, file=file, **kwargs)

    def typing(self):
        return self._ch.typing()


async def _judge(path, question):
    prompt = JUDGE_PROMPT.format(path=path, question=question)
    raw = await system.run_claude_vision(prompt)
    obj = _extract_json(raw) or {}
    return bool(obj.get("pass")), (obj.get("reason") or (raw or "")[:160] or "no verdict")


async def _capture(path):
    await asyncio.to_thread(agentloop._capture_primary, path)


async def _workflow_roundtrip(channel, dispatch, decide, art_path):
    """Exercise the workflow store end to end: run a real GUI task, save its
    recorded run, replay it through the hybrid engine, and vision-judge the
    result. Uses Ping's actual save/replay code (bot._last_run, run_workflow)."""
    import workflows
    import bot  # safe at call time (bot fully imported); avoids an import cycle

    name = "selftest-roundtrip"
    task = f"open notepad and type this exact line: {TAG} wf-roundtrip"
    notes = []
    await asyncio.to_thread(workflows.delete, name)  # idempotent: clear a stale copy

    # 1) Record a live run of the task.
    await channel.send("recording a live run...")
    await dispatch(channel, {"action": "agent_task", "task": task})
    last = bot._last_run.get(channel.id)
    if not last or not last.get("history"):
        return False, ["live run recorded no steps (task didn't run)"]
    if not last.get("completed"):
        notes.append("live run didn't report completion")

    # 2) Save it as a workflow.
    await asyncio.to_thread(workflows.save, name, task, last["history"])
    wf = await asyncio.to_thread(workflows.get, name)
    if not wf or not wf["steps"]:
        return False, notes + ["save produced no steps"]
    notes.append(f"saved {len(wf['steps'])} steps")

    # 3) Replay it through the real hybrid replay path.
    await channel.send("replaying the saved workflow...")
    await bot.run_workflow(channel, name)

    # 4) The run must have been logged against the workflow.
    wf2 = await asyncio.to_thread(workflows.get, name)
    if (wf2 or {}).get("run_count", 0) < 1:
        await asyncio.to_thread(workflows.delete, name)
        return False, notes + ["replay was not logged in runs"]
    notes.append(f"replay logged ({wf2.get('last_status')})")

    # 5) Grade the on-screen result with a separate Claude instance.
    await _capture(art_path)
    ok, reason = await _judge(art_path, 'Does an open Notepad window show the text "wf-roundtrip"?')
    notes.append(f"vision: {reason}")

    await asyncio.to_thread(workflows.delete, name)  # leave the store clean
    return ok, notes


async def run(channel, dispatch, decide, only=""):
    """Run the self-test. `dispatch(channel, action)` and `decide(text, names)`
    are injected from bot.py so this module stays decoupled from it."""
    os.makedirs(ARTIFACTS, exist_ok=True)
    scenarios = _scenarios(only)
    if not scenarios:
        await channel.send("No matching scenarios. Try `!selftest`, `!selftest nogui`, or `!selftest status,shell`.")
        return

    await channel.send(
        f"**Running {len(scenarios)} self-tests.** Real actions will happen on this PC; "
        f"messaging tests are tagged `{TAG}`."
    )
    results = []
    for i, s in enumerate(scenarios, 1):
        await channel.send(f"--- **[{i}/{len(scenarios)}] {s.name}** ---")
        tap = _Tap(channel)
        notes, passed = [], True
        art = os.path.join(ARTIFACTS, f"{i:02d}_{s.name}.png")
        try:
            if s.custom is not None:
                # Multi-step scenario owns its own driving + assertions.
                passed, notes = await asyncio.wait_for(
                    s.custom(tap, dispatch, decide, art), timeout=s.timeout)
            else:
                if s.nl is not None:
                    action = await decide(s.nl, [])
                    await asyncio.wait_for(dispatch(tap, action), timeout=s.timeout)
                else:
                    await asyncio.wait_for(dispatch(tap, dict(s.action)), timeout=s.timeout)

                blob = "\n".join(tap.texts)
                if s.expect_text and not re.search(s.expect_text, blob, re.I):
                    passed = False
                    notes.append(f"missing /{s.expect_text}/ in reply")
                if s.expect_file and tap.files == 0:
                    passed = False
                    notes.append("no attachment was sent")
                if s.judge:
                    await _capture(art)
                    ok, reason = await _judge(art, s.judge)
                    notes.append(f"vision: {reason}")
                    passed = passed and ok
        except asyncio.TimeoutError:
            passed = False
            notes.append(f"timed out after {s.timeout}s")
        except Exception as e:
            passed = False
            notes.append(f"handler error: {e}")

        results.append((s.name, passed, notes))
        await channel.send(f"{'PASS ✅' if passed else 'FAIL ❌'} **{s.name}** — {'; '.join(notes) or 'ok'}")

    n_pass = sum(1 for _, ok, _ in results if ok)
    lines = [f"{'✅' if ok else '❌'} {name} — {'; '.join(n) or 'ok'}" for name, ok, n in results]
    await channel.send(f"**Self-test complete: {n_pass}/{len(results)} passed**\n" + "\n".join(lines))
    return results
