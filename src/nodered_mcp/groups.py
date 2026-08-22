"""Group-level edits: create, populate, rename, restyle."""

import uuid

from . import layout
from .constants import DEFAULT_GROUP_STYLE


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
