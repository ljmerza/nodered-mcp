"""Moving node sets in and out of a flows file."""

import json
import uuid


class TransferMixin:
    """Export a group to JSON, and import a JSON node list back in."""

    def export_group(self, name, output_path=None):
        """Export a group and all its nodes as standalone JSON.

        Args:
            name: Group name (substring match).
            output_path: File path to write. If None, prints to stdout.
        Returns:
            The exported nodes list.
        """
        matches = [g for g in self.group_nodes if name.lower() in g.get("name", "").lower()]
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
