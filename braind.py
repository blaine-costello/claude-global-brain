#!/usr/bin/env python3
"""braind — local Claude brain daemon.

Two jobs, both local-only:
  1. Serve a web frontend + JSON API on 127.0.0.1 (browse memories, themes,
     consolidated knowledge docs).
  2. Run periodic background maintenance: regenerate the consolidated wiki,
     update usage-quality scores, and a conservative retention sweep.

The CLI + hooks talk to SQLite directly, so the brain works even when this
daemon is down — the daemon only *adds* the UI + background consolidation.
Launched by launchd (com.<user>.claude-brain); control via `brain daemon ...`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brain  # noqa: E402

WIKI = HERE / "wiki"
FRONTEND = HERE / "frontend" / "index.html"
LOG = HERE / "braind.log"
PORT = brain.WEB_PORT
CYCLE = int(os.environ.get("CLAUDE_BRAIN_CYCLE", "1800"))  # background pass every 30 min

# Types that form the browsable knowledge base (vs transient noise).
KNOWLEDGE_TYPES = ("preference", "convention", "decision", "gotcha", "fix", "bug", "bug.found")
NOISE_RETENTION_DAYS = {"context.checkpoint": 30, "session.summary": 90}


def log(msg: str) -> None:
    try:
        with open(LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")
    except Exception:
        pass


# ----------------------------------------------------------- background jobs

def _slug(s: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s or "global").strip("-") or "global"


def regenerate_wiki() -> int:
    """Rebuild ~/.claude/brain/wiki/<project>__<type>.md from live knowledge events.
    Non-destructive (derived view); always reflects current memory."""
    WIKI.mkdir(parents=True, exist_ok=True)
    conn = brain._connect()
    try:
        rows = conn.execute(
            "SELECT id, ts, type, project, payload_json FROM events "
            "WHERE superseded_by IS NULL AND type IN (%s) ORDER BY ts DESC"
            % ",".join("?" * len(KNOWLEDGE_TYPES)),
            KNOWLEDGE_TYPES,
        ).fetchall()
    finally:
        conn.close()
    groups: dict[tuple[str, str], list] = {}
    for r in rows:
        proj = r["project"] or "(global)"
        groups.setdefault((proj, r["type"]), []).append(r)
    # Clear stale generated files, then rewrite.
    for old in WIKI.glob("*.md"):
        old.unlink()
    written = 0
    index: dict[str, list[str]] = {}
    for (proj, typ), items in sorted(groups.items()):
        fname = f"{_slug(proj)}__{_slug(typ)}.md"
        lines = [f"# {proj} — {typ}  ({len(items)})", ""]
        for r in items:
            try:
                summary = json.loads(r["payload_json"]).get("summary", "")
            except Exception:
                summary = ""
            lines.append(f"- {summary}  _(#{r['id']}, {r['ts'][:10]})_")
        (WIKI / fname).write_text("\n".join(lines) + "\n")
        index.setdefault(proj, []).append(typ)
        written += 1
    (WIKI / "_index.json").write_text(json.dumps(index, indent=2))
    return written


def update_scores() -> int:
    """quality_score rises for memories repeatedly recalled (a relevance proxy)."""
    conn = brain._connect()
    try:
        conn.execute(
            "UPDATE events SET quality_score = MIN(3.0, 0.15 * ("
            "  SELECT COUNT(*) FROM injection_log WHERE injection_log.event_id = events.id"
            ")) WHERE id IN (SELECT DISTINCT event_id FROM injection_log)"
        )
        n = conn.execute("SELECT changes()").fetchone()[0]
    finally:
        conn.close()
    return n


def retention_sweep() -> int:
    """Conservative: cull transient noise + long-superseded events. Keep knowledge."""
    conn = brain._connect()
    deleted = 0
    try:
        now = datetime.now(timezone.utc).timestamp()
        to_del: list[int] = []
        for typ, days in NOISE_RETENTION_DAYS.items():
            cutoff = datetime.fromtimestamp(now - days * 86400, timezone.utc).isoformat()
            rows = conn.execute(
                "SELECT id FROM events WHERE type=? AND ts < ?", (typ, cutoff)
            ).fetchall()
            to_del += [r[0] for r in rows]
        cutoff90 = datetime.fromtimestamp(now - 90 * 86400, timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT id FROM events WHERE superseded_by IS NOT NULL AND ts < ?", (cutoff90,)
        ).fetchall()
        to_del += [r[0] for r in rows]
        for eid in set(to_del):
            conn.execute("DELETE FROM events WHERE id=?", (eid,))
            try:
                conn.execute("DELETE FROM events_fts WHERE rowid=?", (eid,))
            except Exception:
                pass
            deleted += 1
    finally:
        conn.close()
    return deleted


def run_maintenance() -> None:
    try:
        w = regenerate_wiki()
        s = update_scores()
        d = retention_sweep()
        log(f"maintenance: wiki={w} scored={s} pruned={d}")
    except Exception:
        log("maintenance error:\n" + traceback.format_exc())


def background_loop() -> None:
    time.sleep(2)
    while True:
        run_maintenance()
        time.sleep(CYCLE)


# ----------------------------------------------------------- HTTP API helpers

def _aggregates() -> dict:
    conn = brain._connect()
    try:
        by_type = conn.execute(
            "SELECT type, COUNT(*) n FROM events WHERE superseded_by IS NULL "
            "GROUP BY type ORDER BY n DESC"
        ).fetchall()
        by_proj = conn.execute(
            "SELECT IFNULL(project,'(global)') p, COUNT(*) n FROM events "
            "WHERE superseded_by IS NULL GROUP BY project ORDER BY n DESC"
        ).fetchall()
        by_day = conn.execute(
            "SELECT substr(ts,1,10) d, COUNT(*) n FROM events "
            "WHERE ts >= date('now','-30 day') GROUP BY d ORDER BY d"
        ).fetchall()
    finally:
        conn.close()
    return {
        "by_type": [{"type": r[0], "n": r[1]} for r in by_type],
        "by_project": [{"project": r[0], "n": r[1]} for r in by_proj],
        "by_day": [{"day": r[0], "n": r[1]} for r in by_day],
    }


def _wiki_list() -> list[dict]:
    out = []
    if WIKI.exists():
        for f in sorted(WIKI.glob("*.md")):
            proj, _, rest = f.stem.partition("__")
            out.append({"file": f.name, "project": proj, "type": rest,
                        "bytes": f.stat().st_size})
    return out


def _wiki_doc(name: str) -> str | None:
    # path-traversal safe: only allow simple *.md basenames inside WIKI
    if "/" in name or ".." in name or not name.endswith(".md"):
        return None
    p = WIKI / name
    return p.read_text() if p.exists() else None


# ----------------------------------------------------------- HTTP handler

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence default stderr logging
        pass

    def _send(self, code: int, body, ctype: str = "application/json"):
        if ctype == "application/json":
            body = json.dumps(body, default=str).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            path = u.path
            if path == "/" or path == "/index.html":
                if FRONTEND.exists():
                    return self._send(200, FRONTEND.read_text(), "text/html; charset=utf-8")
                return self._send(200, "<h1>brain</h1><p>frontend not installed</p>", "text/html")
            if path == "/api/stats":
                return self._send(200, brain.stats())
            if path == "/api/themes":
                return self._send(200, _aggregates())
            if path == "/api/recent":
                limit = int(q.get("limit", ["50"])[0])
                proj = q.get("project", [None])[0]
                return self._send(200, brain.query(project=proj, limit=limit))
            if path == "/api/search":
                text = q.get("q", [""])[0]
                proj = q.get("project", [None])[0]
                return self._send(200, brain.search(text, project=proj, limit=60))
            if path == "/api/event":
                eid = q.get("id", [None])[0]
                rows = brain.query(limit=1, include_superseded=True) if eid is None else \
                    [e for e in brain.query(limit=100000, include_superseded=True) if str(e["id"]) == str(eid)]
                return self._send(200, rows[0] if rows else {})
            if path == "/api/recall":
                proj = q.get("project", [None])[0]
                return self._send(200, {"digest": brain.recall(project=proj, log=False)})
            if path == "/api/wiki":
                return self._send(200, _wiki_list())
            if path.startswith("/api/wiki/"):
                doc = _wiki_doc(urllib.parse.unquote(path[len("/api/wiki/"):]))
                return self._send(200 if doc is not None else 404,
                                  {"content": doc} if doc is not None else {"error": "not found"})
            return self._send(404, {"error": "not found"})
        except Exception:
            log("request error:\n" + traceback.format_exc())
            return self._send(500, {"error": "internal"})


def main() -> int:
    brain.init_db()
    WIKI.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=background_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"braind listening on http://127.0.0.1:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
