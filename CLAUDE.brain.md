# Brain protocol

> Drop this into your **global** `~/.claude/CLAUDE.md` (or import it) so Claude
> uses the brain proactively instead of treating it as passive storage.

---

## The brain — machine-wide cross-session memory

You have a persistent, machine-wide memory — the **brain** — shared across every
Claude Code session and every parallel instance on this machine. CLI: `brain`
(at `~/.claude/brain/brain`; put it on PATH). It's local-only SQLite. Lifecycle
hooks auto-inject a recall digest at session start and auto-capture a summary at
session end / before compaction; everything works even if the daemon is down.

**Record high-signal memories as you work** — when something is worth knowing in
a future session, not routine steps:

```
brain record --type <type> --summary "<one line>" [--project <repo-basename>] [--confidence 0..1]
```

- **Types:** `preference` `convention` `decision` `gotcha` `fix` `bug`. A
  `preference`/`convention` about the same subject supersedes the older one.
- **Repo-scope by default.** Pass `--project <repo basename>` for anything
  repo-specific so it never bleeds into another project. Omit it ONLY for
  genuinely universal facts. Recall is repo-scoped, so different repos' knowledge
  is never conflated.
- **Record sparingly and specifically:** decisions + rationale, non-obvious
  gotchas/fixes, stated preferences. Skip the obvious and routine steps.

**Recall on demand:**
`brain recall "<topic>" [--project <repo>]` · `brain query [--project <repo>]` ·
`brain search "<text>"`. A digest also auto-injects at session start — use these
for *targeted* lookups on the current topic.

**Consolidation (keep recall sharp):** when you see a **`🧠 brain: consolidation
due`** nudge (surfaced at session start or mid-session), or the user says "encode
the brain" / "consolidate memory", run the **`/brain-encode`** skill. It's one
bounded pass — read the backlog (`brain encode-list`), distill clusters of related
events into a few `consolidated` memories that supersede their noisy sources, then
close it (`brain encode-done`). No API key, no agentic loop; the judgment rides
this session. Skip it if `brain encode-list` is empty.

**Privacy (hard rule):** secrets are auto-redacted, but wrap anything sensitive
in `<private>…</private>` to exclude it entirely. A `.brain-disabled` file at a
repo root opts that project out of capture.

**Bridging + handoff:** because the brain is machine-wide, parallel instances
share context — record a decision in one and recall it in another. When a session
is ending or about to compact, record the state of play so the next session (or a
parallel agent) resumes cold-free.
