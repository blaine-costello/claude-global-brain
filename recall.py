"""Ranking + rendering for brain recall (adapted from a production agent's context-recall layer).

Pure functions over event-row dicts (as returned by brain.query/search) — no DB
access here, so brain.py orchestrates and this stays the policy layer.

Ranking = confidence × time-decay (per-type Ebbinghaus half-life) × usage-quality
× project-scope boost, then fatigue-dampened so a few loud types don't crowd out
rare-but-useful signal. Rendering is progressive-disclosure:
  layer 1 "digest"   — compact, token-budgeted index (SessionStart injection)
  layer 2 "timeline" — grouped by day
  layer 3 "detail"   — full payload (handled by brain.py `query --id`)
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

# Per-type half-life in DAYS (how long until a memory's weight halves).
HALFLIFE_DAYS: dict[str, float] = {
    "preference": 365.0,
    "convention": 240.0,
    "decision": 120.0,
    "fix": 120.0,
    "bug": 365.0,
    "bug.found": 365.0,
    "gotcha": 120.0,
    "consolidated": 365.0,
    "session.summary": 21.0,
    "observation": 30.0,
}
DEFAULT_HALFLIFE = 45.0


def _parse(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.now(timezone.utc)


def _age_days(ts: str | None, now: datetime) -> float:
    delta = now - _parse(ts)
    return max(0.0, delta.total_seconds() / 86400.0)


def score(row: dict, now: datetime, project: str | None,
          overrides: dict[str, float] | None = None) -> float:
    overrides = overrides or {}
    typ = row.get("type") or ""
    hl = overrides.get(typ) or HALFLIFE_DAYS.get(typ, DEFAULT_HALFLIFE)
    decay = 0.5 ** (_age_days(row.get("ts"), now) / hl)

    base = float(row.get("confidence") or 0.5)
    # quality_score is -3..+3 → multiplier 0.5..1.5
    qmult = 1.0 + max(-3.0, min(3.0, float(row.get("quality_score") or 0.0))) / 6.0

    rp = row.get("project")
    if project and rp == project:
        scope = 1.6           # this project's memories rank first
    elif not rp:
        scope = 1.0           # global memories are broadly relevant
    else:
        scope = 0.65          # other projects' memories are deprioritized
    return base * decay * qmult * scope


def rank(rows: list[dict], project: str | None,
         now: datetime | None = None, overrides: dict | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    scored = []
    for r in rows:
        r = dict(r)
        r["_score"] = score(r, now, project, overrides)
        scored.append(r)
    scored.sort(key=lambda r: r["_score"], reverse=True)
    # Fatigue: dampen the Nth item of a repeated type so loud buckets don't dominate.
    seen: dict[str, int] = {}
    for r in scored:
        typ = r.get("type") or ""
        n = seen.get(typ, 0)
        r["_score"] /= math.sqrt(n + 1)
        seen[typ] = n + 1
    scored.sort(key=lambda r: r["_score"], reverse=True)
    return scored


def _age_label(ts: str | None, now: datetime) -> str:
    d = _age_days(ts, now)
    if d < 1:
        return "today"
    if d < 2:
        return "yesterday"
    if d < 14:
        return f"{int(d)}d ago"
    if d < 60:
        return f"{int(d / 7)}w ago"
    return f"{int(d / 30)}mo ago"


def _line(r: dict, now: datetime) -> str:
    typ = (r.get("type") or "note").replace("_", " ")
    summary = (r.get("summary") or "").strip().replace("\n", " ")
    if len(summary) > 220:
        summary = summary[:217] + "…"
    proj = r.get("project")
    tag = f" `{proj}`" if proj else ""
    return f"- **[{typ}]**{tag} {summary} _({_age_label(r.get('ts'), now)})_"


def render_digest(rows: list[dict], project: str | None,
                  budget_bytes: int = 2048, now: datetime | None = None,
                  overrides: dict | None = None) -> tuple[str, list[int]]:
    """Layer-1 compact digest. Returns (markdown, selected_event_ids)."""
    now = now or datetime.now(timezone.utc)
    ranked = rank(rows, project, now, overrides)
    if not ranked:
        return "", []

    here = [r for r in ranked if project and r.get("project") == project]
    other = [r for r in ranked if r not in here]

    out: list[str] = ["## 🧠 Brain recall"]
    selected: list[int] = []
    used = len(out[0])

    def emit(header: str, items: list[dict]) -> None:
        nonlocal used
        if not items:
            return
        block: list[str] = []
        for r in items:
            ln = _line(r, now)
            if used + len(ln) + 1 > budget_bytes:
                break
            block.append(ln)
            used += len(ln) + 1
            if r.get("id") is not None:
                selected.append(int(r["id"]))
        if block:
            head = f"\n### {header}"
            out.append(head)
            used += len(head)
            out.extend(block)

    emit(f"This project — {project}" if project else "Recent", here)
    emit("Across projects", other)
    out.append("\n_recall more: `brain query --project <p>` · `brain recall <topic>`_")
    return "\n".join(out), selected


def render_timeline(rows: list[dict], now: datetime | None = None) -> str:
    """Layer-2: events grouped by day (most recent first)."""
    now = now or datetime.now(timezone.utc)
    by_day: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r.get("ts") or "", reverse=True):
        day = (r.get("ts") or "")[:10] or "unknown"
        by_day.setdefault(day, []).append(r)
    out: list[str] = []
    for day, items in by_day.items():
        out.append(f"\n### {day}")
        for r in items:
            out.append(_line(r, now))
    return "\n".join(out).strip()
