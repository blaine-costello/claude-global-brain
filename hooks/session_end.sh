#!/bin/bash
# Claude brain session_end hook — fail-open: never block or fail a session.
"$HOME/.claude/brain/brain" hook session_end 2>/dev/null
exit 0
