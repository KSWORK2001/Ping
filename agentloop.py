"""Vision-guided agent loop.

For multi-step desktop tasks, Claude gets a fresh screenshot after every step
and decides the single next action. This is a perceive -> decide -> act ->
re-perceive cycle, so Claude always reacts to the *actual* state of the screen
rather than guessing all steps in advance.
"""
import json
import os
import re

import discord
import mss
from PIL import Image, ImageDraw

import config
import system
import automation
import apps
import shortcuts
import elements
import screen
from applog import logger

CHEAT = shortcuts.render()


def _format_elements(els):
    if not els:
        return "(none detected - fall back to keyboard or grid click)"
    return "\n".join(f'[{i + 1}] {e["type"]} "{e["name"]}"' for i, e in enumerate(els))


def _to_physical(x_disp, y_disp, sx, sy, off_x, off_y):
    """Convert a model coordinate (display space, relative to the captured
    monitor) into an ABSOLUTE virtual-desktop physical pixel that pyautogui's
    SetCursorPos expects. Undoes the downscale (sx/sy) AND adds the captured
    monitor's origin (off_x/off_y) so multi-monitor layouts land correctly."""
    return round(x_disp * sx) + off_x, round(y_disp * sy) + off_y


def _draw_marks(path, els, sx, sy, off_x=0, off_y=0):
    """Overlay each element's id number at its position (Set-of-Marks).

    Element cx/cy are absolute virtual-desktop pixels, but the image is the
    downscaled, monitor-relative capture - so subtract the monitor origin before
    scaling down, or the marks drift on multi-monitor setups."""
    if not els:
        return
    img = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(img)
    for i, e in enumerate(els):
        x, y = int((e["cx"] - off_x) / sx), int((e["cy"] - off_y) / sy)
        label = str(i + 1)
        w = 7 * len(label) + 3
        d.rectangle([x - 1, y - 7, x - 1 + w, y + 7], fill=(200, 0, 200))
        d.text((x + 1, y - 6), label, fill=(255, 255, 255))
    img.save(path)

PROMPT = """You are guiding a Windows PC step by step by looking at screenshots. You control mouse and keyboard.

TASK: {task}

OPEN WINDOWS RIGHT NOW: {windows}
ACTIVE WINDOW: {active}

The CURRENT screen is the image file at: {path}
FIRST, use your Read tool to open that image and look at it. It reflects the result of all previous actions.

The image is {w} x {h} pixels with a RED coordinate grid drawn on it: red lines every {grid}px, each labeled with its x (top) or y (left) value. Use the grid to read off accurate coordinates. Origin (0,0) is top-left; all coordinates MUST be within {w} x {h}.

A WHITE arrow with a black outline is drawn at the CURRENT mouse pointer position; its tip is the exact pixel the pointer is on. After a click/move, check the arrow's tip against where you intended to click: if it overshot or undershot the target, correct your next click accordingly.

TARGET APP & FOCUS - the single most important rule:
- Your keystrokes and clicks go ONLY to the ACTIVE window, which right now is: {active}
- Choose the target app from the task. If the task does not name an app, INFER it: PREFER the app of the ACTIVE window if it fits the task; otherwise pick the best fit from OPEN WINDOWS. Example: "the chat with my manager" while Teams is the ACTIVE window => Teams (NOT Discord). State the app you chose in "thought".
- BEFORE typing or clicking, the ACTIVE window MUST already be the target app. If ACTIVE is not the target app, your ONLY action this step is to focus it:
    * target is in OPEN WINDOWS  -> {{"action":{{"type":"focus_app","name":"<app>"}}}}
    * target is NOT open         -> {{"action":{{"type":"open_app","name":"<app>"}}}}
  Then STOP and wait for the next screenshot; only proceed once ACTIVE shows the target app.
- NEVER send keystrokes while the ACTIVE window is the wrong app (e.g. never type into VS Code / a terminal / Claude Code when your target is Teams).

HOW TO ACT (in priority order):
1. FOCUS first (rule above): make the ACTIVE window the target app before anything else.
2. CLICK PRECISELY by element: to click a specific thing (a chat/person row, a button, a menu item, a search result, a tab), find it in the CLICKABLE ELEMENTS list below and use {{"type":"click_element","id":N}}. This clicks the EXACT center of that real control - always prefer it over raw x,y. The same id numbers are drawn in magenta on the screenshot.
3. TYPE / SHORTCUTS: use type for text; use key for app shortcuts. Handy shortcuts:
{shortcuts}
4. RAW CLICK (last resort): only if the target is NOT in the elements list and no keyboard path exists, use click x,y and read the red grid to hit the CENTER.

CLICKABLE ELEMENTS (real on-screen controls from the accessibility tree - click by id):
{elements}

Steps done so far:
{history}

Decide the SINGLE next action to move the task forward. Reply with ONLY one JSON object, no prose, no markdown fences.
Next-action shape:
  {{"thought":"<one short sentence>","action":{{"type":"<type>", ...}}}}
Action types and fields:
  click_element (PREFERRED for clicking) : {{"type":"click_element","id":N}}   (N from the CLICKABLE ELEMENTS list)
  click / double_click / right_click : {{"type":"click","x":N,"y":N}}   (or give a bounding box and we click its exact center: {{"type":"click","box":[x1,y1,x2,y2]}})
  move                               : {{"type":"move","x":N,"y":N}}
  scroll                             : {{"type":"scroll","amount":-500}}   (negative = down)
  type                               : {{"type":"type","text":"hello"}}
  key                                : {{"type":"key","combo":"ctrl+l"}}
  open_app                           : {{"type":"open_app","name":"outlook"}}
  focus_app                          : {{"type":"focus_app","name":"teams"}}
  wait                               : {{"type":"wait","seconds":2}}
When the TASK is fully complete, reply instead with:
  {{"thought":"<why it's done>","done":true,"summary":"<what was accomplished>"}}
JSON only."""


