---
name: recall
description: Search the local machine-wide brain for relevant past context — prior decisions, gotchas, fixes, conventions, or preferences from earlier Claude sessions. Use when the user asks "what do we know about X", "have we dealt with X before", "what did we decide about Y", "any prior context on Z", or whenever earlier-session context would help the current task.
---

# recall

Pull relevant memories from the machine-wide brain (earlier sessions, across projects).

## How

```
# topic search (ranked, decay-weighted, project-scoped):
~/.claude/brain/brain recall "<topic>" --project <slug>

# broader keyword search across everything:
~/.claude/brain/brain search "<text>"

# everything for the current project / a type:
~/.claude/brain/brain query --project <slug> [--type decision] [--limit N]
```

- Infer **--project** from `git rev-parse --show-toplevel` (basename) or cwd; omit to search globally.
- `recall` returns a compact, ranked digest; `search`/`query` return raw matching events.

## Then

- Summarize what's relevant to the current task in 1–4 lines; cite the memory by `#id` if useful.
- If nothing relevant comes back, say so briefly and proceed — don't fabricate.
- Note: the brain already auto-injects a digest at session start, so use this skill for
  *targeted* lookups on the current topic, not a blanket dump.

## Browse

For a visual overview (themes, timeline, consolidated knowledge docs):
`~/.claude/brain/brain web --open` → http://127.0.0.1:8787
