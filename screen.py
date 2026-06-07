"""Screen capture helpers."""
import ctypes
import ctypes.wintypes
import io
import mss
from PIL import Image, ImageDraw

MAX_WIDTH = 1920  # downscale wide screens so uploads stay small


def list_monitors():
    with mss.mss() as sct:
        return list(range(len(sct.monitors)))


def _cursor_pos():
    """Return the cursor position in virtual-screen coordinates, or None."""
    try:
        pt = ctypes.wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None


def _draw_cursor(img, x, y):
    """Draw an arrow pointer with its tip at (x, y) on the image."""
    draw = ImageDraw.Draw(img)
    # Classic arrow pointer outline, tip at (0, 0), scaled up for visibility.
    s = 1.6
    pts = [(0, 0), (0, 16), (4, 12), (7, 18), (9, 17), (6, 11), (11, 11)]
    poly = [(x + px * s, y + py * s) for px, py in pts]
    # White fill with a black outline so it shows on any background.
    draw.polygon(poly, fill=(255, 255, 255), outline=(0, 0, 0))
    draw.line(poly + [poly[0]], fill=(0, 0, 0), width=2)


def capture(monitor=0, cursor=True):
    """Capture a monitor to a PNG BytesIO.

    monitor=0 -> the virtual "all monitors" rectangle.
    monitor=1 -> primary display, 2 -> second display, etc.
    cursor=True -> overlay the mouse pointer at its current position.
    """
    with mss.mss() as sct:
        mons = sct.monitors
        idx = monitor if 0 <= monitor < len(mons) else 0
        mon = mons[idx]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

        ratio = 1.0
        if img.width > MAX_WIDTH:
            ratio = MAX_WIDTH / img.width
            img = img.resize((MAX_WIDTH, int(img.height * ratio)))

        if cursor:
            pos = _cursor_pos()
            if pos is not None:
                # Translate from virtual-screen coords into this capture's frame.
                cx = (pos[0] - mon["left"]) * ratio
                cy = (pos[1] - mon["top"]) * ratio
                # Only draw if the cursor lands within the captured region.
                if -32 <= cx <= img.width + 32 and -32 <= cy <= img.height + 32:
                    _draw_cursor(img, cx, cy)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf
