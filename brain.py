#!/usr/bin/env python3
"""Local Claude brain — machine-wide cross-session memory (CLI + module).

Append-only SQLite event log adapted from a production AI-agent memory system, plus FTS5
hybrid search, decay/usage-weighted recall (see recall.py), and secret redaction
(see redact.py). Stdlib only. Works directly against SQLite, so the CLI + hooks
function even when the background daemon (braind.py) is down.

  brain record  --type decision --summary "..." [--project P] [--source S] [--confidence C]
  brain query   [--project P] [--type T] [--since ISO] [--limit N] [--json]
  brain search  "<text>" [--project P] [--limit N]
  brain recall  ["<topic>"] [--project P] [--budget BYTES] [--session SID] [--layer digest|timeline]
  brain stats
  brain daemon  start|stop|restart|status|run
  brain web     [--open]
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import recall as recall_mod  # noqa: E402
import redact as redact_mod  # noqa: E402

DEFAULT_DB = str(Path.home() / ".claude" / "brain" / "brain.db")
SCHEMA = (HERE / "schema.sql").read_text()
PLIST_LABEL = f"com.{getpass.getuser()}.claude-brain"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
SOCKET_PATH = str(HERE / "brain.sock")
WEB_PORT = int(os.environ.get("CLAUDE_BRAIN_PORT", "8787"))

# Proactive-encode trigger files (written by braind.py, read by the session hooks).
ENCODE_FLAG = HERE / ".encode-pending"
LAST_ENCODE = HERE / ".last-encode"
ENCODE_NUDGED = HERE / ".encode-nudged"

_lock = threading.Lock()


def db_path() -> str:
    return os.environ.get("CLAUDE_BRAIN_DB") or DEFAULT_DB


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str | None = None) -> sqlite3.Connection:
    p = path or db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | None = None) -> bool:
    """Create schema idempotently. Returns True if FTS5 is available."""
    with _lock:
        conn = _connect(path)
        try:
            try:
                conn.executescript(SCHEMA)
                fts = True
            except sqlite3.OperationalError:
                core = re.sub(r"CREATE VIRTUAL TABLE.*?\);", "", SCHEMA, flags=re.DOTALL)
                conn.executescript(core)
                fts = False
            return fts
        finally:
            conn.close()


def _has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events_fts'"
    ).fetchone()
    return row is not None


def project_slug(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).name or None


# ---------------------------------------------------------------- write path

# Actor/type combos whose newer event supersedes older ones of the same kind
# (state that changes over time), mirroring a production AI-agent memory system's supersession rule.
_SUPERSEDE_TYPES = {"preference", "convention"}


def record(*, source: str, type: str, summary: str, project: str | None = None,
           actor: str | None = None, session_id: str | None = None,
           payload: dict | None = None, confidence: float = 0.5, key: str | None = None,
           ts: str | None = None, parent_id: int | None = None,
           path: str | None = None) -> int:
    if not source or not type or not summary:
        raise ValueError("source, type, summary are required")

    summary = redact_mod.clean(summary)
    body = redact_mod.clean_obj(dict(payload or {}))
    body["summary"] = summary
    if key:
        body["key"] = key
    payload_json = json.dumps(body, default=str)
    ts_val = ts or _now_iso()
    ingested = _now_iso()

    fts = init_db(path)
    with _lock:
        conn = _connect(path)
        try:
            cur = conn.execute(
                "INSERT INTO events (ts, source, type, actor, project, session_id, "
                "payload_json, confidence, ingested_at, parent_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts_val, source, type, actor, project, session_id, payload_json,
                 float(confidence), ingested, parent_id),
            )
            new_id = int(cur.lastrowid)

            if fts and _has_fts(conn):
                try:
                    conn.execute(
                        "INSERT INTO events_fts(rowid, summary, type, project) VALUES (?,?,?,?)",
                        (new_id, summary, type, project or ""),
                    )
                except sqlite3.OperationalError:
                    pass

            # Supersede only an EARLIER memory about the same subject (--key) in the
            # same scope — newer state wins, without clobbering unrelated entries.
            if key and type in _SUPERSEDE_TYPES:
                conn.execute(
                    "UPDATE events SET superseded_by=? WHERE type=? AND id!=? "
                    "AND superseded_by IS NULL AND IFNULL(project,'')=IFNULL(?,'') "
                    "AND json_extract(payload_json,'$.key')=?",
                    (new_id, type, new_id, project, key),
                )
            # Explicit supersession via payload.supersedes = [ids]
            for old in (body.get("supersedes") or []):
                try:
                    conn.execute(
                        "UPDATE events SET superseded_by=? WHERE id=? AND superseded_by IS NULL",
                        (new_id, int(old)),
                    )
                except (ValueError, TypeError):
                    pass
            return new_id
        finally:
            conn.close()


# ---------------------------------------------------------------- read path

def _row_to_dict(r: sqlite3.Row) -> dict:
    try:
        body = json.loads(r["payload_json"]) if r["payload_json"] else {}
    except json.JSONDecodeError:
        body = {"_raw": r["payload_json"]}
    return {
        "id": r["id"], "ts": r["ts"], "source": r["source"], "type": r["type"],
        "actor": r["actor"], "project": r["project"], "session_id": r["session_id"],
        "summary": body.get("summary"), "payload": body,
        "confidence": r["confidence"], "quality_score": r["quality_score"],
        "superseded_by": r["superseded_by"], "ingested_at": r["ingested_at"],
    }


_COLS = ("id, ts, source, type, actor, project, session_id, payload_json, "
         "confidence, quality_score, superseded_by, ingested_at")


def query(*, source=None, type=None, project=None, actor=None, since=None,
          until=None, limit=100, order="desc", include_superseded=False,
          path=None) -> list[dict]:
    where, params = [], []
    for col, val in (("source", source), ("type", type), ("project", project), ("actor", actor)):
        if val is not None:
            where.append(f"{col} = ?")
            params.append(val)
    if since:
        where.append("ts >= ?"); params.append(since)
    if until:
        where.append("ts <= ?"); params.append(until)
    if not include_superseded:
        where.append("superseded_by IS NULL")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    direction = "ASC" if order.lower() == "asc" else "DESC"
    params.append(int(limit))
    init_db(path)
    with _lock:
        conn = _connect(path)
        try:
            rows = conn.execute(
                f"SELECT {_COLS} FROM events{clause} ORDER BY ts {direction} LIMIT ?", params
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_dict(r) for r in rows]


_FTS_SANITIZE = re.compile(r"[^A-Za-z0-9_]+")


def search(text: str, *, project=None, limit=50, path=None) -> list[dict]:
    """Hybrid keyword search: FTS5 if available, else LIKE. Ranking happens later."""
    tokens = [t for t in _FTS_SANITIZE.split(text or "") if len(t) >= 2]
    if not tokens:
        return query(project=project, limit=limit, path=path)
    init_db(path)
    with _lock:
        conn = _connect(path)
        try:
            ids: list[int] = []
            if _has_fts(conn):
                match = " OR ".join(tokens)
                try:
                    rows = conn.execute(
                        "SELECT rowid FROM events_fts WHERE events_fts MATCH ? LIMIT ?",
                        (match, int(limit) * 3),
                    ).fetchall()
                    ids = [r[0] for r in rows]
                except sqlite3.OperationalError:
                    ids = []
            if not ids:  # LIKE fallback
                likeclause = " OR ".join(["payload_json LIKE ?"] * len(tokens))
                rows = conn.execute(
                    f"SELECT id FROM events WHERE ({likeclause}) AND superseded_by IS NULL "
                    "ORDER BY ts DESC LIMIT ?",
                    [f"%{t}%" for t in tokens] + [int(limit) * 3],
                ).fetchall()
                ids = [r[0] for r in rows]
            if not ids:
                return []
            qmarks = ",".join("?" * len(ids))
            extra = ""
            eparams: list = list(ids)
            if project:
                extra = " AND (project = ? OR project IS NULL)"
                eparams.append(project)
            rows = conn.execute(
                f"SELECT {_COLS} FROM events WHERE id IN ({qmarks}) "
                f"AND superseded_by IS NULL{extra}", eparams
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_dict(r) for r in rows]


def _candidates(project: str | None, limit: int, path=None) -> list[dict]:
    """Recent live events for this project + global, for the SessionStart digest."""
    init_db(path)
    with _lock:
        conn = _connect(path)
        try:
            if project:
                rows = conn.execute(
                    f"SELECT {_COLS} FROM events WHERE superseded_by IS NULL "
                    "AND (project = ? OR project IS NULL) ORDER BY ts DESC LIMIT ?",
                    (project, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_COLS} FROM events WHERE superseded_by IS NULL "
                    "ORDER BY ts DESC LIMIT ?", (int(limit),)
                ).fetchall()
        finally:
            conn.close()
    return [_row_to_dict(r) for r in rows]


def _log_injection(ids: list[int], session_id: str | None, project: str | None, path=None):
    if not ids:
        return
    now = _now_iso()
    with _lock:
        conn = _connect(path)
        try:
            conn.executemany(
                "INSERT INTO injection_log(event_id, session_id, project, injected_at) "
                "VALUES (?,?,?,?)",
                [(i, session_id, project, now) for i in ids],
            )
        finally:
            conn.close()


def recall(*, project=None, query_text=None, budget=2048, session_id=None,
           layer="digest", limit=200, log=True, path=None) -> str:
    rows = search(query_text, project=project, limit=limit, path=path) if query_text \
        else _candidates(project, limit, path=path)
    if layer == "timeline":
        return recall_mod.render_timeline(rows)
    md, selected = recall_mod.render_digest(rows, project, budget_bytes=budget)
    if log and selected:
        _log_injection(selected, session_id, project, path=path)
    return md


def stats(path=None) -> dict:
    init_db(path)
    with _lock:
        conn = _connect(path)
        try:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            live = conn.execute("SELECT COUNT(*) FROM events WHERE superseded_by IS NULL").fetchone()[0]
            by_type = conn.execute(
                "SELECT type, COUNT(*) n FROM events WHERE superseded_by IS NULL "
                "GROUP BY type ORDER BY n DESC LIMIT 15"
            ).fetchall()
            by_proj = conn.execute(
                "SELECT IFNULL(project,'(global)') p, COUNT(*) n FROM events "
                "WHERE superseded_by IS NULL GROUP BY project ORDER BY n DESC LIMIT 15"
            ).fetchall()
        finally:
            conn.close()
    return {"total": total, "live": live,
            "by_type": {r[0]: r[1] for r in by_type},
            "by_project": {r[0]: r[1] for r in by_proj}}


def promote(min_score: float = 0.45, limit: int = 20, path=None) -> list[dict]:
    """Memories repeatedly recalled across sessions (high quality_score) — candidates
    to lift into a CLAUDE.md / curated project memory. Surfaced, never auto-applied."""
    init_db(path)
    with _lock:
        conn = _connect(path)
        try:
            rows = conn.execute(
                f"SELECT {_COLS}, "
                "(SELECT COUNT(*) FROM injection_log WHERE event_id=events.id) AS recalls "
                "FROM events WHERE superseded_by IS NULL AND quality_score >= ? "
                "AND type IN ('preference','convention','decision','gotcha','fix','bug') "
                "ORDER BY quality_score DESC, recalls DESC LIMIT ?",
                (min_score, int(limit)),
            ).fetchall()
        finally:
            conn.close()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["recalls"] = r["recalls"]
        out.append(d)
    return out


# ---------------------------------------------------------- consolidation (encode)

def encode_list(path=None) -> list[dict]:
    """Events awaiting consolidation: live, not yet consolidated, not transient."""
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT id, ts, type, project, payload_json, confidence FROM events "
            "WHERE superseded_by IS NULL AND consolidated_at IS NULL "
            "AND type NOT IN ('consolidated','context.checkpoint') ORDER BY ts"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            summary = json.loads(r["payload_json"]).get("summary", "")
        except Exception:
            summary = ""
        out.append({"id": r["id"], "ts": r["ts"], "type": r["type"],
                    "project": r["project"], "summary": summary, "confidence": r["confidence"]})
    return out


def encode_done(ids, path=None) -> int:
    """Stamp consolidated_at on the reviewed events so they leave the pending set,
    record the encode time, and clear the daemon's pending/nudge flags. Returns count."""
    now = _now_iso()
    n = 0
    with _lock:
        conn = _connect(path)
        try:
            for eid in ids:
                try:
                    conn.execute("UPDATE events SET consolidated_at=? WHERE id=? "
                                 "AND consolidated_at IS NULL", (now, int(eid)))
                    n += conn.execute("SELECT changes()").fetchone()[0]
                except (ValueError, TypeError):
                    pass
        finally:
            conn.close()
    try:
        LAST_ENCODE.write_text(now)
        ENCODE_FLAG.unlink(missing_ok=True)
        ENCODE_NUDGED.unlink(missing_ok=True)
    except Exception:
        pass
    return n


