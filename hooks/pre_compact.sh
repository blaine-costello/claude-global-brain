#!/bin/bash
# Claude brain pre_compact hook — fail-open: never block or fail a session.
"$HOME/.claude/brain/brain" hook pre_compact 2>/dev/null
exit 0
