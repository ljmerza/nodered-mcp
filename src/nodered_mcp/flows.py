#!/usr/bin/env python3
"""Node-RED flows.json reader, query engine, and editor.

This is the engine the MCP server drives. It is also usable directly as a
library or CLI, exactly like the original nodered-flows/flows_util.py it was
ported from -- the only change is that every host-specific path is now a
parameter instead of a module constant.

Path resolution, highest priority first:
    Flows(path=...)  >  --flows-path  >  $NODERED_FLOWS_PATH  >  error

CLI:
    python3 -m nodered_mcp.flows summary          # High-level overview
    python3 -m nodered_mcp.flows groups           # List all groups by tab
    python3 -m nodered_mcp.flows ungrouped        # Nodes not in any group
    python3 -m nodered_mcp.flows tab <name>       # All nodes on a tab
    python3 -m nodered_mcp.flows group <name>     # Nodes in a group (substring)
    python3 -m nodered_mcp.flows search <term>    # Search names, types, entities
    python3 -m nodered_mcp.flows orphans          # Nodes with no wires either way
    python3 -m nodered_mcp.flows subflows         # Subflows and usage counts
    python3 -m nodered_mcp.flows styles           # Group style distribution
    python3 -m nodered_mcp.flows inspect <id>     # Full config of a node
    python3 -m nodered_mcp.flows connections <id> # Wires into and out of a node
    python3 -m nodered_mcp.flows trace <id>       # Follow the wire path
    python3 -m nodered_mcp.flows entities         # All HA entities referenced
    python3 -m nodered_mcp.flows export <group>   # Export a group as JSON
    python3 -m nodered_mcp.flows deploy           # docker cp + restart

    Every command accepts --flows-path PATH.

Python:
    from nodered_mcp.flows import Flows
    f = Flows("/path/to/flows.json")
    f.summary(); f.groups(); f.tab("Upstairs"); f.search("motion")
    f.inspect(node_id); f.connections(node_id); f.trace(node_id)

    # Layout: never hand-pick coordinates
    ox, oy = f.free_region(tab_id, w=1600, h=300)   # claim empty canvas
    f.create_node(tab_id, "inject", "tick", x=ox, y=oy)
    f.layout_check()                                # lint the in-memory state

    # Modify and commit
    f.create_group("My Group", tab_id, [node_id_1, node_id_2])
    f.wire_nodes(src_id, dst_id)
    f.save()                       # REFUSES if the edit adds an overlap
    f.save(allow_overlap=True)     # override the layout gate
    f.deploy()                     # docker cp into the container + restart

Layout gate:
    save() lints the canvas before and after the edit and blocks on any NEW
    ERROR-severity finding (groups overlapping each other, a group box that
    doesn't cover its own nodes). Pre-existing problems never block. Warnings
    (node-on-node collisions, nodes sitting inside a foreign group box) print
    but do not block. See layout.py for the geometry and the CLI linter.
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict

from . import layout

# Every host-specific value below is a default that the caller, the CLI, or
# the environment can override. Nothing here is hardcoded to one machine.
FLOWS_PATH = os.environ.get("NODERED_FLOWS_PATH") or None
CONTAINER_NAME = os.environ.get("NODERED_CONTAINER", "nodered")
CONTAINER_FLOWS_PATH = os.environ.get("NODERED_CONTAINER_FLOWS_PATH", "/data/flows.json")


def _default_restart_cmd(container):
    """Command used to restart Node-RED after a deploy.

    NODERED_RESTART_CMD is a shell-style string; "{container}" in it is
    substituted. Unset falls back to a plain `docker restart <container>`.
    """
    raw = os.environ.get("NODERED_RESTART_CMD")
    if raw:
        return [part.format(container=container) for part in shlex.split(raw)]
    return ["docker", "restart", container]

DEFAULT_GROUP_STYLE = {
    "stroke": "#2e333a",
    "stroke-opacity": "1",
    "fill": "#2e333a",
    "fill-opacity": "0.75",
    "label": True,
    "label-position": "nw",
    "color": "#a4a4a4",
}

class LayoutError(Exception):
    """Raised by save() when an edit introduces a new layout collision."""


CONFIG_TYPES = {
    "server", "mqtt-broker", "websocket-listener", "websocket-client",
    "pushbullet-config", "GSheetConnection", "ha-device-config",
    "ha-entity-config", "gauth",
}


class Flows:
    def __init__(self, path=None, container=None, container_flows_path=None,
                 restart_cmd=None):
        self.path = path or FLOWS_PATH
        if not self.path:
            raise ValueError(
                "No flows.json path given. Pass path=..., set NODERED_FLOWS_PATH, "
                "or start the server with --flows-path."
            )
        self.container = container or CONTAINER_NAME
        self.container_flows_path = container_flows_path or CONTAINER_FLOWS_PATH
        self.restart_cmd = list(restart_cmd) if restart_cmd else _default_restart_cmd(self.container)
        self._load()

    def _load(self):
        with open(self.path) as f:
            self.nodes = json.load(f)
        self._index()

    def _index(self):
        """Build lookup indexes."""
        self.by_id = {n["id"]: n for n in self.nodes}
        self.tabs = {n["id"]: n.get("label", "") for n in self.nodes if n["type"] == "tab"}
        self.group_nodes = [n for n in self.nodes if n["type"] == "group"]
        self.subflow_defs = {n["id"]: n for n in self.nodes if n["type"] == "subflow"}

        # Map node_id -> group
        self.node_to_group = {}
        for g in self.group_nodes:
            for nid in g.get("nodes", []):
                self.node_to_group[nid] = g

    def _is_content_node(self, n):
        """True if the node is a flow content node (not meta/config)."""
        ntype = n.get("type", "")
        if ntype in ("tab", "group", "subflow"):
            return False
        if ntype in CONFIG_TYPES:
            return False
        if n.get("z", "") in self.subflow_defs:
            return False
        return True

    def _tab_name(self, node):
        return self.tabs.get(node.get("z", ""), "?")

    def _node_label(self, n):
        """Short display string for a node."""
        name = n.get("name", "")
        ntype = n.get("type", "")
        entity = n.get("entityid") or n.get("entity_id") or ""
        parts = [f"[{ntype}]"]
        if name:
            parts.append(name)
        if entity:
            parts.append(f"entity={entity}")
        return " ".join(parts)

    # ── Read commands ──

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
            print(f"\n{'='*60}")
            print(f"  {tab} ({len(by_tab[tab])} groups)")
            print(f"{'='*60}")
            for g in sorted(by_tab[tab], key=lambda x: x.get("name", "")):
                name = g.get("name", "(unnamed)")
                count = len(g.get("nodes", []))
                print(f"  {name:50s} {count:3d} nodes  id={g['id']}")

    def tab(self, name):
        """Show all nodes and groups on a tab (substring match)."""
        matches = [(tid, label) for tid, label in self.tabs.items()
                    if name.lower() in label.lower()]
        if not matches:
            print(f"No tab matching '{name}'")
            return
        for tid, label in matches:
            print(f"\n{'='*60}")
            print(f"  Tab: {label}")
            print(f"{'='*60}")
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
            ungrouped = [n for n in self.nodes
                         if n.get("z") == tid and self._is_content_node(n)
                         and n["id"] not in grouped_ids and not n.get("g")]
            if ungrouped:
                print(f"\n  --- (ungrouped) ({len(ungrouped)} nodes) ---")
                for n in ungrouped:
                    print(f"    {self._node_label(n)}")

    def group(self, name):
        """Show nodes in a specific group (substring match on name)."""
        matches = [g for g in self.group_nodes
                    if name.lower() in g.get("name", "").lower()]
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
            searchable = " ".join([
                n.get("name", ""),
                n.get("type", ""),
                n.get("entityid", "") or n.get("entity_id", ""),
                n.get("topic", ""),
            ]).lower()
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
            "inject", "server-state-changed", "server-events", "mqtt in",
            "mqtt-in-dynamicsub", "poll-state", "http in", "websocket in",
            "cronplus", "link in", "comment",
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
        entity_fields = [
            "entity_id", "entityid", "entityidfilter",
        ]
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
                import re
                pattern = r'(?:entity[_.]id|image_entity)["\s:]*\[?"?([a-z_]+\.[a-z_0-9]+)'
                for match in re.findall(pattern, data):
                    entity_refs[match].append(f"[{tab}] {label}")

        print(f"Entity references ({len(entity_refs)} unique entities):\n")
        for entity in sorted(entity_refs):
            refs = entity_refs[entity]
            print(f"  {entity}")
            for ref in refs:
                print(f"    {ref}")

    def export_group(self, name, output_path=None):
        """Export a group and all its nodes as standalone JSON.

        Args:
            name: Group name (substring match).
            output_path: File path to write. If None, prints to stdout.
        Returns:
            The exported nodes list.
        """
        matches = [g for g in self.group_nodes
                    if name.lower() in g.get("name", "").lower()]
        if not matches:
            print(f"No group matching '{name}'")
            return []
        if len(matches) > 1:
            print(f"Multiple groups match '{name}':")
            for g in matches:
                print(f"  {g.get('name', '?')} (id={g['id']})")
            print("Be more specific.")
            return []

        grp = matches[0]
        node_ids = set(grp.get("nodes", []))
        exported = [self.by_id[nid] for nid in node_ids if nid in self.by_id]

        if output_path:
            with open(output_path, "w") as f:
                json.dump(exported, f, indent=4)
            print(f"Exported {len(exported)} nodes from '{grp.get('name')}' to {output_path}")
        else:
            print(json.dumps(exported, indent=2))

        return exported

    def import_nodes(self, json_path, tab_id, group_id=None, remap_ids=True):
        """Import nodes from a JSON file into the live flows.

        Args:
            json_path: Path to a JSON file containing a list of node objects.
            tab_id: Tab to place the imported nodes on.
            group_id: Optional group to add the nodes to.
            remap_ids: If True, assigns new unique IDs to avoid conflicts.
                       Wire references are updated to match.
        Returns:
            Dict mapping old IDs to new IDs.
        """
        with open(json_path) as f:
            imported = json.load(f)

        if not isinstance(imported, list):
            raise ValueError("JSON file must contain a list of node objects")

        id_map = {}
        if remap_ids:
            # Generate new IDs
            for n in imported:
                old_id = n["id"]
                new_id = uuid.uuid4().hex[:16]
                id_map[old_id] = new_id
                n["id"] = new_id

            # Remap wire references
            for n in imported:
                new_wires = []
                for wire_set in n.get("wires", []):
                    new_wires.append([id_map.get(w, w) for w in wire_set])
                n["wires"] = new_wires

        # Set tab and group
        for n in imported:
            n["z"] = tab_id
            if group_id:
                n["g"] = group_id

        self.nodes.extend(imported)

        # Add to group's node list
        if group_id:
            grp = self.by_id.get(group_id)
            if grp and grp.get("type") == "group":
                for n in imported:
                    grp.setdefault("nodes", []).append(n["id"])

        self._index()
        print(f"Imported {len(imported)} nodes onto tab {self.tabs.get(tab_id, tab_id)}")
        if group_id:
            grp = self.by_id.get(group_id)
            print(f"  Added to group: {grp.get('name', group_id) if grp else group_id}")
        if id_map:
            print(f"  Remapped {len(id_map)} node IDs")
        return id_map

    # ── Write commands ──

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
            others = [g for g in self.nodes
                      if isinstance(g, dict) and g.get("type") == "group"
                      and g.get("z") == tab_id and g["id"] != gid and not g.get("g")]
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

    def set_node_name(self, node_id, name):
        """Rename a node."""
        if node_id not in self.by_id:
            raise ValueError(f"Node {node_id} not found")
        self.by_id[node_id]["name"] = name

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
        node = {
            "id": nid,
            "type": node_type,
            "z": tab_id,
            "name": name,
            "x": x,
            "y": y,
            "wires": [[]],
        }
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
            subprocess.run(
                ["docker", "cp", tmp, f"{self.container}:{self.container_flows_path}"],
                check=True,
            )
        finally:
            os.unlink(tmp)
        subprocess.run(self.restart_cmd, check=True)
        print(f"Deployed and restarted Node-RED ({' '.join(self.restart_cmd)})")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # --flows-path is accepted anywhere in the args so it can be tacked on the
    # end of an existing command line.
    path = FLOWS_PATH
    if "--flows-path" in argv:
        i = argv.index("--flows-path")
        if i + 1 >= len(argv):
            print("--flows-path needs a value")
            return 2
        path = argv[i + 1]
        del argv[i:i + 2]

    if not argv:
        print(__doc__)
        return 0

    cmd = argv[0]
    arg = argv[1] if len(argv) > 1 else None

    try:
        f = Flows(path)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}")
        return 2

    no_arg = {
        "summary": f.summary, "groups": f.groups, "ungrouped": f.ungrouped,
        "orphans": f.orphans, "subflows": f.subflows, "styles": f.styles,
        "entities": f.entities, "deploy": f.deploy,
    }
    one_arg = {
        "tab": f.tab, "group": f.group, "search": f.search, "inspect": f.inspect,
        "connections": f.connections, "trace": f.trace,
    }

    if cmd in no_arg:
        no_arg[cmd]()
    elif cmd in one_arg and arg:
        one_arg[cmd](arg)
    elif cmd == "export" and arg:
        f.export_group(arg, argv[2] if len(argv) > 2 else None)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