def _extract_json(text):
    if not text:
        return None
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


def _capture_primary(path):
    """Full-resolution screenshot of the primary monitor. Returns (w, h)."""
    with mss.MSS() as sct:
        mons = sct.monitors
        mon = mons[1] if len(mons) > 1 else mons[0]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        img.save(path, format="PNG")
        return img.width, img.height


def _capture_for_agent(path):
    """Capture the primary monitor, downscale to AGENT_IMAGE_WIDTH, and draw a
    labeled coordinate grid to help Claude target clicks.

    Returns (disp_w, disp_h, scale_x, scale_y, off_x, off_y): scale_* undo the
    downscale and off_* are the captured monitor's virtual-desktop origin. Feed
    all six to _to_physical() to turn a model coordinate into an absolute click.
    """
    with mss.MSS() as sct:
        mons = sct.monitors
        mon = mons[1] if len(mons) > 1 else mons[0]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    off_x, off_y = mon["left"], mon["top"]

    phys_w, phys_h = img.size
    disp_w = min(config.AGENT_IMAGE_WIDTH, phys_w)
    disp_h = round(phys_h * disp_w / phys_w)
    small = img.resize((disp_w, disp_h), Image.LANCZOS).convert("RGB")

    draw = ImageDraw.Draw(small)
    step = max(40, config.AGENT_GRID)
    line = (255, 0, 0)
    for x in range(0, disp_w, step):
        draw.line([(x, 0), (x, disp_h)], fill=line, width=1)
        draw.text((x + 2, 2), str(x), fill=line)
    for y in range(0, disp_h, step):
        draw.line([(0, y), (disp_w, y)], fill=line, width=1)
        draw.text((2, y + 2), str(y), fill=line)

    # Overlay the live mouse pointer so Claude can see where its last click
    # actually landed (overshoot/undershoot) and correct on the next step.
    pos = screen._cursor_pos()
    if pos is not None:
        cx = (pos[0] - mon["left"]) * disp_w / phys_w
        cy = (pos[1] - mon["top"]) * disp_h / phys_h
        if -32 <= cx <= disp_w + 32 and -32 <= cy <= disp_h + 32:
            screen._draw_cursor(small, cx, cy)

    small.save(path, format="PNG")
    return disp_w, disp_h, phys_w / disp_w, phys_h / disp_h, off_x, off_y


