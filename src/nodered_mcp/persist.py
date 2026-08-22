"""Writing flows.json back out, and the layout gate that guards it."""

import json
import os
import subprocess
import tempfile

from . import layout
from .constants import LayoutError


class PersistMixin:
    """Commit in-memory edits to disk, or to disk and the container."""

    def save(self, allow_overlap=False, quiet=False):
        """Write flows.json back to disk.

        Before writing, lints the canvas layout and refuses if this edit
        introduces a NEW error-level problem -- a group landing on top of
        another group, or a group box that no longer covers its own nodes.
        Problems that already existed on disk never block. Warnings
        (node-on-node collisions, nodes inside a foreign group box) print.

        Args:
            allow_overlap: Write anyway, printing what would have blocked.
            quiet: Suppress the "no new layout problems" line.

        Raises:
            LayoutError: A new error-level layout problem was introduced.

        Uses passwordless sudo tee if available, falling back to a direct
        write (flows.json is cubxi-owned, so the fallback normally wins).
        """
        self._check_layout(allow_overlap=allow_overlap, quiet=quiet)
        content = json.dumps(self.nodes, indent=4)
        try:
            subprocess.run(
                ["sudo", "-n", "/usr/bin/tee", self.path],
                input=content.encode(),
                stdout=subprocess.DEVNULL,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to direct write if sudo tee not available
            with open(self.path, "w") as f:
                f.write(content)
        print(f"Saved to {self.path}")

    def _check_layout(self, allow_overlap=False, quiet=False):
        """Compare on-disk layout with in-memory layout; block on new errors."""
        try:
            with open(self.path) as fh:
                before = layout.lint(json.load(fh))
        except (OSError, ValueError):
            return  # can't read the old file; nothing to diff against

        after = layout.lint(self.nodes)
        new = layout.new_findings(before, after)
        if not new:
            if not quiet:
                print("layout gate: no new layout problems")
            return

        errors = [f for f in new if f.severity == "ERROR"]
        warns = [f for f in new if f.severity != "ERROR"]
        for f in warns:
            print(f"layout gate: [WARN ] {f.check} [{f.tab}] {f.message}")
        if not errors:
            return

        for f in errors:
            print(f"layout gate: [ERROR] {f.check} [{f.tab}] {f.message}")
        if allow_overlap:
            print(f"layout gate: overridden, writing anyway ({len(errors)} errors)")
            return
        raise LayoutError(
            f"{len(errors)} new layout error(s) -- fix the placement, or call "
            f"save(allow_overlap=True) to write anyway. "
            f"Try f.free_region(tab_id, w, h) to claim empty canvas, or "
            f"f.refit_group(group_id) to resize a group around its nodes."
        )

    def deploy(self, allow_overlap=False):
        """Copy flows.json into the Node-RED container and restart.

        Runs the same layout gate as save() -- a deploy that would introduce a
        new overlap is refused before anything reaches the container.
        """
        self._check_layout(allow_overlap=allow_overlap)
        # Write to a temp file first, then docker cp it in.
        fd, tmp = tempfile.mkstemp(prefix="flows_deploy_", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.nodes, f, indent=4)
            subprocess.run(["docker", "cp", tmp, f"{self.container}:{self.container_flows_path}"], check=True)
        finally:
            os.unlink(tmp)
        subprocess.run(self.restart_cmd, check=True)
        print(f"Deployed and restarted Node-RED ({' '.join(self.restart_cmd)})")
