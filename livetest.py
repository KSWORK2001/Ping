"""Live regression tests for Ping - a real Discord round-trip harness.

This is a SECOND Discord bot (the "tester") that joins the same server/channel
as Ping, posts the exact commands a user would, reads Ping's replies back, and
checks them. Deterministic commands (ping/status/shell/screenshot) are asserted
directly; non-deterministic GUI tasks (message someone, post to a server) are
graded by a final Claude vision call against the goal.

Outward sends are real but TEST-TAGGED (default body prefix "[ping-livetest]")
and target a configurable person/channel so you control who gets pinged.

----------------------------------------------------------------------------
SETUP (one-time)
  1. Create a second bot application in the Discord Developer Portal, enable the
     MESSAGE CONTENT intent, and invite it to your server with permission to
     read/send messages + attachments in your command channel.
  2. In Ping's .env, authorize that tester bot's user id:
         TEST_AUTHOR_IDS=<tester-bot-user-id>
     (This bypasses Ping's "ignore other bots" rule AND grants it command auth.
      It's empty by default, so production behavior is unchanged.)
  3. Set the tester env vars (can live in the same .env):
         LIVETEST_TOKEN=<tester bot token>
         LIVETEST_CHANNEL_ID=<channel id to run tests in>
         LIVETEST_PING_BOT_ID=<Ping bot user id>      # optional; else any other bot
         LIVETEST_TARGET_PERSON=Karan Shrivastava     # who the Teams DM goes to
         LIVETEST_SERVER_CHAT=general                 # Discord channel name to post in
         LIVETEST_TAG=[ping-livetest]                 # marks test messages
         LIVETEST_ONLY=                               # optional: only these scenarios
         LIVETEST_GUI=1                               # 0 = skip GUI-messaging scenarios
  4. Start Ping (run.ps1 / python bot.py), then in another shell:  python livetest.py

Run a subset:  LIVETEST_ONLY=ping,status,screenshot python livetest.py
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass

import discord
from dotenv import load_dotenv
from PIL import Image

import config
import system

load_dotenv()

TOKEN = os.getenv("LIVETEST_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("LIVETEST_CHANNEL_ID", "0") or "0")
PING_BOT_ID = int(os.getenv("LIVETEST_PING_BOT_ID", "0") or "0")
TARGET_PERSON = os.getenv("LIVETEST_TARGET_PERSON", "Karan Shrivastava")
SERVER_CHAT = os.getenv("LIVETEST_SERVER_CHAT", "general")
TAG = os.getenv("LIVETEST_TAG", "[ping-livetest]")
ONLY = {s.strip() for s in os.getenv("LIVETEST_ONLY", "").split(",") if s.strip()}
GUI_ENABLED = os.getenv("LIVETEST_GUI", "1").lower() in ("1", "true", "yes", "on")

ARTIFACTS = os.path.join(config.WORKDIR, "tests_artifacts")

# How a non-terminal command's stream of replies is judged "finished".
QUIET_SECONDS = 5.0     # no new Ping message for this long => done
POLL_SECONDS = 2.0

# Terminal markers an agent / workflow run prints when it stops for any reason.
AGENT_DONE = r"Done after|complete|step limit|Stopping|Task stopped|Task error|step failed"


@dataclass
class Scenario:
    name: str
    command: str                      # exact text the tester posts (NL or !command)
    kind: str = "det"                 # "det" (deterministic) or "gui" (vision-judged)
    expect_text: str = None           # regex that must appear in Ping's replies
    expect_image: bool = False        # at least one image attachment must come back
    judge: str = None                 # vision yes/no question (gui scenarios)
    done: str = None                  # regex marking the run finished
    timeout: int = 60                 # hard cap (seconds)


def _scenarios():
    s = [
        Scenario("ping", "!ping", expect_text=r"pong", timeout=30),
        Scenario("status", "what's your status?", expect_text=r"status|CPU|uptime", timeout=40),
        Scenario("shell", "!sh echo ping-ok-7913",
                 expect_text=r"ping-ok-7913", timeout=40),
        Scenario("screenshot", "send me a screenshot",
                 expect_image=True, timeout=60),
        Scenario("open_teams", "open teams",
                 expect_text=r"Opened|Launched|Focused|teams", kind="gui",
                 judge="Is the Microsoft Teams app open and in the foreground?", timeout=90),
        Scenario("claude_code_cmd", "!claude reply with exactly one word: PONG",
                 expect_text=r"PONG|pong", timeout=config.CLAUDE_TIMEOUT),
    ]
    if GUI_ENABLED:
        s += [
            Scenario(
                "teams_message",
                f"open teams and send {TARGET_PERSON} the message: {TAG} hello",
                kind="gui", done=AGENT_DONE, timeout=240,
                judge=(f'Does the screen show a Microsoft Teams chat with "{TARGET_PERSON}" '
                       f'in which a message containing the word "hello" was just sent?'),
            ),
            Scenario(
                "discord_message",
                f"open discord and post this in the {SERVER_CHAT} channel: {TAG} hello",
                kind="gui", done=AGENT_DONE, timeout=240,
                judge=(f'Does Discord show a message containing the word "hello" posted '
                       f'in the {SERVER_CHAT} channel?'),
            ),
            Scenario(
                "claude_client",
                f"open the Claude desktop app and start a new chat that says: {TAG} say hi",
                kind="gui", done=AGENT_DONE, timeout=240,
                judge="Is the Claude desktop app open with a chat where a prompt was typed or sent?",
            ),
        ]
    if ONLY:
        s = [x for x in s if x.name in ONLY]
    return s


JUDGE_PROMPT = """You are grading a UI automation test from a screenshot.

