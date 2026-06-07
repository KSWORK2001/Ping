"""Keyboard-shortcut cheat-sheet for the apps Ping drives (web-verified 2025-2026).

Mouse clicking from a screenshot is imprecise, so the agent is told to navigate
with these shortcuts whenever possible. `render()` formats this for the prompt.
Each app: title substrings (for window/screenshot identification), a list of
(action, keys) in pyautogui hotkey format, a launch_then sequence to reach the
main search/compose box, and warn (critical gotchas the agent must respect).
"""

SHORTCUTS = {
    "Google Chrome": {
        "titles": ["google chrome", "- chrome"],
        "launch_then": "ctrl+l -> type URL or search query -> enter.",
        "keys": [
            ("focus address/search bar", "ctrl+l"),
            ("tab quick-switcher (type to filter open tabs)", "ctrl+shift+a"),
            ("new tab / close tab", "ctrl+t / ctrl+w"),
            ("next / previous tab", "ctrl+tab / ctrl+shift+tab"),
            ("jump to tab 1-8 / last tab", "ctrl+1 / ctrl+9"),
            ("find on page", "ctrl+f"),
            ("submit", "enter"),
        ],
        "warn": "",
    },
    "Visual Studio Code": {
        "titles": ["visual studio code", "- code"],
        "launch_then": "ctrl+p -> type filename -> enter (jump to file/tab); ctrl+shift+p -> type command -> enter.",
        "keys": [
            ("command palette (any command)", "ctrl+shift+p"),
            ("quick open file/tab", "ctrl+p"),
            ("find / search project", "ctrl+f / ctrl+shift+f"),
            ("go to line", "ctrl+g"),
            ("toggle terminal", "ctrl+`"),
            ("focus explorer tree", "ctrl+shift+e"),
            ("save / close tab", "ctrl+s / ctrl+w"),
        ],
        "warn": "",
    },
    "Windows File Explorer": {
        "titles": ["file explorer", "this pc", "documents", "downloads", "home"],
        "launch_then": "alt+d -> type a full path -> enter; or ctrl+e -> type a search -> enter.",
        "keys": [
            ("open a new window", "win+e"),
            ("focus address bar (type a path)", "alt+d"),
            ("focus search box", "ctrl+e"),
            ("up one folder / back", "alt+up / alt+left"),
            ("open selected / rename", "enter / f2"),
            ("new folder", "ctrl+shift+n"),
        ],
        "warn": "Window title shows the folder name (e.g. 'Documents'), not 'File Explorer'.",
    },
    "Microsoft Teams": {
        "titles": ["teams", "microsoft teams"],
        "launch_then": "ctrl+e -> type a person's name -> enter to open that chat (or ctrl+g -> name -> enter).",
        "keys": [
            ("focus search / command box", "ctrl+e"),
            ("go to a chat/person by name", "ctrl+g"),
            ("new chat", "ctrl+n"),
            ("find in current chat", "ctrl+f"),
            ("send (force send)", "ctrl+enter"),
            ("go to Chat / Calendar", "ctrl+2 / ctrl+4"),
            ("pick list item", "down/up then enter"),
        ],
        "warn": "",
    },
    "Microsoft Outlook": {
        "titles": ["outlook", "- mail", "inbox"],
        "launch_then": "ctrl+1 (be in Mail) -> ctrl+e -> type query -> enter. Compose: ctrl+shift+m -> To -> tab -> subject -> tab -> body -> alt+s.",
        "keys": [
            ("search mailbox", "ctrl+e"),
            ("new email (from any module)", "ctrl+shift+m"),
            ("send", "alt+s"),
            ("reply / reply all", "ctrl+r / ctrl+shift+r"),
            ("go to Mail", "ctrl+1"),
            ("open / next / prev in list", "enter / down / up"),
        ],
        "warn": "ctrl+f = FORWARD in Outlook, NOT find. Use ctrl+e to search.",
    },
    "Discord": {
        "titles": ["discord"],
        "launch_then": "ctrl+k -> type a person/channel name -> enter to jump there (prefix @user, #channel, *server).",
        "keys": [
            ("quick switcher (jump to any DM/channel/server)", "ctrl+k"),
            ("search current channel", "ctrl+f"),
            ("send / newline", "enter / shift+enter"),
            ("next / previous channel", "alt+down / alt+up"),
            ("edit last message (empty box)", "up"),
        ],
        "warn": "ctrl+e = emoji picker and ctrl+s = sticker picker here, so do NOT use them for search/save. Send is plain enter.",
    },
    "Claude desktop app": {
        "titles": ["claude"],
        "launch_then": "ctrl+shift+o for a new chat (cursor lands in the box) -> type -> enter; or ctrl+k -> type -> down/up -> enter to open an existing chat.",
        "keys": [
            ("search / jump to a conversation", "ctrl+k"),
            ("new chat", "ctrl+shift+o"),
            ("send / newline", "enter / shift+enter"),
            ("find in conversation", "ctrl+f"),
        ],
        "warn": "new chat is ctrl+shift+o (ctrl+n opens a new WINDOW, not a new chat). If a hotkey seems dead, press esc to defocus the input first.",
    },
}


def render():
    lines = []
    for app, d in SHORTCUTS.items():
        sc = "; ".join(f"{a}={k}" for a, k in d["keys"])
        line = f"- {app} (window contains {d['titles']}): {sc}."
        if d.get("launch_then"):
            line += f" Flow: {d['launch_then']}"
        if d.get("warn"):
            line += f" WARNING: {d['warn']}"
        lines.append(line)
    return "\n".join(lines)
