"""Node-level edits: create, update, delete, wire."""

import uuid


class NodeEditMixin:
    """Mutate individual nodes and the wires between them.

    All edits are in memory; PersistMixin.save() is what reaches disk.
    """

    def create_node(self, tab_id, node_type, name, config=None, x=200, y=200, group_id=None):
        """Create a new node on a tab. Returns the new node's ID.

        Args:
            tab_id: The tab to place the node on.
            node_type: Node-RED node type (e.g. "function", "inject", "switch").
            name: Display name for the node.
            config: Dict of additional node properties merged into the node.
            x, y: Canvas coordinates.
            group_id: Optional group to add the node to.
        """
        nid = uuid.uuid4().hex[:16]
        node = {"id": nid, "type": node_type, "z": tab_id, "name": name, "x": x, "y": y, "wires": [[]]}
        if config:
            node.update(config)
        # Ensure id/type/z aren't overwritten by config
        node["id"] = nid
        node["type"] = node_type
        node["z"] = tab_id

        self.nodes.append(node)

        if group_id:
            grp = self.by_id.get(group_id)
            if grp and grp.get("type") == "group":
                grp.setdefault("nodes", []).append(nid)
                node["g"] = group_id

        self._index()
        return nid

    def update_node(self, node_id, **kwargs):
        """Update arbitrary properties on a node.

        Args:
            node_id: ID of the node to update.
            **kwargs: Properties to set (e.g. func="...", delay="5").
        """
        node = self.by_id.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")
        node.update(kwargs)

    def delete_node(self, node_id):
        """Remove a node and clean up all references to it.

        Removes the node from its group, and removes any wires
        pointing to it from other nodes.
        """
        node = self.by_id.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")

        # Remove from any group
        for g in self.group_nodes:
            if node_id in g.get("nodes", []):
                g["nodes"].remove(node_id)

        # Remove wires pointing to this node
        for n in self.nodes:
            for wire_set in n.get("wires", []):
                if node_id in wire_set:
                    wire_set.remove(node_id)

        self.nodes.remove(node)
        self._index()

    def set_node_name(self, node_id, name):
        """Rename a node."""
        if node_id not in self.by_id:
            raise ValueError(f"Node {node_id} not found")
        self.by_id[node_id]["name"] = name

    def duplicate_node(self, node_id, x_offset=50, y_offset=50):
        """Clone a node with a new ID. Wires are NOT copied.

        Args:
            node_id: ID of the node to clone.
            x_offset: Horizontal offset for the clone's position.
            y_offset: Vertical offset for the clone's position.
        Returns:
            The new node's ID.
        """
        node = self.by_id.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")

        new_id = uuid.uuid4().hex[:16]
        clone = dict(node)
        clone["id"] = new_id
        clone["x"] = node.get("x", 200) + x_offset
        clone["y"] = node.get("y", 200) + y_offset
        clone["wires"] = [[] for _ in node.get("wires", [[]])]

        self.nodes.append(clone)

        # Add to same group if applicable
        group_id = clone.get("g")
        if group_id:
            grp = self.by_id.get(group_id)
            if grp and grp.get("type") == "group":
                grp.setdefault("nodes", []).append(new_id)

        self._index()
        print(f"Duplicated '{node.get('name', node_id)}' -> {new_id}")
        return new_id

    def wire_nodes(self, source_id, target_id, output_index=0):
        """Add a wire from source node's output to target node.

        Args:
            source_id: ID of the source node.
            target_id: ID of the target node.
            output_index: Which output port on the source (0-based).
        """
        src = self.by_id.get(source_id)
        if not src:
            raise ValueError(f"Source node {source_id} not found")
        if target_id not in self.by_id:
            raise ValueError(f"Target node {target_id} not found")

        wires = src.setdefault("wires", [[]])
        # Extend outputs if needed
        while len(wires) <= output_index:
            wires.append([])
        if target_id not in wires[output_index]:
            wires[output_index].append(target_id)

    def unwire_nodes(self, source_id, target_id, output_index=None):
        """Remove wire(s) from source to target.

        Args:
            source_id: ID of the source node.
            target_id: ID of the target node to disconnect.
            output_index: If given, only remove from that output port.
                          If None, remove from all outputs.
        """
        src = self.by_id.get(source_id)
        if not src:
            raise ValueError(f"Source node {source_id} not found")

        wires = src.get("wires", [])
        for i, wire_set in enumerate(wires):
            if output_index is not None and i != output_index:
                continue
            if target_id in wire_set:
                wire_set.remove(target_id)

    def find_nodes(self, tab_id=None, node_type=None, name_contains=None):
        """Find nodes matching criteria. Returns list of nodes.

        Args:
            tab_id: Filter to nodes on this tab.
            node_type: Filter to this node type.
            name_contains: Substring match on node name (case-insensitive).
        """
        results = []
        for n in self.nodes:
            if not self._is_content_node(n):
                continue
            if tab_id and n.get("z") != tab_id:
                continue
            if node_type and n.get("type") != node_type:
                continue
            if name_contains and name_contains.lower() not in n.get("name", "").lower():
                continue
            results.append(n)
        return results

    def get_node(self, node_id):
        """Get a node by ID, or None if not found."""
        return self.by_id.get(node_id)
