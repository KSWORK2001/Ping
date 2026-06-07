"""A small local status dashboard for Ping.

Runs an aiohttp web server inside the bot's existing asyncio loop (aiohttp ships
with discord.py, so no new dependency). It serves:
  GET /            - a self-contained HTML/CSS/JS page (no build step)
  GET /api/status  - JSON: bot + system status, recent activity, workflows, log tail
  GET /shot.png    - a fresh screenshot (optional; off when DASHBOARD_SHOT=0)

The page polls /api/status on an interval and refreshes the screenshot less
often. It binds to 127.0.0.1 by default so it's reachable only from this PC;
set DASHBOARD_HOST=0.0.0.0 to expose it on your LAN (only if you trust it).

bot.py records high-level milestones via dashboard.event(...); finer detail comes
from tailing ping_debug.log. Everything is in-memory and best-effort: a failure
here never takes down the bot.
"""
import collections
import os
import platform
import time
from datetime import datetime

import psutil
from aiohttp import web

import config
import system
import screen
import workflows
from applog import logger

# High-level activity ring buffer (newest last). bot.py appends to this.
_events = collections.deque(maxlen=60)
_runner = None
_state_fn = None  # set by start(): returns bot-specific counts (tasks/watchers)
_LOG_PATH = os.path.join(config.WORKDIR, "ping_debug.log")


def event(text):
    """Record a one-line activity milestone shown on the dashboard."""
    try:
        _events.append({"t": datetime.now().strftime("%H:%M:%S"), "text": str(text)[:160]})
    except Exception:
        pass


def _uptime():
    secs = int(time.time() - system.START)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"


def _system_info():
    """Blocking system snapshot - call via to_thread so the loop isn't stalled."""
    mem = psutil.virtual_memory()
    return {
        "uptime": _uptime(),
        "cpu": psutil.cpu_percent(interval=0.2),
        "ram_pct": mem.percent,
        "ram_used_mb": mem.used // (1024 ** 2),
        "ram_total_mb": mem.total // (1024 ** 2),
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "active_window": system.active_window_title() or "(none)",
        "open_windows": len(system.list_windows()),
    }


def _log_tail(n=40):
    try:
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [ln.rstrip("\n") for ln in lines[-n:]]
    except FileNotFoundError:
        return []
    except Exception as e:
        return [f"(could not read log: {e})"]


async def _api_status(request):
    import asyncio
    sysinfo = await asyncio.to_thread(_system_info)
    try:
        flows = await asyncio.to_thread(workflows.list_all)
    except Exception:
        flows = []
    bot_state = {}
    if _state_fn:
        try:
            bot_state = _state_fn()
        except Exception:
            bot_state = {}
    data = {
        "ok": True,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bot": bot_state,
        "system": sysinfo,
        "workflows": [
            {"name": f["name"], "steps": f.get("step_count", 0),
             "runs": f.get("run_count", 0), "last_status": f.get("last_status")}
            for f in flows
        ],
        "events": list(_events)[-40:][::-1],  # newest first
        "log": _log_tail(40),
        "shot_enabled": config.DASHBOARD_SHOT > 0,
    }
    return web.json_response(data)


async def _shot(request):
    import asyncio
    if config.DASHBOARD_SHOT <= 0:
        return web.Response(status=404, text="screenshots disabled")
    try:
        buf = await asyncio.to_thread(screen.capture, config.DASHBOARD_SHOT_MONITOR)
        return web.Response(body=buf.getvalue(), content_type="image/png")
    except Exception as e:
        return web.Response(status=500, text=f"capture failed: {e}")


async def _index(request):
    html = (PAGE
            .replace("__REFRESH__", str(max(5, config.DASHBOARD_REFRESH)))
            .replace("__SHOT_REFRESH__", str(config.DASHBOARD_SHOT))
            .replace("__SHOT_ENABLED__", "true" if config.DASHBOARD_SHOT > 0 else "false"))
    return web.Response(text=html, content_type="text/html")


