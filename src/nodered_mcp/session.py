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
import os
from dataclasses import dataclass, field

from .config import Settings
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


class StaleFlowsError(RuntimeError):
    """flows.json changed on disk while edits were pending in memory."""


class FlowsSession:
    """One lazily-loaded, mutable view of flows.json."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.journal = Journal()
        self._flows: Flows | None = None
        self._stamp: tuple[int, int] | None = None

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
        )
        self._stamp = self._disk_stamp()
        self.journal.clear()
        nodes = len(self._flows.nodes)
        if had:
            return f"Reloaded {nodes} nodes from disk. Discarded {had} pending edit(s)."
        return f"Loaded {nodes} nodes from {self.settings.flows_path}"

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
        """True if something else wrote flows.json since we loaded it."""
        if self._stamp is None:
            return False
        current = self._disk_stamp()
        return current is not None and current != self._stamp

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

    def commit(self, deploy: bool = False, allow_overlap: bool = False,
               force: bool = False) -> str:
        self.assert_writable(force=force)
        with capture() as out:
            if deploy:
                self.flows.deploy(allow_overlap=allow_overlap)
            else:
                self.flows.save(allow_overlap=allow_overlap)
        committed = len(self.journal.entries)
        self.journal.clear()
        self._stamp = self._disk_stamp()
        return f"{out.text}\nCommitted {committed} edit(s)."

    def status(self) -> dict:
        loaded = self._flows is not None
        return {
            **self.settings.describe(),
            "loaded": loaded,
            "nodes": len(self._flows.nodes) if loaded else None,
            "dirty": self.journal.dirty,
            "pending_edits": list(self.journal.entries),
            "file_changed_on_disk": self.externally_changed(),
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