def _encode_nudge(force: bool, debounce_min: int = 90) -> str:
    """One-line nudge if a consolidation pass is due, else ''. Debounced so
    mid-session prompts don't repeat it; `force` (session start) bypasses debounce."""
    if not ENCODE_FLAG.exists():
        return ""
    if not force:
        try:
            last = datetime.fromisoformat(ENCODE_NUDGED.read_text().strip())
            if (datetime.now(timezone.utc) - last).total_seconds() < debounce_min * 60:
                return ""
        except Exception:
            pass
    try:
        reason = json.loads(ENCODE_FLAG.read_text()).get("reason", "events pending")
    except Exception:
        reason = "events pending"
    try:
        ENCODE_NUDGED.write_text(_now_iso())
    except Exception:
        pass
    return ("🧠 brain: consolidation due — " + reason + ". Run /brain-encode to distill "
            "the backlog into the knowledge base (merge duplicates + write a high-signal "
            "synthesis).")


# ---------------------------------------------------------------- daemon ctl

def _uid() -> int:
    return os.getuid()


def _launchctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def daemon_ctl(action: str) -> str:
    domain = f"gui/{_uid()}"
    target = f"{domain}/{PLIST_LABEL}"
    if action == "run":
        os.execv(sys.executable, [sys.executable, str(HERE / "braind.py")])
    if not PLIST_PATH.exists() and action in ("start", "restart"):
        return (f"launchd plist not installed at {PLIST_PATH}. "
                "Run the installer (Phase C) first.")
    if action == "start":
        _launchctl("enable", target)
        r = _launchctl("bootstrap", domain, str(PLIST_PATH))
        if r.returncode == 0:
            return "brain daemon started"
        _launchctl("kickstart", "-k", target)
        return f"brain daemon (re)started [{r.stderr.strip() or 'ok'}]"
    if action == "stop":
        _launchctl("bootout", target)
        return "brain daemon stopped"
    if action == "restart":
        _launchctl("kickstart", "-k", target)
        return "brain daemon restarted"
    if action == "status":
        import socket as _socket
        r = _launchctl("print", target)
        running = "running" if r.returncode == 0 and "state = running" in r.stdout else "not running"
        try:
            with _socket.create_connection(("127.0.0.1", WEB_PORT), timeout=1):
                web = "up"
        except Exception:
            web = "down"
        return (f"daemon: {running}; web: http://127.0.0.1:{WEB_PORT} ({web}); "
                f"plist: {'installed' if PLIST_PATH.exists() else 'missing'}")
    return f"unknown action {action}"


