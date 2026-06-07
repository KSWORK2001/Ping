"""Windows UI Automation element enumeration for precise, pixel-perfect clicking.

We read the on-screen controls (name, type, exact rectangle) straight from the OS
instead of guessing coordinates from the screenshot. Electron/Chromium apps
(Teams, Discord, VS Code, Claude) only expose their tree AFTER an accessibility
client has queried them, so the first query may be sparse; we warm up and retry.
"""
import ctypes
import time

from pywinauto.uia_element_info import UIAElementInfo

# Control types worth clicking / reading. DataItem is how Teams/Outlook expose
# list rows (e.g. a chat with a person), so it is essential.
CLICKABLE = {
    "Button", "ListItem", "DataItem", "Hyperlink", "MenuItem", "TabItem",
    "CheckBox", "ComboBox", "TreeItem", "Edit", "RadioButton", "Custom",
    "Text", "Image", "Group",
}


def _fg_hwnd():
    return ctypes.windll.user32.GetForegroundWindow()


def _collect(ei, budget):
    t0 = time.time()
    out, seen = [], set()
    try:
        desc = ei.descendants()
    except Exception:
        return out
    for info in desc:
        if time.time() - t0 > budget:
            break
        try:
            name = (info.name or "").strip()
            ct = info.control_type
            if not name or ct not in CLICKABLE:
                continue
            r = info.rectangle
            w, h = r.right - r.left, r.bottom - r.top
            if w < 6 or h < 6 or w > 3200 or h > 2200:
                continue
            cx, cy = r.left + w // 2, r.top + h // 2
            if cx < 0 or cy < 0:
                continue
            key = (name[:40], cx // 6, cy // 6)
            if key in seen:
                continue
            seen.add(key)
            out.append({"type": ct, "name": name[:70], "cx": cx, "cy": cy, "w": w, "h": h})
        except Exception:
            continue
    return out


def enumerate_active(max_elems=55, budget=4.0):
    """Return (window_title, [elements]) for the foreground window.

    Each element: {type, name, cx, cy, w, h} with cx/cy the TRUE screen-pixel
    center (click these directly with pyautogui - do not rescale). Safe to call
    from a worker thread; never raises (returns ("", []) on any failure).
    """
    try:
        import comtypes
        try:
            comtypes.CoInitialize()  # COM must be init'd per worker thread
        except Exception:
            pass
        hwnd = _fg_hwnd()
        if not hwnd:
            return "", []
        ei = UIAElementInfo(hwnd)
        title = ei.name or ""
        elems = _collect(ei, budget)
        if len(elems) < 5:  # Electron a11y warm-up: first query enables the tree
            time.sleep(0.3)
            elems = _collect(ei, budget)
        elems.sort(key=lambda e: (e["cy"], e["cx"]))
        return title, elems[:max_elems]
    except Exception:
        return "", []
