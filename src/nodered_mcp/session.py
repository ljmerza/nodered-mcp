"""In-memory editing session over one flows.json.

Why this exists: `Flows` is stateful by design. Every build script in the
original nodered-flows/ repo constructs it once, mutates 10-30 times, then
saves once so the layout gate runs against the finished canvas. MCP tool calls
are discrete, so this wraps that lifecycle:

    edits accumulate in memory  ->  save/deploy commits them  ->  reload discards

Two guards the original didn't need:

*   Stdout capture. Nearly every read method in `Flows` prints and returns
    None. On stdio transport a stray print corrupts the MCP framing, so every
    call runs inside a redirect and the captured text becomes the tool result.

*   Staleness. Node-RED rewrites flows.json whenever someone hits Deploy in the
    browser. Committing stale in-memory nodes would silently revert their work,
    so the file's mtime is recorded at load and re-checked before every write.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from dataclasses import dataclass, field

from .config import Settings
from .constants import StaleFlowsError
from .flows import Flows


@dataclass
class Journal:
    """What has been changed in memory since the last commit."""

    entries: list[str] = field(default_factory=list)

    def record(self, message: str) -> None:
        self.entries.append(message)

    def clear(self) -> None:
        self.entries.clear()

    @property
    def dirty(self) -> bool:
        return bool(self.entries)


# How many undo steps to keep. Each one is a full copy of the node list, so
# this is a memory ceiling as much as a usability one.
UNDO_DEPTH = 20


def _snapshot(nodes: list) -> list:
    """Deep copy of a node list. flows.json is pure JSON, so this is enough."""
    return json.loads(json.dumps(nodes))


def _describe(node: dict) -> dict:
    return {"id": node.get("id"), "type": node.get("type"), "name": node.get("name", "")}


# Re-exported: `from nodered_mcp.session import StaleFlowsError` is the
# historical import path and stays working.
__all__ = ["FlowsSession", "Journal", "StaleFlowsError", "capture", "run"]


class FlowsSession:
    """One lazily-loaded, mutable view of flows.json."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.journal = Journal()
        self._flows: Flows | None = None
        self._stamp: tuple[int, int] | None = None
        # What was on disk when we loaded, for diff(), and the undo stack.
        self._baseline: list | None = None
        self._undo: list[tuple[list, int]] = []

    # ── lifecycle ──

    @property
    def flows(self) -> Flows:
        if self._flows is None:
            self.reload()
        return self._flows

    def reload(self) -> str:
        """Drop all in-memory edits and re-read flows.json from disk."""
        had = len(self.journal.entries)
        self._flows = Flows(
            self.settings.flows_path,
            container=self.settings.container,
            container_flows_path=self.settings.container_flows_path,
            restart_cmd=self.settings.restart_cmd,
            admin_url=self.settings.admin_url,
            admin_token=self.settings.admin_token,
            group_style=self.settings.group_style,
        )
        self._stamp = self._disk_stamp()
        self.journal.clear()
        self._baseline = _snapshot(self._flows.nodes)
        self._undo.clear()
        nodes = len(self._flows.nodes)
        if had:
            return f"Reloaded {nodes} nodes from disk. Discarded {had} pending edit(s)."
        where = self.settings.flows_path or f"{self.settings.admin_url}/flows"
        return f"Loaded {nodes} nodes from {where}"

    def _disk_stamp(self) -> tuple[int, int] | None:
        """(mtime_ns, size) identity for flows.json.

        Nanoseconds, not os.path.getmtime: a float epoch only resolves to
        about a microsecond, so a write landing in the same tick as the load
        would compare equal and slip past the guard. Size is included so a
        restore of an older file is caught too.
        """
        try:
            st = os.stat(self.settings.flows_path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def externally_changed(self) -> bool:
        """True if something else wrote flows.json since we loaded it.

        The cheap check is the stamp. A mismatch is then confirmed against the
        file's actual content, because not every write is somebody else's
        work: after an Admin API deploy Node-RED persists the very flows it
        was just handed, back to the same file, with its own formatting and a
        fresh mtime. Treating that as a conflict would make the write after
        every deploy fail for no reason.
        """
        if self._stamp is None:
            return False
        current = self._disk_stamp()
        if current is None or current == self._stamp:
            return False
        return not self._matches_disk()

    def _matches_disk(self) -> bool:
        """True if flows.json on disk is something this session already knows.

        Either what we loaded (the baseline) or what we hold in memory now. The
        second case is our own write that never got confirmed: an Admin API
        deploy saves flows.json first, so if the push to Node-RED then fails the
        file already holds the pending edits. That is not somebody else's work,
        and calling it stale would make the obvious retry fail.
        """
        try:
            with open(self.settings.flows_path) as fh:
                on_disk = json.load(fh)
        except (OSError, ValueError):
            return False
        if self._baseline is not None and on_disk == self._baseline:
            return True
        return self._flows is not None and on_disk == self._flows.nodes

    def assert_writable(self, force: bool = False) -> None:
        """Refuse to commit over someone else's newer flows.json."""
        if force or not self.externally_changed():
            return
        raise StaleFlowsError(
            f"{self.settings.flows_path} changed on disk after this session loaded it "
            f"-- most likely a Deploy from the Node-RED browser UI. Committing now "
            f"would revert that work.\n"
            f"  Pending edits: {len(self.journal.entries)}\n"
            f"  Either re-read with session(op='reload') and redo the edits, "
            f"or pass force=true to overwrite the newer file."
        )

    # ── committing ──

    def commit(self, deploy: bool = False, allow_overlap: bool = False, force: bool = False) -> str:
        self.assert_writable(force=force)
        with capture() as out:
            if deploy:
                self.flows.deploy(allow_overlap=allow_overlap, force=force)
            else:
                self.flows.save(allow_overlap=allow_overlap)
        committed = len(self.journal.entries)
        self.journal.clear()
        self._stamp = self._disk_stamp()
        # What we just wrote is the new baseline; undoing across a commit
        # would silently disagree with the file on disk.
        self._baseline = _snapshot(self.flows.nodes)
        self._undo.clear()
        return f"{out.text}\nCommitted {committed} edit(s)."

    # ── undo ──

    @contextlib.contextmanager
    def undoable(self):
        """Snapshot the flows around a mutating tool call.

        The snapshot is kept only if the call actually changed something, so a
        read-only op or one that failed its own validation doesn't leave a
        dead step on the stack. A call that raised *after* mutating does get
        a step, which is the only way back from a half-applied edit.
        """
        before = _snapshot(self.flows.nodes)
        mark = len(self.journal.entries)
        try:
            yield
        except Exception:
            # Only worth a step if the failure left something half-applied;
            # a rejected argument changed nothing to roll back.
            if self.flows.nodes != before:
                self._remember(before, mark)
            raise
        if len(self.journal.entries) > mark:
            self._remember(before, mark)

    def _remember(self, nodes: list, mark: int) -> None:
        self._undo.append((nodes, mark))
        del self._undo[:-UNDO_DEPTH]

    def undo(self) -> dict:
        """Roll back the last mutating tool call. In memory only."""
        if not self._undo:
            return {"ok": False, "message": "Nothing to undo in this session."}

        nodes, mark = self._undo.pop()
        undone = list(self.journal.entries[mark:])
        self.flows.nodes = nodes
        self.flows._index()
        del self.journal.entries[mark:]
        return {
            "ok": True,
            "undone": undone,
            "pending_edits": len(self.journal.entries),
            "undo_steps_left": len(self._undo),
        }

    # ── diff ──

    def diff(self) -> dict:
        """What the pending edits would change in flows.json.

        Compares the in-memory nodes against the file as it was when this
        session loaded it, per node id.
        """
        if self._flows is None:
            return {"dirty": False, "added": [], "removed": [], "changed": []}

        base = {n["id"]: n for n in (self._baseline or []) if isinstance(n, dict) and "id" in n}
        now = {n["id"]: n for n in self._flows.nodes if isinstance(n, dict) and "id" in n}

        added = [_describe(now[i]) for i in now if i not in base]
        removed = [_describe(base[i]) for i in base if i not in now]

        changed = []
        for nid in now.keys() & base.keys():
            props = {
                key: {"from": base[nid].get(key), "to": now[nid].get(key)}
                for key in sorted(set(now[nid]) | set(base[nid]))
                if base[nid].get(key) != now[nid].get(key)
            }
            if props:
                changed.append({**_describe(now[nid]), "props": props})

        return {
            "dirty": bool(added or removed or changed),
            "added": added,
            "removed": removed,
            "changed": changed,
            "pending_edits": list(self.journal.entries),
        }

    def status(self) -> dict:
        loaded = self._flows is not None
        return {
            **self.settings.describe(),
            "loaded": loaded,
            "nodes": len(self._flows.nodes) if loaded else None,
            "dirty": self.journal.dirty,
            "pending_edits": list(self.journal.entries),
            "undo_steps": len(self._undo),
            "file_changed_on_disk": self.externally_changed(),
            "rev": self._flows.rev if loaded else None,
        }


class _Captured:
    __slots__ = ("buffer", "text")

    def __init__(self):
        self.buffer = io.StringIO()
        self.text = ""


@contextlib.contextmanager
def capture():
    """Run a block with stdout redirected into a buffer.

    `Flows` methods report by printing. That output is the useful part of the
    result, and on stdio transport it must never reach the real stdout.
    """
    cap = _Captured()
    with contextlib.redirect_stdout(cap.buffer):
        yield cap
    cap.text = cap.buffer.getvalue().rstrip()


def run(fn, *args, **kwargs) -> tuple[str, object]:
    """Call a Flows method, returning (printed output, return value)."""
    with capture() as out:
        value = fn(*args, **kwargs)
    return out.text, value
