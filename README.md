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
- **Consolidation ("encoding")** — over time raw events pile up and duplicate. A periodic **`/brain-encode`** pass distills clusters of related events into a few sharp `consolidated` memories (superseding the noisy sources, which keeps recall high-signal). The daemon only *watches* and raises a **🧠 nudge** when a backlog is due — the judgment runs in your Claude session, so no extra LLM or API key. See [Consolidation](#consolidation--encoding-memory).
- **Optional background daemon** (`braind`) — a local web UI to browse memories + themes, and a **Wiki** tab that renders your encoded knowledge as **linked documentation**: per-project docs with `🧠 Encoded knowledge` up top, each consolidated memory linking back to the raw events it merged. Everything works **even if the daemon is down** — the CLI and hooks talk to SQLite directly.
- **Fail-open** — every hook exits 0 no matter what; a broken or missing brain never blocks or breaks a Claude session.

## Install

> **The easy way:** in Claude Code, just say *"clone https://github.com/blaine-costello/claude-global-brain and set up the brain by following its README."* The steps below are self-contained enough for Claude to run end-to-end — clone, `./install.sh` (which wires the hooks for you), put the CLI on PATH, and append the protocol.

```bash
git clone https://github.com/blaine-costello/claude-global-brain.git
cd claude-global-brain
./install.sh
```

`install.sh` copies the framework into `~/.claude/brain/`, initializes the database (it never touches an existing `brain.db`), installs three slash-commands — **`/remember`**, **`/recall`**, and **`/brain-encode`** — into `~/.claude/skills/`, and **auto-wires the lifecycle hooks** into `~/.claude/settings.json` (idempotent; it backs the file up to `settings.json.bak` first). Then put the `brain` launcher on your PATH:

```bash
ln -s ~/.claude/brain/brain /usr/local/bin/brain   # or add ~/.claude/brain to $PATH
```

Restart Claude Code so the hooks load. That's the whole setup.

### Wire the hooks (manual fallback)

`install.sh` does this for you. If you'd rather wire them by hand — or its auto-merge skipped because your `settings.json` wasn't valid JSON — add this to `~/.claude/settings.json` (merge into an existing `hooks` block if you have one):

```json
{
  "hooks": {
    "SessionStart":     [{ "hooks": [{ "type": "command", "command": "~/.claude/brain/hooks/session_start.sh" }] }],
    "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "~/.claude/brain/hooks/session_end.sh" }] }],
    "PreCompact":       [{ "hooks": [{ "type": "command", "command": "~/.claude/brain/hooks/pre_compact.sh" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "~/.claude/brain/hooks/user_prompt_submit.sh" }] }]
  }
}
```

The `UserPromptSubmit` hook is what surfaces the **🧠 consolidation-due nudge** mid-session (see [Consolidation](#consolidation--encoding-memory)).

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
brain encode-list [--json]                      # the backlog a consolidation pass would review
brain encode-done --ids <a,b,c>                 # mark a reviewed batch (closes the pass; clears the nudge)
brain web --open                                # browse at http://127.0.0.1:8787 (Wiki tab = encoded knowledge)
brain daemon start | stop | restart | status
```

**Types:** `preference` `convention` `decision` `gotcha` `fix` `bug`. (`preference`/`convention` with the same subject auto-supersede the older entry.)

`--project` scopes a memory to a repository (use the repo's basename). Recall is repo-scoped by default, so different repos' knowledge is never conflated; omit `--project` only for genuinely universal facts.

### Slash-commands

`install.sh` drops two skills into `~/.claude/skills/` so you (and Claude) drive the brain conversationally — no need to remember the CLI:

- **`/remember`** — *"remember that we use Stripe for billing"* → records a scoped, typed memory.
- **`/recall`** — *"what do we know about the deploy flow?"* → pulls the relevant prior context.
- **`/brain-encode`** — distills the raw-event backlog into sharp `consolidated` memories. Run it when the **🧠 consolidation-due nudge** appears, or just say *"encode the brain"* / *"consolidate memory"*.

Claude also invokes them proactively when you say "remember this…", ask "have we dealt with X before?", or see the consolidation nudge.

## Consolidation — encoding memory

Recall stays sharp only if the event log doesn't grow into a pile of near-duplicates. **Encoding** is the cleanup pass that keeps it lean — and it's a deliberate *hybrid*: the daemon does the cheap watching, your Claude session does the judgment.

- **The daemon watches (no LLM).** On each maintenance cycle `braind` counts the live, not-yet-reviewed events and writes a small flag when a pass is due — by default **≥ 40 events** queued, or **> 24h** since the last encode with a non-trivial backlog (tune via `CLAUDE_BRAIN_ENCODE_MIN` / `CLAUDE_BRAIN_ENCODE_HOURS`).
- **The session encodes (one bounded pass).** When the flag is set, the `SessionStart` and `UserPromptSubmit` hooks surface a **`🧠 brain: consolidation due`** nudge. Running **`/brain-encode`** then reads the backlog (`brain encode-list`), clusters and distills it into a few `consolidated` memories that supersede their noisy sources, and closes the pass (`brain encode-done`). It's a single read→distill→write pass — *not* an agentic loop, and it uses no API key (the judgment rides the session you're already in).
- **The payoff is visible.** Encoded memories become the headline of the **Wiki** tab in `brain web` — per-project `🧠 Encoded knowledge` docs whose every entry links back (`↳ merges #…`) to the raw events it absorbed, so the distillation stays auditable.

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
| `braind.py` | Optional daemon: local web UI + JSON API + background consolidation-watch / wiki / retention |
| `schema.sql` | SQLite schema (event log + FTS5 + decay/usage + supersession/consolidation columns) — applied idempotently |
| `hooks/*.sh` | Fail-open Claude Code lifecycle hooks (`session_start`, `session_end`, `pre_compact`, `user_prompt_submit`) |
| `frontend/index.html` | The single-file web UI served by `braind` — Overview / Recent / Search / **Wiki** (linked encoded knowledge) |
| `brain` | Launcher that resolves a python3 robustly and runs `brain.py` |
| `skills/{remember,recall,brain-encode}/` | `/remember` + `/recall` + `/brain-encode` slash-commands — the conversational front-end (installed to `~/.claude/skills/`) |

## License

[MIT](LICENSE).
