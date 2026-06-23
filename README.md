# Claude Global Brain

A persistent, **machine-wide memory** for [Claude Code](https://claude.com/claude-code) — so every session, and every parallel Claude instance on your machine, shares the same durable, repo-aware context instead of starting amnesiac.

100% local. SQLite + a few stdlib Python files. No network, no accounts, no external services.

---

## Why

Every Claude Code session starts from zero. You re-explain the same context, re-derive the same decisions, and lose hard-won gotchas the moment a session ends or compacts. And when you run **several Claude instances in parallel** — different terminals, git worktrees, sub-agents — they can't see each other's work at all.

The Global Brain fixes both:

- **Persistent** — decisions, gotchas, fixes, and preferences survive across sessions.
- **Shared across instances** — every Claude on the machine reads and writes the *same* brain, so parallel agents bridge context automatically (see [Bridging parallel agents](#bridging-parallel-agents--handoff)).
- **Repo-aware** — memories are scoped per repository, so one project's patterns never bleed into another's.
- **Automatic** — lifecycle hooks capture a summary at session end / before compaction and inject a relevant digest at session start. A CLI lets you (and Claude) record/recall on demand.

It pairs especially well with a **handoff** workflow: when one session is wrapping up — or about to compact — it records what matters, and the next session (or a parallel agent) picks it up cold-free.

## How it works

```
 Claude session A ─SessionStart─► inject ranked, repo-scoped digest ─┐
 Claude session B ─PreCompact───► record pre-compaction summary       │
 Claude session C ─SessionEnd───► record session summary             ─┤
        │                                                             ▼
        └─ you / Claude also call `brain record` / `brain recall` ──► ~/.claude/brain/brain.db
                                                                       (SQLite + FTS5, machine-wide)
                                                                            ▲
                            braind (optional daemon): local web UI ─────────┘
                            + background consolidation into wiki docs
```

- **Append-only SQLite event log** (`brain.db`) with **FTS5 full-text search**, confidence/usage **decay scoring**, and **key-based supersession** (a newer fact about the same subject supersedes the old one).
- **Redaction built in** — every value is scrubbed of secret shapes (API keys, tokens, AWS keys, JWTs, PEM private-key blocks) *before* it is stored. Wrap anything sensitive in `<private>…</private>` to drop it entirely.
- **3-layer progressive recall** — a compact, ranked, repo-scoped digest first; drill into detail on demand. Token-frugal by design.
- **Optional background daemon** (`braind`) — a local web UI to browse memories + themes, plus a consolidation pass that rolls clusters of memories into per-project/per-type wiki docs. Everything works **even if the daemon is down** — the CLI and hooks talk to SQLite directly.
- **Fail-open** — every hook exits 0 no matter what; a broken or missing brain never blocks or breaks a Claude session.

## Install

```bash
git clone https://github.com/blaine-costello/claude-global-brain.git
cd claude-global-brain
./install.sh
```

`install.sh` copies the framework into `~/.claude/brain/`, initializes the database (it never touches an existing `brain.db`), and installs two slash-commands — **`/remember`** and **`/recall`** — into `~/.claude/skills/`. Put the `brain` launcher on your PATH:

```bash
ln -s ~/.claude/brain/brain /usr/local/bin/brain   # or add ~/.claude/brain to $PATH
```

### Wire the hooks

Add this to `~/.claude/settings.json` so capture + recall happen automatically (merge into an existing `hooks` block if you have one):

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "~/.claude/brain/hooks/session_start.sh" }] }],
    "SessionEnd":   [{ "hooks": [{ "type": "command", "command": "~/.claude/brain/hooks/session_end.sh" }] }],
    "PreCompact":   [{ "hooks": [{ "type": "command", "command": "~/.claude/brain/hooks/pre_compact.sh" }] }]
  }
}
```

### Make Claude brain-aware (recommended)

Drop the protocol in [`CLAUDE.brain.md`](CLAUDE.brain.md) into your **global** `~/.claude/CLAUDE.md` so Claude proactively records high-signal facts and recalls relevant ones — turning the brain from passive storage into an active habit.

## Usage

```bash
brain record --type <type> --summary "<one line>" [--project <repo>] [--confidence 0..1]
brain recall "<topic>" [--project <repo>]      # ranked, decay-weighted, repo-scoped digest
brain query  [--project <repo>] [--type <t>]   # raw recent events
brain search "<text>"                           # FTS5 across everything
brain stats                                     # counts by project / type
brain promote                                   # surface recurring high-signal memories to lift into CLAUDE.md
brain web --open                                # browse at http://127.0.0.1:8787
brain daemon start | stop | restart | status
```

**Types:** `preference` `convention` `decision` `gotcha` `fix` `bug`. (`preference`/`convention` with the same subject auto-supersede the older entry.)

`--project` scopes a memory to a repository (use the repo's basename). Recall is repo-scoped by default, so different repos' knowledge is never conflated; omit `--project` only for genuinely universal facts.

### Slash-commands

`install.sh` drops two skills into `~/.claude/skills/` so you (and Claude) drive the brain conversationally — no need to remember the CLI:

- **`/remember`** — *"remember that we use Stripe for billing"* → records a scoped, typed memory.
- **`/recall`** — *"what do we know about the deploy flow?"* → pulls the relevant prior context.

Claude also invokes them proactively when you say "remember this…" or ask "have we dealt with X before?".

## Bridging parallel agents + handoff

This is where it earns its keep. Because the brain is one machine-wide store:

- **Parallel instances share a working memory.** A Claude in terminal A records a decision or a gotcha; a Claude in terminal B (or a git-worktree agent, or a sub-agent) recalls it moments later. You stop re-explaining the same context to every instance you spin up.
- **Handoffs become free.** When a long session is ending or about to compact, the `SessionEnd`/`PreCompact` hooks (or an explicit `brain record`) persist the state of play. The next session's `SessionStart` hook injects it back — so a fresh agent resumes without re-doing discovery. If you use a structured handoff step, point its summary at `brain record` and the receiving agent recalls it on start.
- **Knowledge compounds.** Decay + usage scoring keep what you actually use near the top and let stale one-offs fade; the consolidation pass distills recurring memories into durable docs. The longer you run it, the more leverage it returns.

## Privacy

- **Local-only.** Nothing leaves your machine. The optional daemon binds to `127.0.0.1`.
- **Secrets are auto-redacted** before storage. Still, wrap anything sensitive in `<private>…</private>` to exclude it entirely.
- **Per-repo opt-out.** Drop a `.brain-disabled` file at a repository root to exclude that project from capture.

## Layout

| File | Role |
|---|---|
| `brain.py` | Core module + CLI: `record` / `query` / `search` / `recall` / `stats` / `promote` / `daemon` / `web` / `hook` |
| `recall.py` | Ranking + 3-layer progressive-disclosure rendering (the recall policy layer) |
| `redact.py` | Secret redaction + `<private>` handling — every write passes through it |
| `braind.py` | Optional daemon: local web UI + JSON API + background consolidation/retention |
| `schema.sql` | SQLite schema (event log + FTS5 + decay/usage columns) — applied idempotently |
| `hooks/*.sh` | Fail-open Claude Code lifecycle hooks |
| `frontend/index.html` | The single-file web UI served by `braind` |
| `brain` | Launcher that resolves a python3 robustly and runs `brain.py` |
| `skills/{remember,recall}/` | `/remember` + `/recall` slash-commands — the conversational front-end (installed to `~/.claude/skills/`) |

## License

[MIT](LICENSE).
