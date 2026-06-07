"""General desktop automation: type, hotkeys, mouse."""
import pyautogui

pyautogui.FAILSAFE = True  # slam mouse to a corner to abort


def type_text(text):
    pyautogui.write(text, interval=0.02)
    return f"Typed {len(text)} chars"


def press(combo):
    keys = [k.strip().lower() for k in combo.replace("+", " ").split() if k.strip()]
    if not keys:
        return "No keys given"
    pyautogui.hotkey(*keys)
    return f"Pressed {'+'.join(keys)}"


def click(x, y):
    pyautogui.click(int(x), int(y))
    return f"Clicked ({x}, {y})"


def double_click(x, y):
    pyautogui.doubleClick(int(x), int(y))
    return f"Double-clicked ({x}, {y})"


def right_click(x, y):
    pyautogui.rightClick(int(x), int(y))
    return f"Right-clicked ({x}, {y})"


def scroll(amount, x=None, y=None):
    if x is not None and y is not None:
        pyautogui.moveTo(int(x), int(y))
    pyautogui.scroll(int(amount))
    return f"Scrolled {amount}"


def move(x, y):
    pyautogui.moveTo(int(x), int(y))
    return f"Moved to ({x}, {y})"


def screen_size():
    w, h = pyautogui.size()
    return f"Screen size: {w} x {h}"