def save_click_overlay(phys_x, phys_y, label, path):
    """Capture the FULL virtual desktop and draw a marker at the absolute physical
    point (phys_x, phys_y). This is the ground-truth check: the green crosshair is
    exactly where the next click will land, so you can eyeball it against the real
    target (and the real mouse cursor)."""
    with mss.MSS() as sct:
        vmon = sct.monitors[0]  # union rectangle of all monitors
        shot = sct.grab(vmon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    x, y = phys_x - vmon["left"], phys_y - vmon["top"]  # into the image's frame
    d = ImageDraw.Draw(img)
    r = 28
    d.ellipse([x - r, y - r, x + r, y + r], outline=(0, 255, 0), width=4)
    d.line([(x - r - 14, y), (x + r + 14, y)], fill=(0, 255, 0), width=2)
    d.line([(x, y - r - 14), (x, y + r + 14)], fill=(0, 255, 0), width=2)
    d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 0, 0))
    d.text((x + r + 8, y - 8), label, fill=(0, 255, 0))
    if img.width > 1920:  # keep the upload small
        ratio = 1920 / img.width
        img = img.resize((1920, int(img.height * ratio)))
    img.save(path, format="PNG")
    return path


def coord_selfcheck(dpi_mode="unknown"):
    """Startup sanity check for the click coordinate path. NO clicking - it only
    captures, runs _to_physical round-trips, and compares mss's physical capture
    size against the OS-reported screen metrics to catch a DPI-awareness mismatch
    (the classic cause of proportional click overshoot). Returns a report dict and
    appends human-readable warnings; never raises."""
    import ctypes
    report = {"dpi_mode": dpi_mode, "ok": True, "warnings": []}
    try:
        tmp = os.path.join(config.WORKDIR, "shots", "selfcheck.png")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        disp_w, disp_h, sx, sy, ox, oy = _capture_for_agent(tmp)
        phys_w, phys_h = round(disp_w * sx), round(disp_h * sy)
        report.update({
            "display": [disp_w, disp_h], "scale": [round(sx, 4), round(sy, 4)],
            "offset": [ox, oy], "captured_physical": [phys_w, phys_h],
        })

        # 1) _to_physical round-trip: map display -> physical -> back, expect ~identity.
        max_err = 0.0
        for xd, yd in [(0, 0), (disp_w // 2, disp_h // 2), (disp_w - 1, disp_h - 1)]:
            px, py = _to_physical(xd, yd, sx, sy, ox, oy)
            bx, by = (px - ox) / sx, (py - oy) / sy  # inverse transform
            max_err = max(max_err, abs(bx - xd), abs(by - yd))
        report["roundtrip_px_error"] = round(max_err, 3)
        if max_err > 2.0:
            report["ok"] = False
            report["warnings"].append(f"coordinate round-trip error {max_err:.1f}px (>2)")

        # 2) mss physical size vs OS-reported PRIMARY screen size. With effective
        # per-monitor/system DPI awareness these match; if the process is being DPI
        # virtualized, GetSystemMetrics returns LOGICAL (scaled-down) pixels while
        # mss returns physical -> the exact ratio clicks would overshoot by.
        try:
            u = ctypes.windll.user32
            os_w = u.GetSystemMetrics(0)  # SM_CXSCREEN (primary, process coord space)
            os_h = u.GetSystemMetrics(1)  # SM_CYSCREEN
            report["os_primary"] = [os_w, os_h]
            if ox == 0 and oy == 0 and os_w and phys_w:  # captured monitor IS primary
                ratio = phys_w / os_w
                report["physical_vs_os_ratio"] = round(ratio, 4)
                if abs(ratio - 1.0) > 0.02:
                    report["ok"] = False
                    report["warnings"].append(
                        f"capture is {phys_w}px wide but OS reports {os_w}px (ratio {ratio:.2f}); "
                        f"clicks likely overshoot ~{ratio:.2f}x - DPI awareness not effective")
        except Exception as e:
            report["warnings"].append(f"OS metric check skipped: {e}")

        # 3) DPI mode advisory.
        if dpi_mode in ("FAILED", "none"):
            report["ok"] = False
            report["warnings"].append(f"DPI awareness is {dpi_mode!r} - set per-monitor before clicking")
        elif dpi_mode == "system":
            report["warnings"].append("DPI awareness is system-level only; multi-monitor mixed-DPI clicks may drift")
    except Exception as e:
        report["ok"] = False
        report["warnings"].append(f"selfcheck failed: {e}")
    return report


def debug_aim(x_disp, y_disp):
    """For the !aim command: take a model-space (display) coordinate, run it
    through the exact same transform a real click uses, move the mouse there, and
    return an annotated overlay + the transform details for verification."""
    shots_dir = os.path.join(config.WORKDIR, "shots")
    os.makedirs(shots_dir, exist_ok=True)
    cap = os.path.join(shots_dir, "aim_capture.png")
    disp_w, disp_h, sx, sy, ox, oy = _capture_for_agent(cap)
    px, py = _to_physical(x_disp, y_disp, sx, sy, ox, oy)
    automation.move(px, py)  # move only (no click) so it's safe to test
    overlay = os.path.join(shots_dir, "aim_overlay.png")
    save_click_overlay(px, py, f"({x_disp},{y_disp})->({px},{py})", overlay)
    info = (f"display=({x_disp},{y_disp}) in {disp_w}x{disp_h} | "
            f"scale=({sx:.3f},{sy:.3f}) offset=({ox},{oy}) -> physical=({px},{py})")
    return overlay, px, py, info


def _execute(action):
    t = (action.get("type") or "").lower()
    try:
        if t in ("click",):
            return automation.click(action["x"], action["y"])
        if t == "double_click":
            return automation.double_click(action["x"], action["y"])
        if t == "right_click":
            return automation.right_click(action["x"], action["y"])
        if t == "move":
            return automation.move(action["x"], action["y"])
        if t == "scroll":
            return automation.scroll(action.get("amount", -500),
                                     action.get("x"), action.get("y"))
        if t == "type":
            return automation.type_text(action.get("text", ""))
        if t == "key":
            return automation.press(action.get("combo", ""))
        if t == "open_app":
            return apps.launch(action.get("name", ""))
        if t == "focus_app":
            return apps.focus(action.get("name", ""))
        if t == "wait":
            return f"waited {action.get('seconds', 1)}s"
        return f"unknown action type: {t}"
    except KeyError as e:
        return f"missing field {e} for {t}"
    except Exception as e:
        return f"error: {e}"


async def _next_action(task, history, path, w, h, windows, active, elem_text):
    hist = "\n".join(
        f"{x['step']}. {json.dumps(x['action'])} -> {x['result']}" for x in history
    ) or "(none yet)"
    prompt = PROMPT.format(
        task=task, path=path, w=w, h=h, grid=config.AGENT_GRID, history=hist,
        windows=", ".join(windows) or "(unknown)", active=active or "(unknown)",
        shortcuts=CHEAT, elements=elem_text,
    )
    raw = await system.run_claude_vision(prompt)
    decision = _extract_json(raw)
    if not decision:
        # Empty / unparseable output is an ERROR, never task completion.
        return {"error": "no parseable action from the model",
                "raw": (raw or "")[:400]}
    return decision


async def run(channel, task, post_image, max_steps=None, seed_history=None, announce=True):
    """Run the vision loop. `post_image(channel)` posts a screenshot to the user.

    Returns {"completed": bool, "summary": str, "history": [...]} so a caller can
    save a successful run as a reusable workflow. `seed_history` lets a replay
    fallback tell the model which steps already ran before vision took over.
    """
    import asyncio
    max_steps = max_steps or config.AGENT_MAX_STEPS
    shots_dir = os.path.join(config.WORKDIR, "shots")
    os.makedirs(shots_dir, exist_ok=True)
    path = os.path.join(shots_dir, "agent_step.png")
    history = list(seed_history or [])

    if announce:
        await channel.send(f"Starting task (up to {max_steps} steps): **{task}**\nSend `stop` to abort.")
    for step in range(1, max_steps + 1):
        # Maximize the target window FIRST, before capturing, so the layout the
        # model sees (and computes click coordinates from) matches what we click.
        if config.AGENT_MAXIMIZE_ACTIVE:
            await asyncio.to_thread(apps.maximize_active)
            await asyncio.sleep(0.3)  # let the window finish maximizing
        disp_w, disp_h, sx, sy, off_x, off_y = await asyncio.to_thread(_capture_for_agent, path)
        windows = await asyncio.to_thread(system.list_windows)
        active = await asyncio.to_thread(system.active_window_title)
        if config.AGENT_USE_UIA:
            _, els = await asyncio.to_thread(elements.enumerate_active, config.AGENT_MAX_ELEMENTS)
            await asyncio.to_thread(_draw_marks, path, els, sx, sy, off_x, off_y)
        else:
            els = []
        logger.info("step %d: active=%r elements=%d", step, active[:40], len(els))

        decision = await _next_action(task, history, path, disp_w, disp_h,
                                      windows, active, _format_elements(els))
        thought = decision.get("thought", "")
        logger.info("step %d decision: %s", step, json.dumps(decision)[:300])

        if decision.get("error"):
            detail = decision.get("raw") or ""
            await channel.send(
                f"Vision step failed: {decision['error']} {detail} "
                f"(no action taken; check ping_debug.log). Stopping."
            )
            return {"completed": False, "summary": decision["error"], "history": history}

        if decision.get("done"):
            summary = decision.get("summary", thought)
            await channel.send(f"Done after {step - 1} steps: {summary}")
            return {"completed": True, "summary": summary, "history": history}

        action = decision.get("action") or {}
        atype = (action.get("type") or "").lower()
        element_meta = None  # set when we click a named accessibility element
        click_pt = None      # absolute physical point we acted on (for debug overlay)

        if atype == "click_element":
            idx = action.get("id")
            el = els[idx - 1] if isinstance(idx, int) and 1 <= idx <= len(els) else None
            if el is None:
                await channel.send(f"**Step {step}** - {thought}\n(invalid element id {idx})")
                result = f"invalid element id {idx}"
            else:
                element_meta = {"name": el["name"], "type": el["type"]}
                click_pt = (el["cx"], el["cy"])  # already absolute physical
                await channel.send(f'**Step {step}** - {thought}\nclick [{idx}] {el["type"]} "{el["name"]}"')
                result = await asyncio.to_thread(automation.click, el["cx"], el["cy"])
        else:
            # Convert display-space coordinates to absolute physical pixels.
            exec_action = dict(action)
            box = action.get("box")
            if isinstance(box, (list, tuple)) and len(box) == 4:
                # Model returned a bounding box -> click its center.
                xd = (float(box[0]) + float(box[2])) / 2
                yd = (float(box[1]) + float(box[3])) / 2
                exec_action.pop("box", None)
                exec_action["x"], exec_action["y"] = _to_physical(xd, yd, sx, sy, off_x, off_y)
            elif "x" in exec_action and "y" in exec_action:
                exec_action["x"], exec_action["y"] = _to_physical(
                    exec_action["x"], exec_action["y"], sx, sy, off_x, off_y)
            else:  # a stray single axis (rare)
                if "x" in exec_action:
                    exec_action["x"] = round(exec_action["x"] * sx) + off_x
                if "y" in exec_action:
                    exec_action["y"] = round(exec_action["y"] * sy) + off_y
            if "x" in exec_action and "y" in exec_action:
                click_pt = (exec_action["x"], exec_action["y"])
            await channel.send(f"**Step {step}** - {thought}\n`{json.dumps(action)}`")
            result = await asyncio.to_thread(_execute, exec_action)

        if click_pt:
            logger.info("step %d click target (physical) = %s", step, click_pt)
            if config.AGENT_DEBUG_CLICKS:
                try:
                    dbg = os.path.join(shots_dir, f"click_debug_{step}.png")
                    await asyncio.to_thread(save_click_overlay, click_pt[0], click_pt[1],
                                            f"step {step} target", dbg)
                    await channel.send(file=discord.File(dbg, filename=f"click_{step}.png"))
                except Exception as e:
                    logger.warning("click overlay failed: %s", e)
        logger.info("step %d acted: %s -> %s", step, json.dumps(action)[:200], result)
        history.append({
            "step": len(history) + 1, "action": action,
            "result": result, "element": element_meta,
        })
        await asyncio.sleep(config.AGENT_STEP_DELAY)
        await post_image(channel)  # so you can watch the result of the step

    await channel.send(f"Reached the {max_steps}-step limit. Send another instruction to continue.")
    return {"completed": False, "summary": "step limit reached", "history": history}


def _match_element(els, meta):
    """Re-locate a recorded element in the current accessibility tree by name/type.

    Element ids and pixel positions change between runs, so we match on the
    stable thing we stored: the control's name (and type when it disambiguates).
    """
    if not meta:
        return None
    name = (meta.get("name") or "").strip().lower()
    typ = (meta.get("type") or "").strip().lower()
    if not name:
        return None
    exact = [e for e in els
             if (e.get("name") or "").strip().lower() == name
             and (e.get("type") or "").strip().lower() == typ]
    if exact:
        return exact[0]
    same = [e for e in els if (e.get("name") or "").strip().lower() == name]
    if same:
        return same[0]
    part = [e for e in els if name in (e.get("name") or "").strip().lower()]
    return part[0] if part else None


async def _fallback(channel, goal, post_image, done_history):
    """Recorded replay couldn't continue - finish the goal with live vision,
    telling the model which steps already ran."""
    await channel.send("Falling back to live vision to finish the task...")
    res = await run(channel, goal, post_image, seed_history=done_history, announce=False)
    res["mode"] = "fallback"
    return res


async def replay(channel, workflow, post_image):
    """Replay a saved workflow's recorded steps deterministically; the moment a
    step can't be resolved, hand off to the live vision loop. Returns the same
    dict shape as run() plus "mode": "replay" | "fallback"."""
    import asyncio
    name, goal, steps = workflow["name"], workflow["goal"], workflow["steps"]
    shots_dir = os.path.join(config.WORKDIR, "shots")
    os.makedirs(shots_dir, exist_ok=True)
    path = os.path.join(shots_dir, "agent_step.png")
    done = []

    if not steps:
        await channel.send(f"**{name}** has no recorded steps; running it live instead.")
        res = await run(channel, goal, post_image)
        res["mode"] = "fallback"
        return res

    await channel.send(
        f"Replaying workflow **{name}** ({len(steps)} steps): {goal}\nSend `stop` to abort."
    )
    for i, st in enumerate(steps, 1):
        action = st["action"]
        atype = (action.get("type") or "").lower()
        meta = st.get("element")
        # Maximize first (same reason as the live loop) so recorded coords + element
        # relocation resolve against the same large layout they were captured on.
        if config.AGENT_MAXIMIZE_ACTIVE:
            await asyncio.to_thread(apps.maximize_active)
            await asyncio.sleep(0.3)
        # Re-capture each step so element relocation + coord scaling use the live screen.
        _, _, sx, sy, off_x, off_y = await asyncio.to_thread(_capture_for_agent, path)

        if atype == "click_element":
            _, els = await asyncio.to_thread(elements.enumerate_active, config.AGENT_MAX_ELEMENTS)
            el = _match_element(els, meta)
            if el is None:
                await channel.send(
                    f'**Step {i}/{len(steps)}** - control "{(meta or {}).get("name", "?")}" '
                    f"not on screen."
                )
                return await _fallback(channel, goal, post_image, done)
            await channel.send(f'**Step {i}/{len(steps)}** - click "{el["name"]}"')
            result = await asyncio.to_thread(automation.click, el["cx"], el["cy"])
        else:
            exec_action = dict(action)
            if "x" in exec_action and "y" in exec_action:
                exec_action["x"], exec_action["y"] = _to_physical(
                    exec_action["x"], exec_action["y"], sx, sy, off_x, off_y)
            else:
                if "x" in exec_action:
                    exec_action["x"] = round(exec_action["x"] * sx) + off_x
                if "y" in exec_action:
                    exec_action["y"] = round(exec_action["y"] * sy) + off_y
            await channel.send(f"**Step {i}/{len(steps)}** - `{json.dumps(action)}`")
            result = await asyncio.to_thread(_execute, exec_action)
            if isinstance(result, str) and result.startswith(("error:", "missing field", "unknown action")):
                await channel.send(f"Step failed: {result}")
                done.append({"step": len(done) + 1, "action": action, "result": result, "element": meta})
                return await _fallback(channel, goal, post_image, done)

        done.append({"step": len(done) + 1, "action": action, "result": result, "element": meta})
        await asyncio.sleep(config.AGENT_STEP_DELAY)
        await post_image(channel)

    await channel.send(f"Replay of **{name}** complete ({len(steps)} steps).")
    return {"completed": True, "summary": f"replayed {name}", "history": done, "mode": "replay"}
