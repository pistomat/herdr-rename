#!/usr/bin/env bash
# Install this plugin on every saved herdr remote machine.
#
# herdr's single-client/multi-server model does not copy plugins over SSH:
# each server keeps its own plugin store, so a plugin installed only on the
# local machine never runs for agents on remote machines. Run this after
# adding a new `herdr machine` or after this plugin changes.
set -euo pipefail

REPO="pistomat/herdr-rename"
# -n keeps ssh from consuming the while-loop's stdin below.
SSH_OPTS=(-n -o BatchMode=yes -o ConnectTimeout=5)

echo "== local =="
if herdr plugin list --json 2>/dev/null | python3 -c "
import json, sys
plugins = json.load(sys.stdin)['result']['plugins']
sys.exit(0 if any(p['plugin_id'] == 'dev.pistomat.rename-sync' for p in plugins) else 1)
"; then
  echo "  already installed"
else
  herdr plugin install "$REPO" --yes
fi

machines_json="$(herdr machine list --json)"

echo "$machines_json" | python3 -c '
import json, sys
for m in json.load(sys.stdin):
    if m["enabled"]:
        print(m["target"])
' | while read -r target; do
  echo "== $target =="

  status="$(ssh "${SSH_OPTS[@]}" "$target" "herdr status" 2>&1)" || {
    echo "  ssh failed: $status"
    continue
  }
  if echo "$status" | grep -q "compatible: no"; then
    echo "  herdr client/server protocol mismatch on $target."
    echo "  run 'herdr update' there, restart the server, then re-run this script."
    continue
  fi

  already="$(ssh "${SSH_OPTS[@]}" "$target" "herdr plugin list --json" 2>/dev/null | python3 -c "
import json, sys
try:
    plugins = json.load(sys.stdin)['result']['plugins']
except (json.JSONDecodeError, KeyError, TypeError):
    sys.exit(1)
sys.exit(0 if any(p['plugin_id'] == 'dev.pistomat.rename-sync' for p in plugins) else 1)
" && echo yes || echo no)"

  if [ "$already" = "yes" ]; then
    echo "  already installed"
  elif ssh "${SSH_OPTS[@]}" "$target" "herdr plugin install $REPO --yes"; then
    echo "  plugin installed"
  else
    echo "  plugin install failed on $target"
  fi
done