# ---------------------------------------------------------------- hooks

def _normalize_remote(url: str) -> str:
    """git@host:owner/repo.git | http(s)://host[:port]/owner/repo(.git) -> owner/repo."""
    url = re.sub(r"\.git$", "", url.strip())
    m = re.search(r"[:/]([^/:]+/[^/:]+)$", url)
    return m.group(1) if m else url


def _repo_meta(cwd: str | None) -> tuple[str | None, str | None, str | None]:
    """Repo identity: (project_slug, repo_root_abs, remote_slug).

    project_slug = git-root basename (readable, used for scoping/display).
    repo_root + remote are stored on every event so two repos that happen to
    share a basename can never be conflated, and a memory's exact origin is
    always recoverable.
    """
    if not cwd:
        return None, None, None
    root = None
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            root = r.stdout.strip()
    except Exception:
        pass
    remote = None
    if root:
        try:
            r = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                remote = _normalize_remote(r.stdout.strip())
        except Exception:
            pass
    slug = Path(root).name if root else (Path(cwd).name or None)
    return slug, root, remote


def _hook_project(cwd: str | None) -> str | None:
    return _repo_meta(cwd)[0]


def _git_branch(cwd: str | None) -> str | None:
    try:
        r = subprocess.run(["git", "-C", cwd or ".", "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or None
    except Exception:
        return None


def _collect_files(obj, files: set) -> None:
    if isinstance(obj, dict):
        if obj.get("type") == "tool_use" and obj.get("name") in ("Edit", "Write", "NotebookEdit"):
            fp = (obj.get("input") or {}).get("file_path") or (obj.get("input") or {}).get("notebook_path")
            if isinstance(fp, str):
                files.add(fp)
        for v in obj.values():
            _collect_files(v, files)
    elif isinstance(obj, list):
        for v in obj:
            _collect_files(v, files)


def _summarize_transcript(path: str | None) -> tuple[list[str], int]:
    files: set[str] = set()
    turns = 0
    if not path or not os.path.exists(path):
        return [], 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "user" or (obj.get("message") or {}).get("role") == "user":
                    turns += 1
                _collect_files(obj, files)
    except Exception:
        pass
    return sorted(files), turns


def _cmd_hook(event: str) -> int:
    """Dispatch a Claude Code lifecycle hook. ALWAYS exits 0 (fail-open)."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    cwd = data.get("cwd") or os.getcwd()
    session_id = data.get("session_id")
    try:
        project, repo_root, remote = _repo_meta(cwd)
        # Per-project opt-out: a .brain-disabled marker at cwd or the repo root.
        if (Path(cwd) / ".brain-disabled").exists() or \
           (repo_root and (Path(repo_root) / ".brain-disabled").exists()):
            return 0
        repo_fields = {"cwd": cwd, "repo": repo_root, "remote": remote}
        if event in ("session_start", "post_compact"):
            out = recall(project=project, budget=2048, session_id=session_id, log=True)
            if out:
                print(out)
            nudge = _encode_nudge(force=True)   # once at session start
            if nudge:
                print("\n" + nudge)
        elif event == "user_prompt_submit":      # mid-session pickup (debounced)
            nudge = _encode_nudge(force=False)
            if nudge:
                print(nudge)
        elif event == "session_end":
            files, turns = _summarize_transcript(data.get("transcript_path"))
            branch = _git_branch(cwd)
            short = [Path(f).name for f in files[:8]]
            summary = (f"Session in {project or 'unknown'}"
                       + (f" ({branch})" if branch else "")
                       + f": {turns} user turn(s)"
                       + (f", edited {len(files)} file(s): {', '.join(short)}" if files else ""))
            record(source="session", type="session.summary", summary=summary, project=project,
                   session_id=session_id, confidence=0.4,
                   payload={**repo_fields, "files": files, "turns": turns, "branch": branch})
        elif event == "pre_compact":
            record(source="session", type="context.checkpoint",
                   summary=f"Context compaction in {project or 'unknown'} — prior context summarized",
                   project=project, session_id=session_id, confidence=0.3, payload=repo_fields)
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------- CLI

def _print_events(rows: list[dict], as_json: bool):
    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return
    for r in rows:
        proj = f" [{r['project']}]" if r.get("project") else ""
        print(f"#{r['id']} {r['ts'][:19]} {r['type']}{proj}: {r['summary']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="brain", description="Local Claude cross-session brain")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("record")
    pr.add_argument("--source", default="claude-code")
    pr.add_argument("--type", required=True)
    pr.add_argument("--summary", required=True)
    pr.add_argument("--project")
    pr.add_argument("--actor")
    pr.add_argument("--session")
    pr.add_argument("--confidence", type=float, default=0.5)
    pr.add_argument("--key", help="subject key; a newer entry with the same key supersedes the older")
    pr.add_argument("--payload", help="JSON object of extra fields")

    pq = sub.add_parser("query")
    pq.add_argument("--source"); pq.add_argument("--type"); pq.add_argument("--project")
    pq.add_argument("--actor"); pq.add_argument("--since"); pq.add_argument("--until")
    pq.add_argument("--limit", type=int, default=50); pq.add_argument("--json", action="store_true")
    pq.add_argument("--all", action="store_true", help="include superseded")

    ps = sub.add_parser("search")
    ps.add_argument("text"); ps.add_argument("--project")
    ps.add_argument("--limit", type=int, default=50); ps.add_argument("--json", action="store_true")

    pc = sub.add_parser("recall")
    pc.add_argument("text", nargs="?")
    pc.add_argument("--project"); pc.add_argument("--budget", type=int, default=2048)
    pc.add_argument("--session"); pc.add_argument("--layer", default="digest",
                                                  choices=["digest", "timeline"])
    pc.add_argument("--no-log", action="store_true")

    sub.add_parser("stats")
    sub.add_parser("init")
    pp = sub.add_parser("promote")
    pp.add_argument("--min-score", type=float, default=0.45)
    pp.add_argument("--limit", type=int, default=20)
    pd = sub.add_parser("daemon"); pd.add_argument("action",
                                                   choices=["start", "stop", "restart", "status", "run"])
    pw = sub.add_parser("web"); pw.add_argument("--open", action="store_true")
    ph = sub.add_parser("hook"); ph.add_argument(
        "event", choices=["session_start", "session_end", "pre_compact", "post_compact",
                          "user_prompt_submit"])

    pel = sub.add_parser("encode-list"); pel.add_argument("--json", action="store_true")
    ped = sub.add_parser("encode-done")
    ped.add_argument("--ids", required=True, help="comma-separated event ids reviewed this pass")

    a = p.parse_args(argv)

    if a.cmd == "record":
        payload = json.loads(a.payload) if a.payload else None
        nid = record(source=a.source, type=a.type, summary=a.summary, project=a.project,
                     actor=a.actor, session_id=a.session, confidence=a.confidence,
                     key=a.key, payload=payload)
        print(f"recorded #{nid}")
    elif a.cmd == "query":
        _print_events(query(source=a.source, type=a.type, project=a.project, actor=a.actor,
                            since=a.since, until=a.until, limit=a.limit,
                            include_superseded=a.all), a.json)
    elif a.cmd == "search":
        _print_events(search(a.text, project=a.project, limit=a.limit), a.json)
    elif a.cmd == "recall":
        out = recall(project=a.project, query_text=a.text, budget=a.budget,
                     session_id=a.session, layer=a.layer, log=not a.no_log)
        if out:
            print(out)
    elif a.cmd == "stats":
        print(json.dumps(stats(), indent=2))
    elif a.cmd == "promote":
        cands = promote(min_score=a.min_score, limit=a.limit)
        if not cands:
            print("No promotion candidates yet — memories gain score as they're recalled "
                  "across sessions. Re-check after the brain has been used a while.")
        for c in cands:
            proj = f" [{c['project']}]" if c.get("project") else ""
            print(f"#{c['id']} score={c['quality_score']:.2f} recalls={c['recalls']} "
                  f"{c['type']}{proj}: {c['summary']}")
    elif a.cmd == "init":
        print("FTS5 enabled" if init_db() else "FTS5 unavailable (LIKE fallback)")
    elif a.cmd == "daemon":
        print(daemon_ctl(a.action))
    elif a.cmd == "web":
        url = f"http://127.0.0.1:{WEB_PORT}/"
        if a.open:
            subprocess.run(["open", url])
        print(url)
    elif a.cmd == "hook":
        return _cmd_hook(a.event)
    elif a.cmd == "encode-list":
        items = encode_list()
        if a.json:
            print(json.dumps(items, indent=2, default=str))
        else:
            for it in items:
                proj = f" [{it['project']}]" if it.get("project") else ""
                print(f"#{it['id']} {it['ts'][:19]} {it['type']}{proj}: {it['summary']}")
            print(f"\n{len(items)} event(s) pending consolidation")
    elif a.cmd == "encode-done":
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
        print(f"marked {encode_done(ids)} event(s) consolidated; encode flag cleared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