async def start(bot_client, state_fn=None):
    """Start the dashboard web server. Best-effort: logs and returns on failure
    instead of raising, so the bot stays up even if the port is taken."""
    global _runner, _state_fn
    if not config.DASHBOARD_ENABLED:
        return
    if _runner is not None:
        return  # already started (guard against on_ready firing twice)
    _state_fn = state_fn
    app = web.Application()
    app.add_routes([
        web.get("/", _index),
        web.get("/api/status", _api_status),
        web.get("/shot.png", _shot),
    ])
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, config.DASHBOARD_HOST, config.DASHBOARD_PORT)
        await site.start()
    except Exception as e:
        logger.warning("dashboard: failed to start on %s:%s - %s",
                       config.DASHBOARD_HOST, config.DASHBOARD_PORT, e)
        return
    _runner = runner
    url = f"http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}"
    logger.info("dashboard: serving at %s", url)
    print(f"[Ping] dashboard at {url}", flush=True)
    event("dashboard started")


async def stop():
    global _runner
    if _runner is not None:
        try:
            await _runner.cleanup()
        except Exception:
            pass
        _runner = None


# --- the page (inline CSS+JS; CSS braces mean we use .replace, not .format) ---
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ping status</title>
<style>
  :root { --bg:#0f1117; --card:#1a1d27; --line:#2a2e3a; --txt:#e6e8ee; --muted:#8b90a0; --ok:#3ddc84; --bad:#ff5c5c; --warn:#ffb454; --accent:#6ea8fe; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; }
  header { display:flex; align-items:center; gap:12px; padding:16px 20px; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--bg); }
  h1 { font-size:18px; margin:0; font-weight:600; }
  .dot { width:12px; height:12px; border-radius:50%; background:var(--muted); box-shadow:0 0 0 0 rgba(61,220,132,.6); }
  .dot.ok { background:var(--ok); animation:pulse 2s infinite; }
  .dot.bad { background:var(--bad); }
  @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(61,220,132,.5);} 70%{box-shadow:0 0 0 8px rgba(61,220,132,0);} 100%{box-shadow:0 0 0 0 rgba(61,220,132,0);} }
  .sub { color:var(--muted); font-size:12px; margin-left:auto; text-align:right; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(290px,1fr)); gap:14px; padding:18px 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
  .card h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 10px; }
  .kv { display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px dashed var(--line); }
  .kv:last-child { border-bottom:0; }
  .kv b { font-weight:600; }
  .bar { height:6px; border-radius:4px; background:var(--line); overflow:hidden; margin-top:3px; }
  .bar > i { display:block; height:100%; background:var(--accent); }
  .feed { max-height:280px; overflow:auto; font-size:12.5px; }
  .feed div { padding:3px 0; border-bottom:1px solid var(--line); display:flex; gap:8px; }
  .feed .t { color:var(--muted); flex:0 0 auto; }
  .log { max-height:300px; overflow:auto; font:11.5px/1.45 ui-monospace,Consolas,monospace; color:#c8cdda; white-space:pre-wrap; word-break:break-word; }
  .wf { font-size:12.5px; }
  .pill { font-size:11px; padding:1px 7px; border-radius:10px; background:var(--line); color:var(--muted); }
  .pill.success { background:rgba(61,220,132,.15); color:var(--ok); }
  .pill.failed { background:rgba(255,92,92,.15); color:var(--bad); }
  img#shot { width:100%; border-radius:8px; border:1px solid var(--line); display:block; }
  .grid2 { grid-column:1 / -1; }
  a { color:var(--accent); text-decoration:none; }
</style>
</head>
<body>
<header>
  <span id="dot" class="dot"></span>
  <h1>Ping</h1>
  <div class="sub"><span id="who">connecting…</span><br><span id="gen"></span></div>
</header>
<div class="grid">
  <div class="card">
    <h2>Bot</h2>
    <div class="kv"><span>State</span><b id="state">—</b></div>
    <div class="kv"><span>Latency</span><b id="lat">—</b></div>
    <div class="kv"><span>Running tasks</span><b id="tasks">—</b></div>
    <div class="kv"><span>Screen watchers</span><b id="watch">—</b></div>
    <div class="kv"><span>Uptime</span><b id="up">—</b></div>
  </div>
  <div class="card">
    <h2>System</h2>
    <div class="kv"><span>Host</span><b id="host">—</b></div>
    <div class="kv"><span>Active window</span><b id="active">—</b></div>
    <div class="kv"><span>CPU</span><b id="cpu">—</b></div>
    <div class="bar"><i id="cpubar" style="width:0%"></i></div>
    <div class="kv" style="margin-top:6px"><span>RAM</span><b id="ram">—</b></div>
    <div class="bar"><i id="rambar" style="width:0%"></i></div>
  </div>
  <div class="card">
    <h2>Activity</h2>
    <div class="feed" id="feed"><div>—</div></div>
  </div>
  <div class="card">
    <h2>Workflows</h2>
    <div class="wf" id="wf">—</div>
  </div>
  <div class="card" id="shotcard" style="display:none">
    <h2>Screen</h2>
    <img id="shot" alt="screenshot">
  </div>
  <div class="card grid2">
    <h2>Recent log</h2>
    <div class="log" id="log">—</div>
  </div>
</div>
<script>
const REFRESH = __REFRESH__ * 1000;
const SHOT_REFRESH = __SHOT_REFRESH__ * 1000;
const SHOT_ENABLED = __SHOT_ENABLED__;
function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function tick(){
  let ok = true;
  try {
    const r = await fetch('/api/status', {cache:'no-store'});
    const d = await r.json();
    const b = d.bot||{}, s = d.system||{};
    const ready = !!b.ready;
    document.getElementById('dot').className = 'dot ' + (ready?'ok':'bad');
    document.getElementById('who').textContent = b.bot_user || 'Ping';
    document.getElementById('gen').textContent = 'updated ' + (d.generated||'');
    document.getElementById('state').textContent = ready ? 'online' : 'offline';
    document.getElementById('lat').textContent = (b.latency_ms!=null? b.latency_ms+' ms':'—');
    document.getElementById('tasks').textContent = b.running_tasks!=null? b.running_tasks : '—';
    document.getElementById('watch').textContent = b.watchers!=null? b.watchers : '—';
    document.getElementById('up').textContent = s.uptime || '—';
    document.getElementById('host').textContent = (s.host||'—') + ' · ' + (s.os||'');
    document.getElementById('active').textContent = s.active_window || '—';
    document.getElementById('cpu').textContent = (s.cpu!=null? s.cpu+'%':'—');
    document.getElementById('cpubar').style.width = (s.cpu||0) + '%';
    document.getElementById('ram').textContent = (s.ram_pct!=null? s.ram_pct+'% ('+s.ram_used_mb+'/'+s.ram_total_mb+' MB)':'—');
    document.getElementById('rambar').style.width = (s.ram_pct||0) + '%';
    const feed = (d.events||[]).map(e=>`<div><span class="t">${esc(e.t)}</span><span>${esc(e.text)}</span></div>`).join('') || '<div>(no activity yet)</div>';
    document.getElementById('feed').innerHTML = feed;
    const wf = (d.workflows||[]).map(w=>{
      const st = w.last_status||'never run';
      const cls = st==='success'?'success':(st==='failed'?'failed':'');
      return `<div class="kv"><span>${esc(w.name)} <span style="color:var(--muted)">· ${w.steps} steps · ${w.runs} runs</span></span><span class="pill ${cls}">${esc(st)}</span></div>`;
    }).join('') || '(no saved workflows)';
    document.getElementById('wf').innerHTML = wf;
    document.getElementById('log').textContent = (d.log||[]).join('\\n') || '(log empty)';
  } catch(e){
    ok = false;
    document.getElementById('dot').className = 'dot bad';
    document.getElementById('state').textContent = 'starting…';
  }
  // Poll on the normal cadence when healthy; retry quickly while still starting up.
  setTimeout(tick, ok ? REFRESH : 3000);
}
function refreshShot(){
  if(!SHOT_ENABLED) return;
  document.getElementById('shotcard').style.display = '';
  document.getElementById('shot').src = '/shot.png?ts=' + Date.now();
}
tick();
if(SHOT_ENABLED){ refreshShot(); if(SHOT_REFRESH>0) setInterval(refreshShot, SHOT_REFRESH); }
</script>
</body>
</html>"""
