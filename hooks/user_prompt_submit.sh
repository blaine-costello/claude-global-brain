#!/bin/bash
# Claude brain user_prompt_submit hook — fail-open: never block or fail a turn.
# Surfaces a /brain-encode nudge mid-session when the daemon has flagged a backlog.
"$HOME/.claude/brain/brain" hook user_prompt_submit 2>/dev/null
exit 0
