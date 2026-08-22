"""Tab-level edits: create, rename, delete, reorder, enable."""

import uuid


class TabMixin:
    """Create and manipulate the flow tabs themselves.

    A tab is a plain entry in flows.json, and its position in that array is
    the order the editor shows it in -- which is why reorder_tabs rewrites
    array positions rather than setting a property.
    """

    def create_tab(self, label, disabled=False, info=""):
        """Create an empty tab. Returns the new tab's ID.

        Args:
            label: Tab name as shown in the editor.
            disabled: Create it disabled, so nothing on it runs.
            info: Markdown description shown in the editor's info panel.
        """
        tid = uuid.uuid4().hex[:16]
        self.nodes.append(
            {"id": tid, "type": "tab", "label": label, "disabled": bool(disabled), "info": info}
        )
        self._index()
        print(f"Created tab '{label}' (id={tid})")
        return tid

    def set_tab_label(self, tab_id, label):
        """Rename a tab."""
        tab = self._get_tab(tab_id)
        old = tab.get("label", "")
        tab["label"] = label
        self._index()
        print(f"Renamed tab '{old}' -> '{label}'")

    def set_tab_enabled(self, tab_id, enabled):
        """Enable or disable a whole tab. A disabled tab does not run."""
        tab = self._get_tab(tab_id)
        tab["disabled"] = not enabled
        print(f"{'Enabled' if enabled else 'Disabled'} tab '{tab.get('label', tab_id)}'")

    def set_tab_info(self, tab_id, info):
        """Set the tab's description (the editor's info panel)."""
        tab = self._get_tab(tab_id)
        tab["info"] = info
        print(f"Set the description on tab '{tab.get('label', tab_id)}' ({len(info)} chars)")

    def tab_contents(self, tab_id):
        """Everything living on a tab: nodes, groups, and subflow instances."""
        return [n for n in self.nodes if isinstance(n, dict) and n.get("z") == tab_id]

    def delete_tab(self, tab_id, delete_nodes=False):
        """Delete a tab. Refuses to discard a tab that still holds nodes.

        Args:
            tab_id: The tab to remove.
            delete_nodes: Delete everything on the tab along with it. Without
                this, a non-empty tab is left alone -- deleting it would
                orphan every node on it, which flows.json has no way to
                represent.

        Returns:
            How many nodes went with it.
        """
        tab = self._get_tab(tab_id)
        contents = self.tab_contents(tab_id)
        if contents and not delete_nodes:
            raise ValueError(
                f"Tab '{tab.get('label', tab_id)}' still holds {len(contents)} node(s). "
                f"Pass delete_nodes=true to delete them with it."
            )

        doomed = {n["id"] for n in contents if "id" in n}
        doomed.add(tab_id)
        self.nodes = [n for n in self.nodes if n.get("id") not in doomed]
        self._strip_references(doomed)
        self._index()
        print(f"Deleted tab '{tab.get('label', tab_id)}' and {len(contents)} node(s)")
        return len(contents)

    def reorder_tabs(self, tab_ids):
        """Reorder the editor's tab bar.

        Takes every tab id exactly once: a partial list would leave the
        position of the tabs you left out ambiguous.
        """
        current = [n for n in self.nodes if n.get("type") == "tab"]
        have = [t["id"] for t in current]
        if sorted(tab_ids) != sorted(have):
            missing = [t for t in have if t not in tab_ids]
            unknown = [t for t in tab_ids if t not in have]
            raise ValueError(
                f"reorder_tabs needs every tab id exactly once (missing={missing}, unknown={unknown})"
            )

        order = {tid: i for i, tid in enumerate(tab_ids)}
        ordered = sorted(current, key=lambda t: order[t["id"]])

        # Drop the re-sorted tabs back into the slots tabs already occupy, so
        # no other entry in the file shifts position.
        slots = [i for i, n in enumerate(self.nodes) if n.get("type") == "tab"]
        for slot, tab in zip(slots, ordered):
            self.nodes[slot] = tab

        self._index()
        print("Tab order: " + ", ".join(t.get("label", t["id"]) for t in ordered))

    def _get_tab(self, tab_id):
        tab = self.by_id.get(tab_id)
        if not tab or tab.get("type") != "tab":
            raise ValueError(f"Tab {tab_id} not found")
        return tab
