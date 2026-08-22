"""Read-only reports over a loaded flows.json.

Every method here prints and returns None -- that is the original
flows_util.py contract, kept deliberately so the vendored engine stays
drop-in compatible. The MCP layer captures stdout and turns it into the
tool result; see session.capture().
"""

import json
import re
from collections import Counter, defaultdict


class ReadMixin:
    """Query and describe the flow graph. Never mutates."""

    def summary(self):
        """Print high-level overview of the flows file."""
        content = [n for n in self.nodes if self._is_content_node(n)]
        groups = self.group_nodes
        named_groups = [g for g in groups if g.get("name")]
        styles = set(json.dumps(g.get("style", {}), sort_keys=True) for g in groups)

        grouped_ids = set()
        for g in groups:
            grouped_ids.update(g.get("nodes", []))
        ungrouped = [n for n in content if n["id"] not in grouped_ids and not n.get("g")]

        print(f"Flows file:  {self.path}")
        print(f"Tabs:        {len(self.tabs)}")
        print(f"Nodes:       {len(content)}")
        print(f"Groups:      {len(groups)} ({len(named_groups)} named)")
        print(f"Subflows:    {len(self.subflow_defs)}")
        print(f"Ungrouped:   {len(ungrouped)}")
        print(f"Styles:      {len(styles)} unique")
        print()
        print("Tabs:")
        for tid, label in sorted(self.tabs.items(), key=lambda x: x[1]):
            tab_nodes = [n for n in content if n.get("z") == tid]
            tab_groups = [g for g in groups if g.get("z") == tid]
            print(f"  {label:20s}  {len(tab_nodes):4d} nodes  {len(tab_groups):2d} groups")

    def groups(self):
        """List all groups organized by tab."""
        by_tab = defaultdict(list)
        for g in self.group_nodes:
            tab = self._tab_name(g)
            by_tab[tab].append(g)

        for tab in sorted(by_tab):
            print(f"\n{'=' * 60}")
            print(f"  {tab} ({len(by_tab[tab])} groups)")
            print(f"{'=' * 60}")
            for g in sorted(by_tab[tab], key=lambda x: x.get("name", "")):
                name = g.get("name", "(unnamed)")
                count = len(g.get("nodes", []))
                print(f"  {name:50s} {count:3d} nodes  id={g['id']}")

    def tab(self, name):
        """Show all nodes and groups on a tab (substring match)."""
        matches = [(tid, label) for tid, label in self.tabs.items() if name.lower() in label.lower()]
        if not matches:
            print(f"No tab matching '{name}'")
            return
        for tid, label in matches:
            print(f"\n{'=' * 60}")
            print(f"  Tab: {label}")
            print(f"{'=' * 60}")
            tab_groups = [g for g in self.group_nodes if g.get("z") == tid]
            for g in sorted(tab_groups, key=lambda x: x.get("name", "")):
                gname = g.get("name", "(unnamed)")
                children = [self.by_id[nid] for nid in g.get("nodes", []) if nid in self.by_id]
                print(f"\n  --- {gname} ({len(children)} nodes) ---")
                for c in children:
                    if c.get("type") != "group":
                        print(f"    {self._node_label(c)}")

            # Ungrouped nodes on this tab
            grouped_ids = set()
            for g in tab_groups:
                grouped_ids.update(g.get("nodes", []))
            ungrouped = [
                n
                for n in self.nodes
                if n.get("z") == tid
                and self._is_content_node(n)
                and n["id"] not in grouped_ids
                and not n.get("g")
            ]
            if ungrouped:
                print(f"\n  --- (ungrouped) ({len(ungrouped)} nodes) ---")
                for n in ungrouped:
                    print(f"    {self._node_label(n)}")

    def group(self, name):
        """Show nodes in a specific group (substring match on name)."""
        matches = [g for g in self.group_nodes if name.lower() in g.get("name", "").lower()]
        if not matches:
            print(f"No group matching '{name}'")
            return
        for g in matches:
            gname = g.get("name", "(unnamed)")
            tab = self._tab_name(g)
            children = [self.by_id[nid] for nid in g.get("nodes", []) if nid in self.by_id]
            print(f"\n  {gname} (Tab: {tab}, {len(children)} nodes, id={g['id']})")
            for c in children:
                if c.get("type") != "group":
                    print(f"    {self._node_label(c)}")

    def search(self, term):
        """Search node names, types, and entity IDs."""
        term_lower = term.lower()
        results = []
        for n in self.nodes:
            if not self._is_content_node(n):
                continue
            searchable = " ".join(
                [
                    n.get("name", ""),
                    n.get("type", ""),
                    n.get("entityid", "") or n.get("entity_id", ""),
                    n.get("topic", ""),
                ]
            ).lower()
            if term_lower in searchable:
                results.append(n)

        print(f"Found {len(results)} nodes matching '{term}':\n")
        by_tab = defaultdict(list)
        for n in results:
            by_tab[self._tab_name(n)].append(n)

        for tab in sorted(by_tab):
            print(f"  [{tab}]")
            for n in by_tab[tab]:
                grp = self.node_to_group.get(n["id"])
                grp_name = grp.get("name", "?") if grp else "(ungrouped)"
                print(f"    {self._node_label(n)}  | group: {grp_name}")

    def ungrouped(self):
        """Show content nodes not in any group."""
        grouped_ids = set()
        for g in self.group_nodes:
            grouped_ids.update(g.get("nodes", []))

        by_tab = defaultdict(list)
        for n in self.nodes:
            if not self._is_content_node(n):
                continue
            if n["id"] not in grouped_ids and not n.get("g"):
                by_tab[self._tab_name(n)].append(n)

        total = sum(len(v) for v in by_tab.values())
        print(f"Ungrouped nodes: {total}\n")
        for tab in sorted(by_tab):
            print(f"  [{tab}] ({len(by_tab[tab])} nodes)")
            for n in by_tab[tab]:
                print(f"    {self._node_label(n)}  id={n['id']}")

    def orphans(self):
        """Find nodes with no wire connections (potential dead code)."""
        # Build sets of nodes that have wires in/out
        has_wire_out = set()
        wired_to = set()
        for n in self.nodes:
            for wire_set in n.get("wires", []):
                if wire_set:
                    has_wire_out.add(n["id"])
                    wired_to.update(wire_set)

        # Trigger types that don't need input wires
        trigger_types = {
            "inject",
            "server-state-changed",
            "server-events",
            "mqtt in",
            "mqtt-in-dynamicsub",
            "poll-state",
            "http in",
            "websocket in",
            "cronplus",
            "link in",
            "comment",
        }

        orphans = []
        for n in self.nodes:
            if not self._is_content_node(n):
                continue
            ntype = n.get("type", "")
            has_in = n["id"] in wired_to
            has_out = n["id"] in has_wire_out
            if not has_in and ntype not in trigger_types and not has_out:
                orphans.append(n)

        print(f"Orphan nodes (no wires in or out, not triggers): {len(orphans)}\n")
        for n in orphans:
            tab = self._tab_name(n)
            grp = self.node_to_group.get(n["id"])
            grp_name = grp.get("name", "?") if grp else "(ungrouped)"
            print(f"  [{tab}] {self._node_label(n)}  | group: {grp_name}  id={n['id']}")

    def subflows(self):
        """List subflow definitions and their instance counts."""
        instance_count = Counter()
        for n in self.nodes:
            ntype = n.get("type", "")
            if ntype.startswith("subflow:"):
                sf_id = ntype.split(":")[1]
                instance_count[sf_id] += 1

        print(f"Subflows ({len(self.subflow_defs)}):\n")
        for sf_id, sf in sorted(self.subflow_defs.items(), key=lambda x: x[1].get("name", "")):
            name = sf.get("name", "(unnamed)")
            count = instance_count.get(sf_id, 0)
            print(f"  {name:40s}  {count:2d} instances  id={sf_id}")

    def styles(self):
        """Show group style distribution."""
        style_groups = defaultdict(list)
        for g in self.group_nodes:
            key = json.dumps(g.get("style", {}), sort_keys=True)
            style_groups[key].append(g.get("name", g["id"]))

        print(f"Group style distribution ({len(style_groups)} unique styles):\n")
        for style_json, names in sorted(style_groups.items(), key=lambda x: -len(x[1])):
            d = json.loads(style_json)
            fill = d.get("fill", "none")
            opacity = d.get("fill-opacity", "?")
            print(f"  {len(names)}x: fill={fill} opacity={opacity}")
            for name in sorted(names):
                print(f"       - {name}")

    def inspect(self, node_id):
        """Pretty-print full config of a node by ID."""
        node = self.by_id.get(node_id)
        if not node:
            print(f"Node {node_id} not found")
            return
        tab = self._tab_name(node)
        grp = self.node_to_group.get(node_id)
        grp_name = grp.get("name", "?") if grp else "(ungrouped)"
        print(f"Node: {node.get('name', '(unnamed)')}")
        print(f"  Type:  {node.get('type')}")
        print(f"  Tab:   {tab}")
        print(f"  Group: {grp_name}")
        print(f"  ID:    {node_id}")
        print(f"  Pos:   ({node.get('x', '?')}, {node.get('y', '?')})")
        print()
        print(json.dumps(node, indent=2))

    def connections(self, node_id):
        """Show what wires into and out of a node."""
        node = self.by_id.get(node_id)
        if not node:
            print(f"Node {node_id} not found")
            return

        name = node.get("name", node_id)
        print(f"Connections for: {name} [{node.get('type')}]\n")

        # Wires out
        wires = node.get("wires", [])
        print("  Outputs:")
        if not any(wires):
            print("    (none)")
        for i, wire_set in enumerate(wires):
            for tid in wire_set:
                target = self.by_id.get(tid)
                tlabel = self._node_label(target) if target else f"(missing: {tid})"
                print(f"    [{i}] -> {tlabel}")

        # Wires in
        print("\n  Inputs:")
        found_in = False
        for n in self.nodes:
            for i, wire_set in enumerate(n.get("wires", [])):
                if node_id in wire_set:
                    print(f"    <- [{i}] {self._node_label(n)}")
                    found_in = True
        if not found_in:
            print("    (none — this is a trigger/entry node)")

    def trace(self, node_id, max_depth=20):
        """Follow the wire path from a node through the flow.

        Prints the chain of connected nodes, handling branches.
        """
        node = self.by_id.get(node_id)
        if not node:
            print(f"Node {node_id} not found")
            return

        print(f"Trace from: {node.get('name', node_id)} [{node.get('type')}]\n")
        visited = set()

        def _walk(nid, depth, output_label=""):
            if nid in visited or depth > max_depth:
                if nid in visited:
                    print(f"{'  ' * depth}(cycle -> {self.by_id.get(nid, {}).get('name', nid)})")
                return
            visited.add(nid)
            n = self.by_id.get(nid)
            if not n:
                print(f"{'  ' * depth}(missing: {nid})")
                return

            prefix = f"{'  ' * depth}{output_label}"
            print(f"{prefix}{self._node_label(n)}")

            wires = n.get("wires", [])
            for i, wire_set in enumerate(wires):
                out_label = f"[{i}] " if len(wires) > 1 else ""
                for tid in wire_set:
                    _walk(tid, depth + 1, out_label)

        _walk(node_id, 0)

    def entities(self):
        """List all Home Assistant entity IDs referenced across all flows."""
        entity_refs = defaultdict(list)
        entity_fields = ["entity_id", "entityid", "entityidfilter"]
        for n in self.nodes:
            if not self._is_content_node(n):
                continue
            label = self._node_label(n)
            tab = self._tab_name(n)
            # Check direct entity fields
            for field in entity_fields:
                val = n.get(field)
                if val:
                    entities = val if isinstance(val, list) else [val]
                    for e in entities:
                        if isinstance(e, str) and "." in e and len(e) > 3:
                            entity_refs[e].append(f"[{tab}] {label}")
            # Check entityId array (HA service call nodes)
            for e in n.get("entityId", []):
                if isinstance(e, str) and "." in e:
                    entity_refs[e].append(f"[{tab}] {label}")
            # Check data field for entity references
            data = n.get("data", "")
            if isinstance(data, str):
                pattern = r'(?:entity[_.]id|image_entity)["\s:]*\[?"?([a-z_]+\.[a-z_0-9]+)'
                for match in re.findall(pattern, data):
                    entity_refs[match].append(f"[{tab}] {label}")

        print(f"Entity references ({len(entity_refs)} unique entities):\n")
        for entity in sorted(entity_refs):
            refs = entity_refs[entity]
            print(f"  {entity}")
            for ref in refs:
                print(f"    {ref}")
