#!/bin/bash
# Claude brain post_compact hook — fail-open: never block or fail a session.
"$HOME/.claude/brain/brain" hook post_compact 2>/dev/null
exit 0