The screenshot file is at: {path}
FIRST use your Read tool to open and look at that image.

PASS CRITERION: {question}

Answer with ONLY one JSON object, no prose, no markdown fences:
{{"pass": true or false, "reason": "<one short sentence of what you see>"}}"""


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


def _is_image(att):
    ct = (att.content_type or "").lower()
    return ct.startswith("image/") or (att.filename or "").lower().endswith((".png", ".jpg", ".jpeg"))


class Tester(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.inbox = asyncio.Queue()
        self.channel = None
        self._started = False
        self._counter = 0

    # --- collect Ping's messages in our channel ---
    async def on_message(self, message):
        if message.channel.id != CHANNEL_ID:
            return
        if message.author.id == self.user.id:
            return  # ignore our own posted commands
        if PING_BOT_ID and message.author.id != PING_BOT_ID:
            return  # not the bot under test
        await self.inbox.put(message)

    async def on_ready(self):
        if self._started:
            return
        self._started = True
        os.makedirs(ARTIFACTS, exist_ok=True)
        self.channel = self.get_channel(CHANNEL_ID)
        if self.channel is None:
            print(f"[livetest] channel {CHANNEL_ID} not found / not visible. Aborting.")
            await self.close()
            return
        print(f"[livetest] connected as {self.user}; running in #{self.channel}")
        try:
            await self._run_all()
        finally:
            await self.close()

    def _drain(self):
        while not self.inbox.empty():
            self.inbox.get_nowait()

    async def _save_image(self, att, label):
        self._counter += 1
        path = os.path.join(ARTIFACTS, f"{self._counter:02d}_{label}.png")
        await att.save(path)
        return path

    async def _collect(self, timeout, done_re):
        """Read Ping's replies until a terminal marker, a quiet gap, or timeout."""
        texts, images = [], []
        loop = asyncio.get_running_loop()
        start = loop.time()
        last = None
        while loop.time() - start < timeout:
            remaining = timeout - (loop.time() - start)
            try:
                msg = await asyncio.wait_for(self.inbox.get(), timeout=min(POLL_SECONDS, remaining))
            except asyncio.TimeoutError:
                if last is not None and (loop.time() - last) >= QUIET_SECONDS:
                    break
                continue
            if msg.content:
                texts.append(msg.content)
            images += [a for a in msg.attachments if _is_image(a)]
            last = loop.time()
            if done_re and msg.content and re.search(done_re, msg.content, re.I):
                break
        return texts, images

    async def _grab_screenshot(self, label):
        self._drain()
        await self.channel.send("!shot 1")
        _, images = await self._collect(timeout=45, done_re=None)
        return await self._save_image(images[-1], label) if images else None

    async def _judge(self, path, question):
        prompt = JUDGE_PROMPT.format(path=path, question=question)
        raw = await system.run_claude_vision(prompt)
        obj = _extract_json(raw) or {}
        return bool(obj.get("pass")), (obj.get("reason") or raw[:160] or "no verdict")

    async def _run_one(self, s):
        print(f"\n[livetest] -> {s.name}: {s.command!r}")
        self._drain()
        await self.channel.send(s.command)
        texts, images = await self._collect(s.timeout, s.done)
        blob = "\n".join(texts)

        notes, passed = [], True
        if s.expect_text and not re.search(s.expect_text, blob, re.I):
            passed = False
            notes.append(f"missing text /{s.expect_text}/")
        if s.expect_image:
            if not images:
                passed = False
                notes.append("no image returned")
            else:
                path = await self._save_image(images[-1], s.name)
                if not _valid_png(path):
                    passed = False
                    notes.append("returned image is not a valid PNG")
        if s.judge:
            # Prefer the agent's final posted screenshot; otherwise pull a fresh one.
            path = await self._save_image(images[-1], s.name) if images else await self._grab_screenshot(s.name)
            if not path:
                passed = False
                notes.append("no screenshot available to judge")
            else:
                ok, reason = await self._judge(path, s.judge)
                notes.append(f"vision: {reason}")
                passed = passed and ok
        if not texts and not images:
            passed = False
            notes.append("no reply from Ping (is it running? is the tester id in TEST_AUTHOR_IDS?)")

        print(f"[livetest] {'PASS' if passed else 'FAIL'} {s.name}: {'; '.join(notes) or 'ok'}")
        return passed, notes

    async def _run_all(self):
        scenarios = _scenarios()
        if not scenarios:
            print("[livetest] no scenarios selected (check LIVETEST_ONLY).")
            return
        results = []
        for s in scenarios:
            try:
                ok, notes = await self._run_one(s)
            except Exception as e:
                ok, notes = False, [f"harness error: {e}"]
                print(f"[livetest] ERROR {s.name}: {e}")
            results.append((s.name, ok, notes))

        passed = sum(1 for _, ok, _ in results if ok)
        lines = [f"{'PASS' if ok else 'FAIL'}  {name}  - {'; '.join(n) or 'ok'}"
                 for name, ok, n in results]
        summary = (f"**Ping live tests: {passed}/{len(results)} passed**\n" + "\n".join(lines))
        print("\n" + "=" * 60 + f"\n{passed}/{len(results)} passed\n" + "=" * 60)
        for line in lines:
            print(line)
        try:
            await self.channel.send(summary[:1900])
        except Exception:
            pass


def _valid_png(path):
    try:
        with Image.open(path) as im:
            im.verify()  # guarantees a structurally valid image
        return os.path.getsize(path) > 100  # guard against 0/1-byte stubs
    except Exception:
        return False


def main():
    problems = []
    if not TOKEN:
        problems.append("LIVETEST_TOKEN is empty (the tester bot's token).")
    if not CHANNEL_ID:
        problems.append("LIVETEST_CHANNEL_ID is empty (channel to run tests in).")
    if problems:
        for p in problems:
            print(f"[livetest] {p}")
        print("[livetest] See the setup notes at the top of livetest.py.")
        return
    # Vision subprocesses need the Proactor loop on Windows, same as bot.py.
    if os.name == "nt":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass
    Tester().run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
