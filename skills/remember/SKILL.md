---
name: remember
description: Save a durable cross-session memory to the local machine-wide brain so future Claude sessions recall it. Use when the user says "remember this", "save this for later", "note this for the future", "don't forget X", or when you learn a lasting preference, decision, convention, gotcha, or non-obvious fix worth carrying across sessions.
---

# remember

Persist one high-signal fact to the machine-wide brain so it surfaces in future sessions.

## How

Run the brain CLI (works from any directory):

```
~/.claude/brain/brain record --type <type> --summary "<one concise line>" [--project <slug>] [--confidence 0.0-1.0]
```

- **type**: one of `preference` `convention` `decision` `gotcha` `fix` `bug`.
  (`preference`/`convention` auto-supersede older same-scope entries — newer wins.)
- **--project** (repo-scope): the current repo's basename, from `git rev-parse --show-toplevel`.
  Tag **all** repo-specific facts and design patterns with `--project` so they stay scoped to that
  repo and are never conflated with another's. Omit it ONLY for genuinely cross-project/universal
  facts. (The brain also records the exact repo root + remote on hook-captured events.)
- **--confidence**: how sure / how durable (default 0.5; use 0.8–0.9 for stated preferences and firm decisions).
- **--summary**: ONE line, specific and self-contained — it's what recall shows first.

## Rules

- Record sparingly and specifically: decisions + rationale, non-obvious gotchas/fixes, stated
  preferences/conventions. Skip routine steps and anything obvious from the code.
- **Privacy**: secrets are auto-redacted, but if the fact contains anything sensitive (keys,
  tokens, personal data), wrap that part in `<private>…</private>` or don't record it. The brain
  spans sensitive repos.
- After recording, confirm briefly to the user (the CLI prints `recorded #<id>`).

## Examples

- "Remember we deploy the web service as linux/arm64" →
  `~/.claude/brain/brain record --type convention --project <repo> --confidence 0.9 --summary "Deploy the web service as linux/arm64 (image + runtime platform)"`
- "We decided to use Stripe (not Square) for billing" →
  `~/.claude/brain/brain record --type decision --project <repo> --confidence 0.9 --summary "Billing uses Stripe, not Square"`
- "Note for later: prefer terse PR descriptions" →
  `~/.claude/brain/brain record --type preference --confidence 0.85 --summary "Prefers terse PR descriptions"`
