#!/bin/bash
# Claude brain session_start hook — fail-open: never block or fail a session.
"$HOME/.claude/brain/brain" hook session_start 2>/dev/null
exit 0
