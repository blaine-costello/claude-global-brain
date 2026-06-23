#!/usr/bin/env bash
# Install the Claude Global Brain into ~/.claude/brain.
# Idempotent + safe: copies the framework only — never touches an existing
# brain.db, wiki/, or logs.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude/brain"

mkdir -p "$DEST/hooks" "$DEST/frontend"
cp "$SRC"/brain "$SRC"/brain.py "$SRC"/braind.py "$SRC"/recall.py "$SRC"/redact.py "$SRC"/schema.sql "$DEST/"
cp "$SRC"/hooks/*.sh "$DEST/hooks/"
cp "$SRC"/frontend/index.html "$DEST/frontend/"
chmod +x "$DEST/brain" "$DEST"/hooks/*.sh

# Slash-command skills (/remember, /recall) — the conversational front-end to the brain.
if [ -d "$SRC/skills" ]; then
  mkdir -p "$HOME/.claude/skills"
  cp -R "$SRC"/skills/. "$HOME/.claude/skills/"
  echo "  • installed /remember + /recall skills to ~/.claude/skills/"
fi

# Initialize the schema (idempotent — preserves any existing memories).
"$DEST/brain" init >/dev/null 2>&1 || "$DEST/brain" init || true

echo
echo "✓ Installed to $DEST"
echo
echo "Next steps:"
echo "  1. Put the CLI on your PATH:"
echo "       ln -s \"$DEST/brain\" /usr/local/bin/brain   # or add $DEST to \$PATH"
echo "  2. Wire the hooks into ~/.claude/settings.json — see README 'Wire the hooks'."
echo "  3. (recommended) Append CLAUDE.brain.md to your ~/.claude/CLAUDE.md."
echo "  4. (optional) Start the web UI:  brain daemon start   # http://127.0.0.1:8787"
echo
echo "Smoke test:"
echo "  $DEST/brain record --type decision --summary 'installed the global brain'"
echo "  $DEST/brain recall 'brain'"
