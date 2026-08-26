#!/usr/bin/env python3
"""Propagate agent session names onto the herdr agent, workspace, and outer terminal title."""

import fcntl
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

HERDR_BIN = os.environ.get("HERDR_BIN_PATH") or "herdr"
PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID") or "dev.pistomat.rename-sync"
CLAUDE_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
CODEX_SESSION_INDEX = Path.home() / ".codex" / "session_index.jsonl"
AGENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

DRY_RUN = "--dry-run" in sys.argv


def state_dir() -> Path:
    configured = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".local" / "state" / "herdr" / "plugins" / PLUGIN_ID


def log(message: str) -> None:
    print(message, file=sys.stderr)


def herdr(*args: str) -> dict:
    result = subprocess.run(
        [HERDR_BIN, *args], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(f"herdr {' '.join(args)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def herdr_mutate(*args: str) -> bool:
    if DRY_RUN:
        log(f"dry-run: herdr {' '.join(args)}")
        return True
    try:
        herdr(*args)
        return True
    except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        log(f"skipped: {error}")
        return False


def api_call(method: str, params: dict) -> dict:
    socket_path = os.environ.get("HERDR_SOCKET_PATH")
    if not socket_path:
        raise RuntimeError("HERDR_SOCKET_PATH is not set")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    try:
        client.connect(socket_path)
        request = {"id": f"{PLUGIN_ID}:1", "method": method, "params": params}
        client.sendall((json.dumps(request) + "\n").encode())
        buffer = b""
        while b"\n" not in buffer:
            chunk = client.recv(65536)
            if not chunk:
                break
            buffer += chunk
    finally:
        client.close()
    return json.loads(buffer.decode().split("\n", 1)[0])


def claude_session_names() -> dict:
    """Map Claude session id to the name the user chose, skipping auto-derived names."""
    names = {}
    if not CLAUDE_SESSIONS_DIR.is_dir():
        return names
    for path in CLAUDE_SESSIONS_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("nameSource") == "derived":
            continue
        session_id = record.get("sessionId")
        name = record.get("name")
        if session_id and name:
            names[session_id] = name
    return names


def codex_session_names() -> dict:
    """Map Codex thread id to its latest name; presence in the index means the user named it."""
    names = {}
    try:
        lines = CODEX_SESSION_INDEX.read_text(encoding="utf-8").splitlines()
    except OSError:
        return names
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        thread_id = record.get("id")
        name = record.get("thread_name")
        if thread_id and name:
            names[thread_id] = name
    return names


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)[:32]
    if not slug or not slug[0].isalpha():
        slug = f"a-{slug}"[:32]
    return slug.rstrip("-_")


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    if DRY_RUN:
        return
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")


def chosen_name_for(agent: dict, names_by_kind: dict) -> str:
    session = agent.get("agent_session") or {}
    session_id = session.get("value")
    kind = agent.get("agent")
    if not session_id or kind not in names_by_kind:
        return ""
    return names_by_kind[kind].get(session_id, "")


def sync_agent_names(agents: list, names_by_kind: dict) -> dict:
    """Rename each user-named agent and return the desired label per workspace."""
    desired = {}
    for agent in agents:
        name = chosen_name_for(agent, names_by_kind)
        if not name:
            continue
        slug = slugify(name)
        if AGENT_NAME_PATTERN.match(slug) and agent.get("name") != slug:
            herdr_mutate("agent", "rename", agent["pane_id"], slug)
        workspace_id = agent.get("workspace_id")
        is_root_pane = agent.get("pane_id", "").endswith(":p1")
        if workspace_id and (workspace_id not in desired or is_root_pane):
            desired[workspace_id] = (name, agent.get("cwd") or "")
    return desired


def sync_workspace_labels(workspaces: list, desired: dict, written: dict) -> dict:
    """Rename workspaces whose label is still the default or our own previous write."""
    labels = {workspace["workspace_id"]: workspace["label"] for workspace in workspaces}
    for workspace in workspaces:
        workspace_id = workspace["workspace_id"]
        target = desired.get(workspace_id)
        if not target:
            continue
        name, cwd = target
        current = workspace["label"]
        if current == name:
            written[workspace_id] = name
            continue
        default_label = os.path.basename(cwd.rstrip("/"))
        if current != default_label and current != written.get(workspace_id):
            continue
        if herdr_mutate("workspace", "rename", workspace_id, name):
            written[workspace_id] = name
            labels[workspace_id] = name
    return labels


def sync_window_title(workspaces: list, labels: dict) -> None:
    """Set the outer terminal tab title to '<host>: <focused workspace label>'."""
    focused = next((w for w in workspaces if w.get("focused")), None)
    if not focused:
        return
    label = labels.get(focused["workspace_id"], focused["label"])
    title = f"{socket.gethostname().split('.')[0]}: {label}"
    if DRY_RUN:
        log(f"dry-run: client.window_title.set {title!r}")
        return
    try:
        api_call("client.window_title.set", {"title": title})
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        log(f"window title skipped: {error}")


def main() -> int:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)

    lock_file = open(directory / "sync.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0

    state_path = directory / "state.json"
    state = load_state(state_path)
    written = state.setdefault("workspace_labels", {})

    try:
        agents = herdr("agent", "list")["result"]["agents"]
        workspaces = herdr("workspace", "list")["result"]["workspaces"]
    except (RuntimeError, KeyError, json.JSONDecodeError) as error:
        log(f"herdr state unavailable: {error}")
        return 0

    names_by_kind = {"claude": claude_session_names(), "codex": codex_session_names()}
    desired = sync_agent_names(agents, names_by_kind)
    labels = sync_workspace_labels(workspaces, desired, written)
    sync_window_title(workspaces, labels)

    live = {workspace["workspace_id"] for workspace in workspaces}
    state["workspace_labels"] = {k: v for k, v in written.items() if k in live}
    save_state(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
