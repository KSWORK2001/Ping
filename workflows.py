"""Workflow store: persist successful agent runs and replay them.

A "workflow" is a named, repeatable process. We save the natural-language goal
*and* the concrete action sequence that completed it on a good run (the hybrid
model): replay executes the recorded steps deterministically, but the agent
loop can fall back to live vision the moment a step can't be resolved.

Storage is a single SQLite file (stdlib, zero dependencies). This module is
pure persistence - it knows nothing about the screen; agentloop.replay() does
the executing and calls record_run() here to log the outcome.
"""
import json
import sqlite3
from datetime import datetime, timezone

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    goal        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    run_count   INTEGER NOT NULL DEFAULT 0,
    last_status TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id          INTEGER PRIMARY KEY,
    workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    action      TEXT NOT NULL,   -- JSON of the model's action for this step
    element     TEXT,            -- JSON {name,type} when it was a click_element
    result      TEXT             -- what happened when it ran (for reference)
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,   -- success | failed | fallback
    mode        TEXT,            -- replay | fallback
    steps_taken INTEGER,
    summary     TEXT
);
CREATE INDEX IF NOT EXISTS idx_steps_wf ON steps(workflow_id, idx);
CREATE INDEX IF NOT EXISTS idx_runs_wf  ON runs(workflow_id);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect():
    conn = sqlite3.connect(config.WORKFLOW_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    """Create the schema if it doesn't exist. Safe to call repeatedly."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def save(name, goal, history):
    """Save (or overwrite) a workflow from a completed agent run's history.

    `history` is the list agentloop produces: each item is
    {step, action, result, element?}. Replaces any existing steps for this name
    so re-saving with `name` updates the recipe. Returns the workflow id.
    """
    name = name.strip()
    now = _now()
    with _connect() as conn:
        row = conn.execute("SELECT id FROM workflows WHERE name = ?", (name,)).fetchone()
        if row:
            wf_id = row["id"]
            conn.execute(
                "UPDATE workflows SET goal = ?, updated_at = ? WHERE id = ?",
                (goal, now, wf_id),
            )
            conn.execute("DELETE FROM steps WHERE workflow_id = ?", (wf_id,))
        else:
            cur = conn.execute(
                "INSERT INTO workflows (name, goal, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (name, goal, now, now),
            )
            wf_id = cur.lastrowid
        for item in history:
            el = item.get("element")
            conn.execute(
                "INSERT INTO steps (workflow_id, idx, action, element, result) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    wf_id,
                    item.get("step", 0),
                    json.dumps(item.get("action") or {}),
                    json.dumps(el) if el else None,
                    str(item.get("result", ""))[:500],
                ),
            )
    return wf_id


def list_all():
    """All workflows with their step counts, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT w.*, (SELECT COUNT(*) FROM steps s WHERE s.workflow_id = w.id) "
            "AS step_count FROM workflows w ORDER BY w.updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def names():
    """Just the workflow names (for the brain's router prompt)."""
    with _connect() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM workflows ORDER BY name")]


def get(name):
    """A workflow dict with its ordered `steps`, or None. Each step is
    {idx, action(dict), element(dict|None), result}."""
    with _connect() as conn:
        wf = conn.execute("SELECT * FROM workflows WHERE name = ?", (name.strip(),)).fetchone()
        if not wf:
            return None
        steps = conn.execute(
            "SELECT idx, action, element, result FROM steps "
            "WHERE workflow_id = ? ORDER BY idx",
            (wf["id"],),
        ).fetchall()
    out = dict(wf)
    out["steps"] = [
        {
            "idx": s["idx"],
            "action": json.loads(s["action"]),
            "element": json.loads(s["element"]) if s["element"] else None,
            "result": s["result"],
        }
        for s in steps
    ]
    return out


def delete(name):
    """Delete a workflow (and its steps/runs via cascade). Returns True if removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM workflows WHERE name = ?", (name.strip(),))
        return cur.rowcount > 0


def start_run(wf_id, mode="replay"):
    """Open a run record; returns its id. Close it with finish_run()."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (workflow_id, started_at, status, mode) VALUES (?, ?, ?, ?)",
            (wf_id, _now(), "running", mode),
        )
        return cur.lastrowid


def finish_run(run_id, wf_id, status, mode, steps_taken, summary=""):
    """Close a run record and roll its outcome up onto the workflow."""
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, mode = ?, "
            "steps_taken = ?, summary = ? WHERE id = ?",
            (now, status, mode, steps_taken, summary[:500], run_id),
        )
        conn.execute(
            "UPDATE workflows SET run_count = run_count + 1, last_status = ?, "
            "updated_at = ? WHERE id = ?",
            (status, now, wf_id),
        )
