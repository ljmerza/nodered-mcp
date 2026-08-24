"""Node-level edits: create, update, delete, wire."""

import uuid


class NodeEditMixin:
    """Mutate individual nodes and the wires between them.

    All edits are in memory; PersistMixin.save() is what reaches disk.
    """

    # Kept when a node is replaced in place: identity, canvas position, group
    # membership, and wiring. Everything else belongs to the old type.
    KEEP_ON_REPLACE = ("id", "type", "z", "g", "x", "y", "wires", "d")

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

    def create_config_node(self, node_type, name, config=None):
        """Create a config node. Returns the new node's ID.

        Config nodes -- an `mqtt-broker`, a `tls-config`, an `influxdb`, or
        whatever a contrib palette calls its connection node -- belong to no
        tab, occupy no canvas, and carry no wires. Other nodes point at them
        by id, so create one first and pass its id in the referring node's
        config, under whatever property that node type expects:

            bid = f.create_config_node("mqtt-broker", "local", {"broker": "localhost"})
            f.create_node(tab, "mqtt out", "publish", config={"broker": bid})

        Args:
            node_type: Node-RED config node type.
            name: Display name.
            config: Dict of additional properties merged into the node.
        """
        nid = uuid.uuid4().hex[:16]
        node = {"id": nid, "type": node_type, "name": name}
        if config:
            node.update(config)
        node["id"] = nid
        node["type"] = node_type
        # Anything canvas-shaped would make the editor try to draw it.
        for key in ("z", "g", "x", "y", "wires"):
            node.pop(key, None)

        self.nodes.append(node)
        self._index()
        print(f"Created config node [{node_type}] '{name}' -> {nid}")
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

    def update_many(self, node_ids, props):
        """Apply the same properties to several nodes at once.

        Every id is validated before anything is written, so one typo in the
        list changes nothing rather than half of it.

        Args:
            node_ids: Nodes to update.
            props: Properties to set on each of them.
        Returns:
            How many nodes were updated.
        """
        if not props:
            raise ValueError("update_many needs at least one property to set")
        missing = [nid for nid in node_ids if nid not in self.by_id]
        if missing:
            raise ValueError(f"Node IDs not found: {missing}")

        for nid in node_ids:
            self.by_id[nid].update(props)
        print(f"Updated {len(node_ids)} node(s): {', '.join(sorted(props))}")
        return len(node_ids)

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

    def set_node_enabled(self, node_id, enabled):
        """Enable or disable a node in place.

        Node-RED marks a disabled node with `d: true` and skips it at
        runtime; the node keeps its wiring and configuration, so this is the
        reversible way to take one node out of a flow.
        """
        node = self.by_id.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")
        if enabled:
            node.pop("d", None)
        else:
            node["d"] = True
        print(f"{'Enabled' if enabled else 'Disabled'} '{node.get('name') or node_id}'")

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

    def replace_node(self, node_id, node_type, name=None, config=None):
        """Swap a node's type in place, keeping its id, position, and wiring.

        Everything type-specific is dropped -- a switch's `rules` mean
        nothing to a change node -- so pass the new type's properties in
        `config`. Because the id survives, every wire into and out of the
        node survives with it.

        Args:
            node_id: The node to replace.
            node_type: The type to replace it with.
            name: New name; keeps the existing one if omitted.
            config: Properties for the new type.
        Returns:
            The node id, unchanged.
        """
        node = self.by_id.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")

        old_type = node.get("type")
        kept = {k: v for k, v in node.items() if k in self.KEEP_ON_REPLACE}
        kept["name"] = node.get("name", "") if name is None else name
        if config:
            kept.update(config)
        kept["id"] = node_id
        kept["type"] = node_type

        node.clear()
        node.update(kept)
        self._index()
        print(f"Replaced [{old_type}] with [{node_type}] on '{kept['name'] or node_id}'")
        return node_id

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

    def insert_between(self, source_id, target_id, new_node_id, output_index=None):
        """Splice an existing node into the wire from source to target.

        The wire source -> target is replaced by source -> new -> target. The
        node has to exist already: create and place it first, then splice it
        in.

        Args:
            source_id: Upstream node.
            target_id: Downstream node.
            new_node_id: The node to put between them.
            output_index: Only splice this output port of the source. By
                default every port wired to the target is rerouted.
        Returns:
            The list of source output ports that were rerouted.
        """
        src = self.by_id.get(source_id)
        if not src:
            raise ValueError(f"Source node {source_id} not found")
        if target_id not in self.by_id:
            raise ValueError(f"Target node {target_id} not found")
        new = self.by_id.get(new_node_id)
        if not new:
            raise ValueError(f"Node {new_node_id} not found")
        if new_node_id in (source_id, target_id):
            raise ValueError("insert_between needs a third node, distinct from source and target")

        ports = [
            i
            for i, wire_set in enumerate(src.get("wires", []) or [])
            if target_id in wire_set and (output_index is None or i == output_index)
        ]
        if not ports:
            where = "any output" if output_index is None else f"output {output_index}"
            raise ValueError(f"No wire from {source_id} to {target_id} on {where}")

        for i in ports:
            src["wires"][i].remove(target_id)
            self.wire_nodes(source_id, new_node_id, output_index=i)
        self.wire_nodes(new_node_id, target_id, output_index=0)

        print(
            f"Inserted '{new.get('name') or new_node_id}' between "
            f"'{src.get('name') or source_id}' and "
            f"'{self.by_id[target_id].get('name') or target_id}' on port(s) {ports}"
        )
        return ports

    def bypass_node(self, node_id, delete=False):
        """Route traffic around a node: wire its upstream to its downstream.

        Every wire that fed the node is redirected to whatever the node fed,
        and the node is left in place, configured but unreachable -- so
        putting it back is a matter of rewiring one edge. Pass delete=True to
        remove it once the traffic is routed past it.

        Returns:
            Counts of the wires rerouted, and whether the node was deleted.
        """
        node = self.by_id.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")

        downstream = [t for wire_set in node.get("wires", []) or [] for t in wire_set]
        inbound = [
            (n["id"], i)
            for n in self.nodes
            if isinstance(n, dict) and n.get("id") != node_id
            for i, wire_set in enumerate(n.get("wires", []) or [])
            if node_id in wire_set
        ]

        for src_id, port in inbound:
            self.unwire_nodes(src_id, node_id, output_index=port)
            for target_id in downstream:
                if target_id != src_id:
                    self.wire_nodes(src_id, target_id, output_index=port)

        label = node.get("name") or node_id
        if delete:
            self.delete_node(node_id)
        print(
            f"Bypassed '{label}': {len(inbound)} inbound wire(s) rerouted to "
            f"{len(downstream)} downstream node(s)" + (", node deleted" if delete else "")
        )
        return {"inbound": len(inbound), "downstream": len(downstream), "deleted": bool(delete)}

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
