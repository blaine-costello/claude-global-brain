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

# Slash-command skills (/remember, /recall, /brain-encode) — the conversational front-end.
if [ -d "$SRC/skills" ]; then
  mkdir -p "$HOME/.claude/skills"
  cp -R "$SRC"/skills/. "$HOME/.claude/skills/"
  echo "  • installed /remember, /recall, /brain-encode skills to ~/.claude/skills/"
fi

# Initialize the schema (idempotent — preserves any existing memories).
"$DEST/brain" init >/dev/null 2>&1 || "$DEST/brain" init || true

# Wire the lifecycle hooks into ~/.claude/settings.json (idempotent; backs up first).
# Fail-soft: a parse error leaves settings.json untouched and prints the manual fallback.
SETTINGS="$HOME/.claude/settings.json"
if python3 - "$SETTINGS" "$DEST" <<'PY'
import json, os, shutil, sys
settings_path, dest = sys.argv[1], sys.argv[2]
hooks_dir = os.path.join(dest, "hooks")
wanted = {"SessionStart": "session_start.sh", "SessionEnd": "session_end.sh",
          "PreCompact": "pre_compact.sh", "UserPromptSubmit": "user_prompt_submit.sh"}
try:
    with open(settings_path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}
except Exception as e:
    print(f"  ! {settings_path} is not valid JSON ({e}); left untouched", file=sys.stderr)
    sys.exit(1)
os.makedirs(os.path.dirname(settings_path), exist_ok=True)
hooks = data.setdefault("hooks", {})
added = []
for event, script in wanted.items():
    entries = hooks.setdefault(event, [])
    present = any(script in h.get("command", "")
                  for g in entries if isinstance(g, dict)
                  for h in g.get("hooks", []) if isinstance(h, dict))
    if not present:
        entries.append({"hooks": [{"type": "command", "command": os.path.join(hooks_dir, script)}]})
        added.append(event)
if added:
    if os.path.exists(settings_path):
        shutil.copy2(settings_path, settings_path + ".bak")
    with open(settings_path, "w") as f:
        json.dump(data, f, indent=2)
    print("  • wired hooks into settings.json: " + ", ".join(added))
else:
    print("  • hooks already wired in settings.json")
PY
then :; else
  echo "  ! hook auto-wiring skipped — add them manually (README › Wire the hooks)"
fi

# Install the launchd plist for the optional daemon so `brain daemon start` works
# first-time (the daemon — web UI + consolidation-watch — is launchd-managed, macOS only).
# Idempotent (never clobbers an existing plist) + fail-soft (never aborts the install).
if [ "$(uname)" = "Darwin" ]; then
  if python3 - "$DEST" <<'PY'
import getpass, os, sys
dest = sys.argv[1]
label = f"com.{getpass.getuser()}.claude-brain"          # must match brain.py PLIST_LABEL
plist_dir = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")
plist_path = os.path.join(plist_dir, f"{label}.plist")
if os.path.exists(plist_path):
    print(f"  • launchd plist already present: {plist_path}")
    sys.exit(0)
brain = os.path.join(dest, "brain")
plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{brain}</string>
        <string>daemon</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{dest}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{os.path.join(dest, "braind.out.log")}</string>
    <key>StandardErrorPath</key>
    <string>{os.path.join(dest, "braind.err.log")}</string>
</dict>
</plist>
"""
os.makedirs(plist_dir, exist_ok=True)
with open(plist_path, "w") as f:
    f.write(plist)
print(f"  • installed launchd plist: {plist_path}")
PY
  then :; else
    echo "  ! launchd plist install skipped — 'brain daemon start' will report it missing"
  fi
fi

echo
echo "✓ Installed to $DEST"
echo
echo "Next steps:"
echo "  1. Put the CLI on your PATH:"
echo "       ln -s \"$DEST/brain\" /usr/local/bin/brain   # or add $DEST to \$PATH"
echo "  2. Lifecycle hooks were wired into ~/.claude/settings.json automatically (restart Claude Code to load them)."
echo "  3. (recommended) Append CLAUDE.brain.md to your ~/.claude/CLAUDE.md."
echo "  4. (optional) Start the web UI:  brain daemon start   # http://127.0.0.1:8787  (Wiki tab = encoded knowledge)"
echo
echo "Smoke test:"
echo "  $DEST/brain record --type decision --summary 'installed the global brain'"
echo "  $DEST/brain recall 'brain'"
