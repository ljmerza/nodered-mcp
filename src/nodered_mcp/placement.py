"""Canvas placement: claim empty space, measure and repair group boxes.

The geometry itself lives in layout.py, ported from the Node-RED editor.
This is the part that reads and writes coordinates on a loaded flows file.
"""

import math

from . import layout

# arrange_groups defaults. The gutter is wider than the editor's 20px grid so
# adjacent group boxes read as separate blocks; the target ratio is roughly a
# widescreen editor viewport, which is what stops a tab growing into one tall
# column.
ARRANGE_GUTTER = 60
ARRANGE_TARGET_RATIO = 1.6
ARRANGE_MAX_COLUMNS = 4
ARRANGE_SORTS = ("packed", "current", "name")


def _snap(value, grid):
    return int(round(value / grid) * grid)


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

    # ── organising a whole tab ──

    def _top_level_groups(self, tab_id):
        return [
            n
            for n in self.nodes
            if isinstance(n, dict) and n.get("type") == "group" and n.get("z") == tab_id and not n.get("g")
        ]

    def _loose_boxes(self, tab_id):
        """Boxes of the canvas nodes on a tab that belong to no group."""
        sf = self._subflow_names()
        boxes = []
        for n in self.nodes:
            if not isinstance(n, dict) or n.get("z") != tab_id or n.get("g"):
                continue
            if not layout.is_canvas_node(n):
                continue
            box = layout.node_box(n, sf)
            if box:
                boxes.append(box)
        return boxes

    def tab_footprint(self, tab_id):
        """Current bounding box and density of a tab, or None if it is empty."""
        boxes = [layout.group_rect(g) for g in self._top_level_groups(tab_id)]
        boxes += self._loose_boxes(tab_id)
        if not boxes:
            return None
        x1 = min(b[0] for b in boxes)
        y1 = min(b[1] for b in boxes)
        x2 = max(b[2] for b in boxes)
        y2 = max(b[3] for b in boxes)
        w, h = x2 - x1, y2 - y1
        used = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        return {
            "x": x1,
            "y": y1,
            "w": w,
            "h": h,
            "ratio": (w / h) if h else 0.0,
            "fill": (used / (w * h)) if w and h else 0.0,
        }

    def _column_layout(self, sized, columns, gutter):
        """Masonry: each group goes in the shortest column so far.

        `sized` is [(group, w, h)] already in placement order. Returns
        (placements, block_w, block_h) where a placement is
        (group, column index, y offset within the block).
        """
        heights = [0.0] * columns
        widths = [0.0] * columns
        placements = []
        for group, w, h in sized:
            col = min(range(columns), key=lambda i: (heights[i], i))
            placements.append((group, col, heights[col]))
            heights[col] += h + gutter
            widths[col] = max(widths[col], w)

        # Column x origins are only knowable once every column's widest member
        # is known, hence the second pass.
        offsets = []
        acc = 0.0
        for col in range(columns):
            offsets.append(acc)
            acc += widths[col] + gutter

        block_w = acc - gutter if columns else 0.0
        block_h = max(heights) - gutter if any(heights) else 0.0
        return [(g, offsets[c], y) for g, c, y in placements], block_w, block_h

    def arrange_plan(
        self,
        tab_id,
        columns=0,
        sort="packed",
        gutter=ARRANGE_GUTTER,
        target_ratio=ARRANGE_TARGET_RATIO,
        grid=20,
    ):
        """Work out where every top-level group on a tab should go.

        Groups are packed into columns; the column count is chosen to land the
        tab's bounding box nearest `target_ratio` unless you pass one. Nodes
        that belong to no group never move -- if the packed block would land
        on one, the whole block drops below them instead.

        Returns a plan dict, or None if the tab holds fewer than two groups.
        """
        if sort not in ARRANGE_SORTS:
            raise ValueError(f"sort must be one of {ARRANGE_SORTS}, not {sort!r}")

        groups = self._top_level_groups(tab_id)
        if len(groups) < 2:
            return None

        rects = {g["id"]: layout.group_rect(g) for g in groups}
        if sort == "packed":
            ordered = sorted(groups, key=lambda g: -(rects[g["id"]][3] - rects[g["id"]][1]))
        elif sort == "name":
            ordered = sorted(groups, key=lambda g: (g.get("name", "").lower(), g["id"]))
        else:
            ordered = sorted(groups, key=lambda g: (rects[g["id"]][1], rects[g["id"]][0]))
        sized = [
            (g, rects[g["id"]][2] - rects[g["id"]][0], rects[g["id"]][3] - rects[g["id"]][1]) for g in ordered
        ]

        if columns:
            candidates = [columns]
        else:
            candidates = range(1, min(ARRANGE_MAX_COLUMNS, len(groups)) + 1)

        best = None
        for count in candidates:
            placements, bw, bh = self._column_layout(sized, count, gutter)
            if not bw or not bh:
                continue
            # Compare in log space so 0.5 and 2.0 sit the same distance from 1.
            score = abs(math.log((bw / bh) / target_ratio))
            if best is None or score < best[0]:
                best = (score, count, placements, bw, bh)
        if best is None:
            return None
        _, count, placements, block_w, block_h = best

        loose = self._loose_boxes(tab_id)
        boxes = list(rects.values()) + loose
        ox = _snap(min(b[0] for b in boxes), grid)
        oy = _snap(min(b[1] for b in boxes), grid)

        sizes = {g["id"]: (w, h) for g, w, h in sized}

        def hits_a_loose_node(origin_y):
            for group, dx, dy in placements:
                w, h = sizes[group["id"]]
                tx, ty = _snap(ox + dx, grid), _snap(origin_y + dy, grid)
                if any(layout.intersect((tx, ty, tx + w, ty + h), box) for box in loose):
                    return True
            return False

        # Loose nodes hold their ground: if the packed block would cover one,
        # the whole block drops below every loose node rather than shoving it
        # aside. One drop is always enough -- nothing loose sits lower.
        pushed = 0
        if loose and hits_a_loose_node(oy):
            pushed = _snap(max(b[3] for b in loose) + gutter, grid) - oy
            oy += pushed

        moves = []
        projected = list(loose)
        for group, dx, dy in placements:
            tx, ty = _snap(ox + dx, grid), _snap(oy + dy, grid)
            w, h = sizes[group["id"]]
            projected.append((tx, ty, tx + w, ty + h))
            cx, cy = rects[group["id"]][0], rects[group["id"]][1]
            if (tx, ty) != (cx, cy):
                moves.append(
                    {
                        "group_id": group["id"],
                        "name": group.get("name", ""),
                        "from": [cx, cy],
                        "to": [tx, ty],
                        "size": [w, h],
                    }
                )

        # `after` measures the whole tab, loose nodes included, so it compares
        # like for like with `before`. The packed block on its own is narrower
        # and shorter, and that is what the column count was scored against.
        pw = max(b[2] for b in projected) - min(b[0] for b in projected)
        ph = max(b[3] for b in projected) - min(b[1] for b in projected)

        before = self.tab_footprint(tab_id)
        return {
            "tab_id": tab_id,
            "tab": self.tabs.get(tab_id, tab_id),
            "columns": count,
            "sort": sort,
            "groups": len(groups),
            "loose_nodes": len(loose),
            "pushed_below_loose_nodes": pushed,
            "before": {
                "w": round(before["w"]),
                "h": round(before["h"]),
                "ratio": round(before["ratio"], 2),
            },
            "after": {
                "w": round(pw),
                "h": round(ph),
                "ratio": round(pw / ph, 2) if ph else 0.0,
            },
            "block": {"w": round(block_w), "h": round(block_h)},
            "moves": moves,
        }

    def arrange_groups(
        self,
        tab_id,
        columns=0,
        sort="packed",
        gutter=ARRANGE_GUTTER,
        target_ratio=ARRANGE_TARGET_RATIO,
        grid=20,
        apply=False,
    ):
        """Repack a tab's groups into columns. Dry run unless apply=True.

        Nothing is written to disk either way -- applying moves the groups in
        memory, and save() still runs the layout gate over the result.
        """
        plan = self.arrange_plan(
            tab_id, columns=columns, sort=sort, gutter=gutter, target_ratio=target_ratio, grid=grid
        )
        if plan is None:
            print(f"Nothing to arrange on {self.tabs.get(tab_id, tab_id)}: fewer than two groups")
            return None

        label = plan["tab"]
        b, a = plan["before"], plan["after"]
        print(
            f"{'ARRANGING' if apply else 'DRY RUN'} [{label}] "
            f"{plan['groups']} groups into {plan['columns']} column(s), sort={sort}"
        )
        print(f"  {b['w']}x{b['h']} (ratio {b['ratio']}) -> {a['w']}x{a['h']} (ratio {a['ratio']})")
        if plan["pushed_below_loose_nodes"]:
            print(
                f"  dropped {plan['pushed_below_loose_nodes']}px to clear "
                f"{plan['loose_nodes']} ungrouped node(s)"
            )
        for move in plan["moves"]:
            print(f"  move '{move['name']}' {tuple(move['from'])} -> {tuple(move['to'])}")
        if not plan["moves"]:
            print("  already arranged, nothing to move")

        if apply:
            for move in plan["moves"]:
                self.shift_group(
                    move["group_id"],
                    move["to"][0] - move["from"][0],
                    move["to"][1] - move["from"][1],
                )
        else:
            print("  nothing moved -- re-run with apply=true")
        return plan

    def audit_tabs(self, tab=None, target_ratio=ARRANGE_TARGET_RATIO, gutter=ARRANGE_GUTTER):
        """Report how well organised each tab is, worst first.

        For every tab: its footprint, how much of that footprint is actually
        covered by content, any group overlaps, and what repacking would do to
        its height.
        """
        rows = []
        for tab_id, label in self.tabs.items():
            if tab and tab.lower() not in label.lower():
                continue
            foot = self.tab_footprint(tab_id)
            if foot is None:
                continue
            plan = self.arrange_plan(tab_id, target_ratio=target_ratio, gutter=gutter)
            overlaps = len(layout.lint(self.nodes, checks=("group-overlap",), tab_filter=label))
            rows.append(
                {
                    "tab": label,
                    "tab_id": tab_id,
                    "groups": len(self._top_level_groups(tab_id)),
                    "loose_nodes": len(self._loose_boxes(tab_id)),
                    "w": round(foot["w"]),
                    "h": round(foot["h"]),
                    "ratio": round(foot["ratio"], 2),
                    "fill": round(100 * foot["fill"], 1),
                    "overlaps": overlaps,
                    "recommended_columns": plan["columns"] if plan else None,
                    "projected_h": plan["after"]["h"] if plan else None,
                    "height_saved": round(foot["h"] - plan["after"]["h"]) if plan else 0,
                    "moves": len(plan["moves"]) if plan else 0,
                }
            )

        rows.sort(key=lambda r: -r["height_saved"])
        print(
            f"{'tab':24} {'grps':>4} {'loose':>5} {'w':>6} {'h':>6} {'ratio':>6} "
            f"{'fill':>6} {'ovl':>4} {'cols':>4} {'saves':>7}"
        )
        for r in rows:
            cols = r["recommended_columns"] or "-"
            saved = f"{r['height_saved']:+d}" if r["moves"] else "-"
            print(
                f"{r['tab'][:24]:24} {r['groups']:4} {r['loose_nodes']:5} {r['w']:6} "
                f"{r['h']:6} {r['ratio']:6.2f} {r['fill']:5.1f}% {r['overlaps']:4} "
                f"{cols:>4} {saved:>7}"
            )
        return rows

    def layout_check(self, tab=None, quiet=False):
        """Lint the in-memory canvas. Returns the list of findings."""
        findings = layout.lint(self.nodes, tab_filter=tab)
        layout.report(findings, quiet=quiet)
        return findings
