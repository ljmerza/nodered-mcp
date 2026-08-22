"""Canvas placement: claim empty space, measure and repair group boxes.

The geometry itself lives in layout.py, ported from the Node-RED editor.
This is the part that reads and writes coordinates on a loaded flows file.
"""

from . import layout


class LayoutMixin:
    """Find free canvas and keep group boxes honest."""

    def _subflow_names(self):
        return layout.subflow_names(self.nodes)

    def occupied_rects(self, tab_id):
        """Every canvas rect already in use on a tab.

        Top-level group boxes plus the boxes of ungrouped nodes. Nested groups
        and grouped nodes are skipped -- their parent group's box covers them.
        """
        sf = self._subflow_names()
        rects = []
        for n in self.nodes:
            if not isinstance(n, dict) or n.get("z") != tab_id:
                continue
            if n.get("type") == "group":
                if not n.get("g"):
                    rects.append(layout.group_rect(n))
            elif layout.is_canvas_node(n) and not n.get("g"):
                box = layout.node_box(n, sf)
                if box:
                    rects.append(box)
        return rects

    def free_region(self, tab_id, w, h, margin=40, strategy="below", grid=20):
        """Find empty canvas for a w x h block. Returns its top-left (x, y).

        Use this instead of hand-picking coordinates in a build script: claim
        a region first, then lay nodes out relative to the returned origin.

            ox, oy = f.free_region(TAB, w=1600, h=300)
            f.create_node(TAB, "inject", "tick", x=ox, y=oy)
            f.create_node(TAB, "switch", "gate", x=ox + 200, y=oy)

        Args:
            tab_id: Tab to place the block on.
            w, h: Size of the block to reserve.
            margin: Clearance kept from existing content.
            strategy: "below" drops the block underneath everything on the tab
                (always safe, always predictable). "gap" packs it into the
                first hole big enough, scanning top-to-bottom then
                left-to-right.
            grid: Snap the result to this grid (Node-RED's is 20).
        """

        def snap(v):
            return int(round(v / grid) * grid)

        rects = self.occupied_rects(tab_id)
        if not rects:
            return (100, 100)

        min_x = min(r[0] for r in rects)
        max_bottom = max(r[3] for r in rects)

        if strategy == "below":
            return (snap(min_x), snap(max_bottom + margin))
        if strategy != "gap":
            raise ValueError(f"unknown strategy {strategy!r} (use below|gap)")

        # Bottom-left packing against the corners of existing content.
        cand_x = sorted({min_x} | {r[2] + margin for r in rects})
        cand_y = sorted({min(r[1] for r in rects)} | {r[3] + margin for r in rects})
        for y in cand_y:
            for x in cand_x:
                box = (x - margin, y - margin, x + w + margin, y + h + margin)
                if not any(layout.intersect(box, r) for r in rects):
                    return (snap(x), snap(y))
        return (snap(min_x), snap(max_bottom + margin))

    def group_bounds(self, group_id):
        """The box the Node-RED editor would draw around a group's members."""
        grp = self.by_id.get(group_id)
        if not grp or grp.get("type") != "group":
            raise ValueError(f"Group {group_id} not found")
        members = [self.by_id[nid] for nid in grp.get("nodes", []) if nid in self.by_id]
        return layout.group_box_for(members, self._subflow_names())

    def refit_group(self, group_id):
        """Resize a group's box to exactly fit its members. Returns True if changed."""
        grp = self.by_id[group_id]
        box = self.group_bounds(group_id)
        if box is None:
            return False
        before = (grp["x"], grp["y"], grp.get("w"), grp.get("h"))
        grp["x"] = round(box[0])
        grp["y"] = round(box[1])
        grp["w"] = round(box[2] - box[0])
        grp["h"] = round(box[3] - box[1])
        return before != (grp["x"], grp["y"], grp["w"], grp["h"])

    def shift_group(self, group_id, dx, dy):
        """Move a group and everything inside it by (dx, dy)."""
        grp = self.by_id[group_id]
        grp["x"] += dx
        grp["y"] += dy
        stack = list(grp.get("nodes", []))
        while stack:
            nid = stack.pop()
            n = self.by_id.get(nid)
            if not n:
                continue
            if n.get("type") == "group":
                n["x"] += dx
                n["y"] += dy
                stack.extend(n.get("nodes", []))
            elif "x" in n:
                n["x"] += dx
                n["y"] += dy

    def layout_check(self, tab=None, quiet=False):
        """Lint the in-memory canvas. Returns the list of findings."""
        findings = layout.lint(self.nodes, tab_filter=tab)
        layout.report(findings, quiet=quiet)
        return findings
