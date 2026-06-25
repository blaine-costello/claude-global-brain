---
name: brain-encode
description: Consolidate ("encode") the machine-wide brain's backlog of raw cross-session events into a smaller set of high-signal, deduplicated knowledge memories. Run when you see the "🧠 brain: consolidation due" nudge (surfaced at session start or mid-session by the brain daemon's threshold watch), or when the user says "encode the brain", "consolidate memory", "run a brain encoding", "distill the events". This is the human/Claude side of the hybrid (Option C) design: the `braind` daemon only *watches* and flags a backlog (≥40 events or >24h since last encode); the actual semantic distillation rides this Claude Code session — no separate agent or API key. It is a single bounded pass (read backlog → distill → write back), not an agentic loop.
---

# brain-encode

Distill the brain's accumulated raw events into fewer, sharper memories so `recall`
serves high-signal knowledge instead of a growing pile of duplicates. The daemon
(`braind.py`) flags when a pass is due; you do the judgment work here.

## When to run
- The **`🧠 brain: consolidation due`** nudge appeared (session start or mid-session).
- Or on request ("encode the brain", "consolidate memory").
- Don't run if `brain encode-list` returns nothing.

## Steps

1. **Read the backlog** (live, not-yet-consolidated, non-transient events):
   ```bash
   brain encode-list --json
   ```
   Each item has `id`, `ts`, `type`, `project`, `summary`, `confidence`.

2. **Cluster and distill.** Group by `project`, then by topic. For each group decide:
   - **Duplicates / subsumed** — several events saying the same thing, or an early
     event a later one supersedes → write **one** consolidated memory that captures the
     final truth, and list the source `id`s to supersede.
   - **A theme** — a cluster of related but distinct events (e.g. five fixes that are all
     "the X subsystem is fragile because Y") → write one higher-level `consolidated`
     memory that names the pattern; supersede the noisy members, keep any that carry
     unique specifics.
   - **Unique + durable** (a one-off gotcha/decision that stands alone) → **keep it**, don't
     supersede; it just gets marked reviewed in step 4.
   - **Transient** (verbose `session.summary`) → extract any durable fact into a proper
     `gotcha`/`decision`/`fix`, then let the raw summary be marked reviewed (it ages out).

   Distillation rules: **preserve durable facts; reference by pointer** (file:line, PR#,
   commit, key names) — don't paste content. Be **conservative about superseding**: only
   supersede genuine duplicates/subsumed events, never a unique fact. Aim to cut the
   live-event count meaningfully while losing no real knowledge.

3. **Write the consolidations.** For each one (`<type>` is usually `consolidated`, or a
   precise `gotcha`/`decision`/`convention` when distilling a single theme):
   ```bash
   brain record --source claude-code --type consolidated \
     --summary "<one crisp, durable sentence>" \
     --project "<project or omit for global>" --confidence 0.85 \
     --payload '{"supersedes":[<source ids merged into this>]}'
   ```
   `payload.supersedes` atomically retires those source events from recall/wiki/promote.
   Use `--key <subject>` if it should supersede a prior same-subject memory too.

4. **Close the pass** — mark the *entire reviewed batch* (every id from step 1, including
   the ones you kept as-is) so they leave the pending set and the daemon clears its flag:
   ```bash
   brain encode-done --ids <comma,separated,all,reviewed,ids>
   ```

5. **Surface promotions (optional).** Strong consolidated items are CLAUDE.md / project-memory
   candidates: `brain promote` lists them (human-approved, never auto-applied).

6. **Report** concisely: N events in → M consolidations written, K superseded, the rest
   reviewed; flag cleared.

## Notes
- Pure distillation, single pass — no tools beyond `brain`, no iteration. (A future "encode +
  verify" mode that re-checks gotchas against the code *would* be an agentic loop; this isn't.)
- Thresholds are env-tunable on the daemon: `CLAUDE_BRAIN_ENCODE_MIN` (default 40 events),
  `CLAUDE_BRAIN_ENCODE_HOURS` (default 24h).
- Everything is local SQLite; safe to re-run. `encode-done` is idempotent.
