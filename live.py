"""Phase 3 (best-effort): drive the Discord desktop client to screen-share.

Discord bots CANNOT screen-share / Go Live - that is a desktop-client-only
feature with no API. So to get true live video we automate the real Discord
window with pywinauto. This is inherently fragile: it depends on Discord's
current UI, your language, and which call you're in. Treat it as best-effort
and prefer !shot / !watch (screenshots) for reliable monitoring.
"""
import subprocess
import time

import config


def open_discord():
    """Make sure the Discord desktop client is running and focused."""
    subprocess.Popen([config.DISCORD_UPDATE_EXE, "--processStart", "Discord.exe"])
    time.sleep(5)
    try:
        import pygetwindow as gw
        for w in gw.getAllWindows():
            if "discord" in (w.title or "").lower():
                if w.isMinimized:
                    w.restore()
                w.activate()
                return True
    except Exception:
        pass
    return False


def go_live(hotkey_join=None):
    """Best-effort: focus Discord and trigger screen share.

    Discord has no stable hotkey for "share screen", so this currently just
    ensures Discord is focused and returns guidance. Wire up a concrete
    pywinauto click sequence here once we pin down your Discord layout
    (which server/voice channel, button positions).
    """
    ok = open_discord()
    if not ok:
        return "Could not focus Discord. Is it installed / logged in?"
    return (
        "Discord focused. Live screen-share automation is a stub - we need to "
        "record your exact click path (join voice channel -> Share Your Screen) "
        "to make it reliable. For now use !shot or !watch for monitoring."
    )
