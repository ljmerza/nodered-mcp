"""Group-level edits: create, populate, rename, restyle."""

import uuid

from . import layout
from .constants import DEFAULT_GROUP_STYLE

# decouple_groups() geometry. A link node renders as a 30x30 icon whatever its
# name, so half its width is a constant; the gap is wide enough that the link
# column reads as a column rather than as part of the last row of nodes.
LINK_GAP = 40
LINK_HALF = 15
LINK_ROW_PITCH = 40


def _link_label(node):
    """Short name for a node, used to label the link node standing in for it."""
    return node.get("name") or node.get("type") or node.get("id", "?")


class GroupMixin:
    """Create and manipulate group boxes.

    New groups are fitted with the editor's own geometry, so the box is
    exactly what Node-RED would draw around the members.
    """

    def create_group(self, name, tab_id, node_ids, style=None, reflow=True):
        """Create a new group containing the specified nodes.

        Args:
            name: Group label.
            tab_id: Tab to create it on.
            node_ids: Members. The box is fitted to them exactly.
            style: Group style; defaults to DEFAULT_GROUP_STYLE (dark).
            reflow: If the fitted box lands on top of another group, move the
                whole group (and its nodes) to free canvas. Pass False to keep
                the coordinates as-is.
        """
        style = style or dict(DEFAULT_GROUP_STYLE)
        gid = uuid.uuid4().hex[:16]

        child_nodes = [self.by_id[nid] for nid in node_ids if nid in self.by_id]
        missing = [nid for nid in node_ids if nid not in self.by_id]
        if missing:
            raise ValueError(f"Node IDs not found: {missing}")

        # Bounding box using the editor's own geometry (layout_util), so the
        # box is exactly what Node-RED would draw -- not the old fixed
        # 180x60-per-node guess, which under-covered wide nodes and
        # over-covered narrow ones.
        box = layout.group_box_for(child_nodes, self._subflow_names())
        if box is None:
            raise ValueError("Group has no members that occupy canvas space")
        x, y = box[0], box[1]
        w, h = box[2] - box[0], box[3] - box[1]

        group_node = {
            "id": gid,
            "type": "group",
            "z": tab_id,
            "name": name,
            "style": style,
            "nodes": list(node_ids),
            "x": round(x),
            "y": round(y),
            "w": round(w),
            "h": round(h),
        }

        for nid in node_ids:
            self.by_id[nid]["g"] = gid

        self.nodes.append(group_node)
        self._index()

        if reflow:
            others = [
                g
                for g in self.nodes
                if isinstance(g, dict)
                and g.get("type") == "group"
                and g.get("z") == tab_id
                and g["id"] != gid
                and not g.get("g")
            ]
            rect = layout.group_rect(group_node)
            if any(layout.intersect(rect, layout.group_rect(o)) for o in others):
                nx, ny = self.free_region(tab_id, round(w), round(h))
                self.shift_group(gid, nx - round(x), ny - round(y))
                print(f"  reflowed group '{name}' to ({nx},{ny}) to clear an overlap")

        print(f"Created group '{name}' ({len(node_ids)} nodes, id={gid})")
        return gid

    def add_to_group(self, group_id, node_ids):
        """Add nodes to an existing group."""
        grp = None
        for n in self.nodes:
            if n["type"] == "group" and n["id"] == group_id:
                grp = n
                break
        if not grp:
            raise ValueError(f"Group {group_id} not found")

        for nid in node_ids:
            if nid not in self.by_id:
                raise ValueError(f"Node {nid} not found")
            if nid not in grp["nodes"]:
                grp["nodes"].append(nid)
            self.by_id[nid]["g"] = group_id

        self._index()
        print(f"Added {len(node_ids)} nodes to '{grp.get('name', group_id)}'")

    def move_to_group(self, node_id, group_id):
        """Move a node from its current group to a different group.

        Args:
            node_id: ID of the node to move.
            group_id: ID of the target group (or None to ungroup).
        """
        node = self.by_id.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")

        # Remove from current group
        for g in self.group_nodes:
            if node_id in g.get("nodes", []):
                g["nodes"].remove(node_id)

        node.pop("g", None)

        if group_id:
            grp = self.by_id.get(group_id)
            if not grp or grp.get("type") != "group":
                raise ValueError(f"Group {group_id} not found")
            grp.setdefault("nodes", []).append(node_id)
            node["g"] = group_id
            print(f"Moved '{node.get('name', node_id)}' to '{grp.get('name', group_id)}'")
        else:
            print(f"Ungrouped '{node.get('name', node_id)}'")

        self._index()

    def delete_group(self, group_id, delete_nodes=False):
        """Delete a group box. Its members are released, or deleted with it.

        Args:
            group_id: The group to remove.
            delete_nodes: Delete the members too. Without this they stay on
                the canvas exactly where they are, just ungrouped.
        Returns:
            How many members the group had.
        """
        grp = self.by_id.get(group_id)
        if not grp or grp.get("type") != "group":
            raise ValueError(f"Group {group_id} not found")

        name = grp.get("name", group_id)
        members = list(grp.get("nodes", []))

        if delete_nodes:
            for nid in members:
                member = self.by_id.get(nid)
                if member is None:
                    continue
                # A nested group takes its own members with it.
                if member.get("type") == "group":
                    self.delete_group(nid, delete_nodes=True)
                else:
                    self.delete_node(nid)
        else:
            for nid in members:
                member = self.by_id.get(nid)
                if member is not None:
                    member.pop("g", None)

        # Nested groups point back at this one through `g`.
        for n in self.nodes:
            if n.get("type") == "group" and n.get("g") == group_id:
                n.pop("g", None)

        if grp in self.nodes:
            self.nodes.remove(grp)
        self._strip_references({group_id})
        self._index()

        verb = "deleted" if delete_nodes else "released"
        print(f"Deleted group '{name}' ({len(members)} member(s) {verb})")
        return len(members)

    def set_group_name(self, group_id, name):
        """Set or update a group's name."""
        for n in self.nodes:
            if n["type"] == "group" and n["id"] == group_id:
                n["name"] = name
                self._index()
                return
        raise ValueError(f"Group {group_id} not found")

    def set_group_style(self, group_id, style=None):
        """Set a group's style (defaults to the standard dark style)."""
        style = style or dict(DEFAULT_GROUP_STYLE)
        for n in self.nodes:
            if n["type"] == "group" and n["id"] == group_id:
                n["style"] = style
                return
        raise ValueError(f"Group {group_id} not found")

    def normalize_group_styles(self, style=None):
        """Set all groups to the same style."""
        style = style or dict(DEFAULT_GROUP_STYLE)
        count = 0
        for n in self.nodes:
            if n["type"] == "group":
                n["style"] = dict(style)
                count += 1
        print(f"Normalized {count} groups to target style")

    # ── decoupling groups from each other ──

    def _group_chain(self, group_id):
        """A group id plus every ancestor group above it."""
        chain = [group_id]
        cur = self.by_id.get(group_id, {}).get("g")
        while cur and cur in self.by_id and cur not in chain:
            chain.append(cur)
            cur = self.by_id[cur].get("g")
        return chain

    def _member_extent(self, group_id):
        """Bounding box of a group's members, without the group's own padding.

        group_bounds() adds the editor's 25px, which is where the box edge
        goes. A link column has to be placed relative to the outermost
        *node*, so it needs the unpadded extent.
        """
        grp = self.by_id[group_id]
        sf = self._subflow_names()
        boxes = []
        for nid in grp.get("nodes", []):
            m = self.by_id.get(nid)
            if m is None:
                continue
            box = layout.group_rect(m) if m.get("type") == "group" else layout.node_box(m, sf)
            if box:
                boxes.append(box)
        if not boxes:
            return layout.group_rect(grp)
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def find_cross_group_wires(self, tab_id=None, group_id=None):
        """Wires that leave one group and land in another.

        A crossing wire pins two groups to each other's position: move one and
        the wire stretches across the canvas. Two kinds of wire are not
        crossings and are left alone -- one touching an ungrouped node, which
        has no second group to decouple from, and one between a group and its
        own ancestor or descendant, which move together anyway.

        Args:
            tab_id: Limit to one tab.
            group_id: Limit to crossings that touch this group, either end.
        """
        crossings = []
        for src in self.nodes:
            if not layout.is_canvas_node(src):
                continue
            if tab_id and src.get("z") != tab_id:
                continue
            src_g = src.get("g")
            if not src_g or src_g not in self.by_id:
                continue
            for port, targets in enumerate(src.get("wires") or []):
                for dst_id in targets:
                    dst = self.by_id.get(dst_id)
                    if dst is None:
                        continue
                    dst_g = dst.get("g")
                    if not dst_g or dst_g not in self.by_id or dst_g == src_g:
                        continue
                    if dst_g in self._group_chain(src_g) or src_g in self._group_chain(dst_g):
                        continue
                    if group_id and group_id not in (src_g, dst_g):
                        continue
                    crossings.append(
                        {
                            "src_id": src["id"],
                            "src_name": _link_label(src),
                            "src_group_id": src_g,
                            "src_group": self.by_id[src_g].get("name", src_g),
                            "port": port,
                            "dst_id": dst_id,
                            "dst_name": _link_label(dst),
                            "dst_group_id": dst_g,
                            "dst_group": self.by_id[dst_g].get("name", dst_g),
                            "tab_id": src.get("z"),
                            "tab": self.tabs.get(src.get("z", ""), src.get("z", "")),
                        }
                    )
        return crossings

    def _link_slot(self, group_id, side, y, columns, gap):
        """Reserve a spot in a group's link column. Returns (x, y).

        The column's x is computed once per group and side, from the extent
        the group had *before* any link node was added, so a group grows by
        one column however many wires cross it. Rows are the wire's own y,
        pushed down in LINK_ROW_PITCH steps when two links want the same one.
        """
        key = (group_id, side)
        if key not in columns:
            x1, _, x2, _ = self._member_extent(group_id)
            x = x2 + gap + LINK_HALF if side == "out" else x1 - gap - LINK_HALF
            columns[key] = {"x": round(x), "rows": []}
        col = columns[key]
        slot = round(y)
        while any(abs(slot - r) < LINK_ROW_PITCH for r in col["rows"]):
            slot += LINK_ROW_PITCH
        col["rows"].append(slot)
        return col["x"], slot

    def decouple_groups(self, tab_id=None, group_id=None, gap=LINK_GAP, apply=False):
        """Replace every cross-group wire with a link out / link in pair.

        One pair per wire. The link out lands in a new column just right of
        the source group's nodes, the link in just left of the target group's,
        and the original wire is cut. After this no wire runs between the two
        groups, so either can be moved without dragging a wire across the
        canvas -- which is what makes arrange() safe to run on a busy tab.

        Dry run unless apply=True. Applying widens both group boxes by their
        new link column, which can push a group into a neighbour; the count of
        overlaps that introduces is reported, and arrange_groups() repacks
        them.

        Args:
            tab_id: Limit to one tab.
            group_id: Limit to crossings that touch this group, either end.
            gap: Clear space between a group's outermost node and its link column.
            apply: Actually rewire. False just reports what would change.
        Returns:
            The list of crossings, each with link_out_id/link_in_id once applied.
        """
        scope = f"[{self.tabs.get(tab_id, tab_id)}]" if tab_id else "[all tabs]"
        if group_id:
            scope += f" group '{self.by_id.get(group_id, {}).get('name', group_id)}'"

        crossings = self.find_cross_group_wires(tab_id=tab_id, group_id=group_id)
        if not crossings:
            print(f"No cross-group wires {scope}")
            return crossings

        print(f"{'DECOUPLING' if apply else 'DRY RUN'} {scope} {len(crossings)} cross-group wire(s)")
        if not apply:
            for c in crossings:
                print(f"  '{c['src_name']}' ({c['src_group']}) -> '{c['dst_name']}' ({c['dst_group']})")
            print("  nothing changed -- re-run with apply=true")
            return crossings

        before = layout.lint(self.nodes, checks=("group-overlap",))
        columns = {}
        touched = set()
        for c in crossings:
            src, dst = self.by_id[c["src_id"]], self.by_id[c["dst_id"]]
            ox, oy = self._link_slot(c["src_group_id"], "out", src["y"], columns, gap)
            ix, iy = self._link_slot(c["dst_group_id"], "in", dst["y"], columns, gap)
            lout = self.create_node(
                c["tab_id"],
                "link out",
                f"-> {c['dst_name']}",
                config={"mode": "link", "links": [], "wires": []},
                x=ox,
                y=oy,
                group_id=c["src_group_id"],
            )
            lin = self.create_node(
                c["tab_id"],
                "link in",
                f"{c['src_name']} ->",
                config={"links": [], "wires": [[c["dst_id"]]]},
                x=ix,
                y=iy,
                group_id=c["dst_group_id"],
            )
            # Both ends list each other: the runtime routes on the link out's
            # list, the editor draws the pairing from either.
            self.by_id[lout]["links"] = [lin]
            self.by_id[lin]["links"] = [lout]
            # The wire that used to leave the group now stops at the link out;
            # the link in picks it up on the far side and carries on to dst.
            self.unwire_nodes(c["src_id"], c["dst_id"], output_index=c["port"])
            self.wire_nodes(c["src_id"], lout, output_index=c["port"])
            c["link_out_id"], c["link_in_id"] = lout, lin
            touched.update((c["src_group_id"], c["dst_group_id"]))
            print(
                f"  '{c['src_name']}' ({c['src_group']}) -> '{c['dst_name']}' "
                f"({c['dst_group']}) via link out ({ox},{oy}) / link in ({ix},{iy})"
            )

        refitted = sum(1 for gid in touched if self.refit_group(gid))
        self._index()
        print(f"  {len(crossings)} pair(s) created, {refitted} group box(es) refitted")

        new = layout.new_findings(before, layout.lint(self.nodes, checks=("group-overlap",)))
        if new:
            for f in new:
                print(f"  [ERROR] {f.message}")
            print(
                f"  {len(new)} new group overlap(s) -- the link columns widened the "
                f"boxes. Repack with nodered_layout(op='arrange', apply=true)."
            )
        return crossings
