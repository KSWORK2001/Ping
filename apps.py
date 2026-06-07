"""Launch and focus desktop applications (Teams, Outlook, Claude, etc.)."""
import os
import shutil
import subprocess
import time

import pyautogui


def _local(*parts):
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), *parts)


# Preferred launch via URI/protocol handlers (most reliable for Store/Electron apps).
URI = {
    "teams": "msteams:",
    "outlook": "outlook:",
    "claude": "claude://",
}

# Fallback executable candidates, tried in order.
EXES = {
    "outlook": ["outlook.exe"],
    "teams": ["ms-teams.exe", "Teams.exe"],
    "claude": [
        _local("AnthropicClaude", "claude.exe"),
        _local("Programs", "claude", "Claude.exe"),
        _local("Claude", "Claude.exe"),
        "claude.exe",
    ],
    "notepad": ["notepad.exe"],
    "explorer": ["explorer.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
}

# Window-title substrings used by focus (case-insensitive). Maps spoken names
# to a substring that appears in the app's OS window title.
TITLES = {
    "teams": "teams",
    "microsoft teams": "teams",
    "outlook": "outlook",
    "claude": "claude",
    "discord": "discord",
    "chrome": "chrome",
    "google chrome": "chrome",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "visual studio code": "visual studio code",
    "explorer": "explorer",
    "file explorer": "explorer",
    "files": "explorer",
}


def _force_foreground(hwnd):
    """Bring a window to the foreground, working around Windows' foreground lock
    (a background process normally can't steal focus; the alt-key tap unlocks it)."""
    import ctypes
    u = ctypes.windll.user32
    u.ShowWindow(hwnd, 9)            # SW_RESTORE
    u.keybd_event(0x12, 0, 0, 0)     # ALT down
    u.keybd_event(0x12, 0, 2, 0)     # ALT up
    u.SetForegroundWindow(hwnd)
    try:
        u.BringWindowToTop(hwnd)
    except Exception:
        pass


def open_via_start(name):
    """Open an app by driving the Start menu: Win -> type name -> Enter.

    Reliable and click-free; assumes the app is installed and is the top search
    result for `name` (which is true for most installed apps).
    """
    pyautogui.press("esc")          # clear any open menu/search state
    time.sleep(0.2)
    pyautogui.press("winleft")      # open Start
    time.sleep(0.9)
    pyautogui.write(name, interval=0.03)
    time.sleep(1.1)                 # let search results populate
    pyautogui.press("enter")        # launch the top hit
    time.sleep(0.5)
    return f"Opened '{name}' via Start menu search"


def launch(name):
    """Default app launcher: Start-menu search (click-free, reliable)."""
    return open_via_start(name.strip())


def launch_direct(name):
    name = name.lower().strip()

    # Claude Cowork: open the Claude app's Cowork surface via deep link, then
    # fall back to just opening the app (navigate to Cowork manually / via UI automation).
    if name in ("cowork", "claude cowork", "claude-cowork"):
        for uri in ("claude://cowork", "claude://"):
            try:
                os.startfile(uri)
                return f"Opened {uri} (if Cowork didn't focus, it may need an in-app click)"
            except OSError:
                continue
        name = "claude"  # fall through to exe launch below

    # 1) Protocol handler.
    if name in URI:
        try:
            os.startfile(URI[name])
            return f"Launched {name} via {URI[name]}"
        except OSError:
            pass

    # 2) Known / arbitrary executable.
    for cand in EXES.get(name, [name]):
        exe = cand if os.path.isabs(cand) else shutil.which(cand)
        if exe and os.path.exists(exe):
            subprocess.Popen([exe])
            return f"Launched {exe}"

    # 3) Last resort: let the shell resolve it (App Paths registry, file assoc).
    try:
        os.startfile(name)
        return f"Launched '{name}' via shell"
    except OSError as e:
        return f"Could not launch '{name}': {e}"


def focus(name):
    name = name.lower().strip()
    needle = TITLES.get(name, name)
    try:
        import pygetwindow as gw
    except Exception as e:
        return f"focus unavailable: {e}"
    matches = [w for w in gw.getAllWindows() if needle in (w.title or "").lower()]
    if not matches:
        return f"No window matching '{needle}'. Try opening {name} first."
    win = matches[0]
    try:
        _force_foreground(win._hWnd)
        return f"Focused: {win.title}"
    except Exception:
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            return f"Focused (fallback): {win.title}"
        except Exception as e:
            return f"Found '{win.title}' but couldn't focus it: {e}"
