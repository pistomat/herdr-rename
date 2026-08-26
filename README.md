# Rename Sync

A [herdr](https://herdr.dev) plugin. Name your Claude Code or Codex session once, and the
name lands everywhere:

| Surface | Result |
| --- | --- |
| herdr agent | `rebalancer-bot` |
| herdr workspace (space) | `rebalancer-bot` |
| Outer terminal tab title | `rewind: rebalancer-bot` |

Type `/rename rebalancer-bot` in Claude Code or Codex. Within a turn boundary, all three
update.

## Install

```bash
herdr plugin install pistomat/herdr-rename
```

Requires herdr 0.8.0+ and `python3` (stdlib only, no dependencies).

## Unpin your terminal tab titles

Ghostty's **Change Tab Title…** command sets a manual override that ignores every title
escape sequence afterwards. If you have ever used it on a tab, this plugin cannot update
that tab.

Clear the override once per tab: open the command palette, run **Change Tab Title…**, and
submit an empty value. After that the title follows the plugin.

## How it works

Neither Claude Code nor Codex fires a hook on `/rename`, so the plugin reconciles state on
herdr events instead — agent detection, agent status changes, and focus changes. Every run
is a full reconcile, so it is self-healing.

It resolves the name you chose from each agent's own registry:

- **Claude Code** — `~/.claude/sessions/<pid>.json`. A `nameSource` of `derived` means the
  name was auto-generated, so it is ignored.
- **Codex** — `~/.codex/session_index.jsonl`. Presence in the index means you named it.

Auto-generated names never trigger a rename. That is why a fresh session leaves your space
label alone.

### Naming rules

Agent names must match `[a-z][a-z0-9_-]{0,31}`, so the session name is slugified for that
surface only. The workspace label and tab title use your name verbatim.

The tab title prefix is the short hostname, so a box named `rewind` produces
`rewind: <name>`. The title is per attached client and tracks the focused workspace, so
switching spaces updates it.

### Not clobbering manual names

A workspace is renamed only when its current label is either the default (the root pane's
directory basename) or a label this plugin wrote earlier. Rename a space by hand and the
plugin leaves it alone from then on. Previous writes are tracked in
`$HERDR_PLUGIN_STATE_DIR/state.json`.

## Development

```bash
herdr plugin link .
python3 sync.py --dry-run          # print what would change
herdr plugin action invoke dev.pistomat.rename-sync.sync-now
herdr plugin log | tail
```
