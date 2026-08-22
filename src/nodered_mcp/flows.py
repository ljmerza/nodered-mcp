#!/usr/bin/env python3
"""Node-RED flows.json reader, query engine, and editor.

This is the engine the MCP server drives. It is also usable directly as a
library or CLI, exactly like the nodered-flows/flows_util.py it was ported
from -- the difference is that every host-specific path is a parameter, and
the 1100-line original is split across focused modules:

    constants.py   defaults, the house group style, LayoutError
    reports.py     ReadMixin      -- summary, tab, group, search, trace, ...
    nodes.py       NodeEditMixin  -- create/update/delete/wire nodes
    groups.py      GroupMixin     -- create and populate group boxes
    placement.py   LayoutMixin    -- claim free canvas, measure and refit boxes
    transfer.py    TransferMixin  -- import and export node sets
    persist.py     PersistMixin   -- save, deploy, and the layout gate
    layout.py      the canvas geometry and linter, ported from the NR editor

`Flows` composes those mixins, so the public API is flat and unchanged:
f.summary(), f.create_node(), f.free_region(), f.save() all still work.

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
    ox, oy = f.free_region(tab_id, w=1600, h=300)   # claim empty canvas
    f.create_node(tab_id, "inject", "tick", x=ox, y=oy)
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
import sys

from .constants import (
    CONFIG_TYPES,
    CONTAINER_FLOWS_PATH,
    CONTAINER_NAME,
    DEFAULT_GROUP_STYLE,
    FLOWS_PATH,
    LayoutError,
    default_restart_cmd,
)
from .groups import GroupMixin
from .nodes import NodeEditMixin
from .persist import PersistMixin
from .placement import LayoutMixin
from .reports import ReadMixin
from .tabs import TabMixin
from .transfer import TransferMixin

# Re-exported so `from nodered_mcp.flows import DEFAULT_GROUP_STYLE, LayoutError`
# keeps working for anything ported from the original single-file module.
__all__ = ["DEFAULT_GROUP_STYLE", "CONFIG_TYPES", "FLOWS_PATH", "Flows", "LayoutError", "main"]


class Flows(ReadMixin, NodeEditMixin, GroupMixin, TabMixin, LayoutMixin, TransferMixin, PersistMixin):
    """One loaded flows.json, with the whole editing surface attached.

    The mixin order is presentation-only -- no method is defined twice, so
    there is no MRO subtlety to reason about.
    """

    def __init__(self, path=None, container=None, container_flows_path=None, restart_cmd=None):
        self.path = path or FLOWS_PATH
        if not self.path:
            raise ValueError(
                "No flows.json path given. Pass path=..., set NODERED_FLOWS_PATH, "
                "or start the server with --flows-path."
            )
        self.container = container or CONTAINER_NAME
        self.container_flows_path = container_flows_path or CONTAINER_FLOWS_PATH
        self.restart_cmd = list(restart_cmd) if restart_cmd else default_restart_cmd(self.container)
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
        if self._is_config_node(n):
            return False
        if n.get("z", "") in self.subflow_defs:
            return False
        return True

    def _is_config_node(self, n):
        """True if the node is a config node: real, but never on a canvas.

        Structural rather than a type allowlist, so an mqtt-broker and a
        tls-config both qualify even though only one of them is named in
        CONFIG_TYPES: a config node belongs to no tab and has no coordinates.
        """
        if not isinstance(n, dict):
            return False
        if n.get("type") in ("tab", "group", "subflow"):
            return False
        if n.get("type") in CONFIG_TYPES:
            return True
        return not n.get("z") and "x" not in n and "y" not in n

    def _references_to(self, node_id):
        """Nodes holding node_id in one of their property values.

        This is how a config node is used: the referring node stores the
        config node's id under a property like `server` or `broker`.
        """
        out = []
        for n in self.nodes:
            if not isinstance(n, dict) or n.get("id") == node_id:
                continue
            for key, value in n.items():
                if key in ("id", "wires", "nodes") or value != node_id:
                    continue
                out.append(
                    {
                        "id": n["id"],
                        "type": n.get("type"),
                        "name": n.get("name", ""),
                        "property": key,
                    }
                )
                break
        return out

    def _strip_references(self, ids):
        """Scrub a set of deleted ids out of wires, groups, and link targets."""
        for n in self.nodes:
            if not isinstance(n, dict):
                continue
            for wire_set in n.get("wires", []) or []:
                for dead in [w for w in wire_set if w in ids]:
                    wire_set.remove(dead)
            if n.get("type") == "group" and n.get("nodes"):
                n["nodes"] = [x for x in n["nodes"] if x not in ids]
            if isinstance(n.get("links"), list):
                n["links"] = [x for x in n["links"] if x not in ids]

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
        del argv[i : i + 2]

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
        "summary": f.summary,
        "groups": f.groups,
        "ungrouped": f.ungrouped,
        "orphans": f.orphans,
        "subflows": f.subflows,
        "styles": f.styles,
        "entities": f.entities,
        "deploy": f.deploy,
    }
    one_arg = {
        "tab": f.tab,
        "group": f.group,
        "search": f.search,
        "inspect": f.inspect,
        "connections": f.connections,
        "trace": f.trace,
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
