import json

import pytest

from conftest import DEBUG, FUNC, GROUP, HA_NODE, INJECT, ORPHAN, SWITCH, TAB
from nodered_mcp import server
from nodered_mcp.session import StaleFlowsError

# The decorators wrap each function in a FunctionTool; .fn is the original.
query = server.nodered_query.fn
find_nodes = server.nodered_find_nodes.fn
get_node = server.nodered_get_node.fn
edit = server.nodered_edit.fn
group = server.nodered_group.fn
tab = server.nodered_tab.fn
layout_tool = server.nodered_layout.fn
session_tool = server.nodered_session.fn


# ── reads ──


def test_tabs_lists_the_tab(sess):
    assert TAB in query(op="tabs")
    assert "Demo" in query(op="tabs")


def test_summary_counts_nodes(sess):
    out = query(op="summary")
    assert "Tabs:" in out and "Demo" in out


def test_search_finds_by_name(sess):
    assert "compute" in query(op="search", target="compute")


def test_entities_lists_ha_references(sess):
    assert "sensor.demo_temperature" in query(op="entities")


def test_orphans_finds_the_dead_node(sess):
    out = query(op="orphans")
    assert "dead end" in out


def test_trace_follows_the_chain(sess):
    out = query(op="trace", target=INJECT)
    assert "compute" in out and "out" in out


def test_query_requires_target_where_needed(sess):
    with pytest.raises(ValueError, match="requires the 'target'"):
        query(op="search")


def test_find_nodes_filters_by_type(sess):
    res = find_nodes(node_type="function")
    assert res["count"] == 1
    assert res["nodes"][0]["id"] == FUNC
    assert res["nodes"][0]["group"] == "Demo Chain"


def test_find_nodes_respects_limit(sess):
    res = find_nodes(limit=2)
    assert len(res["nodes"]) == 2
    assert res["truncated"] is True


def test_get_node_returns_wiring_context(sess):
    res = get_node(node_id=FUNC)
    assert res["node"]["func"] == "return msg;"
    assert res["context"]["group"] == "Demo Chain"
    assert [w["from"] for w in res["wires_in"]] == [INJECT]
    assert [w["to"] for w in res["wires_out"]] == [DEBUG]


def test_get_node_rejects_unknown_id(sess):
    with pytest.raises(ValueError, match="not found"):
        get_node(node_id="nope")


# ── edits stay in memory until committed ──


def test_edits_are_not_written_until_save(sess, on_disk):
    edit(op="rename_node", node_id=FUNC, name="renamed")
    assert sess.journal.dirty
    assert not any(n.get("name") == "renamed" for n in on_disk())

    session_tool(op="save")
    assert not sess.journal.dirty
    assert any(n.get("name") == "renamed" for n in on_disk())


def test_status_reports_pending_edits(sess):
    edit(op="update_node", node_id=FUNC, props={"func": "return null;"})
    st = session_tool(op="status")
    assert st["dirty"] is True
    assert len(st["pending_edits"]) == 1
    assert st["container"] == "test-nodered"


def test_reload_discards_pending_edits(sess):
    edit(op="rename_node", node_id=FUNC, name="scratch")
    res = session_tool(op="reload")
    assert "Discarded 1 pending edit" in res["output"]
    assert sess.flows.get_node(FUNC)["name"] == "compute"


def test_create_wire_and_save_roundtrip(sess, on_disk):
    origin = layout_tool(op="free_region", tab_id=TAB, w=600, h=120)
    a = edit(op="create_node", tab_id=TAB, node_type="inject", name="new tick", x=origin["x"], y=origin["y"])[
        "node_id"
    ]
    b = edit(
        op="create_node", tab_id=TAB, node_type="debug", name="new out", x=origin["x"] + 220, y=origin["y"]
    )["node_id"]
    edit(op="wire", source_id=a, target_id=b)

    assert session_tool(op="save")["ok"] is True
    disk = {n["id"]: n for n in on_disk()}
    assert disk[a]["wires"] == [[b]]
    assert disk[b]["name"] == "new out"


def test_delete_node_removes_inbound_wires(sess):
    edit(op="delete_node", node_id=DEBUG)
    assert sess.flows.get_node(DEBUG) is None
    assert sess.flows.get_node(FUNC)["wires"] == [[]]


def test_unwire_without_index_cuts_every_port(sess):
    edit(op="unwire", source_id=INJECT, target_id=FUNC)
    assert sess.flows.get_node(INJECT)["wires"] == [[]]


def test_update_node_requires_props(sess):
    with pytest.raises(ValueError, match="requires the 'props'"):
        edit(op="update_node", node_id=FUNC)


def test_export_group_returns_members_inline(sess):
    res = edit(op="export_group", name="Demo Chain")
    assert res["count"] == 3
    assert {n["id"] for n in res["nodes"]} == {INJECT, FUNC, DEBUG}


def test_import_nodes_remaps_ids(sess, tmp_path):
    payload = [{"id": "old1", "type": "debug", "name": "imported", "x": 100, "y": 900, "wires": [[]]}]
    src = tmp_path / "import.json"
    src.write_text(json.dumps(payload))
    res = edit(op="import_nodes", json_path=str(src), tab_id=TAB)
    assert res["ok"] and "old1" in res["id_map"]
    assert sess.flows.get_node(res["id_map"]["old1"])["name"] == "imported"


# ── groups ──


def test_create_group_uses_the_house_dark_style(sess):
    res = group(op="create", name="Loose", tab_id=TAB, node_ids=[SWITCH, ORPHAN, HA_NODE])
    gid = res["group_id"]
    style = sess.flows.get_node(gid)["style"]
    assert style["fill"] == "#2e333a"
    assert style["fill-opacity"] == "0.75"


def test_created_group_box_covers_its_members(sess):
    gid = group(op="create", name="Loose", tab_id=TAB, node_ids=[SWITCH, ORPHAN, HA_NODE])["group_id"]
    assert layout_tool(op="check")["errors"] == 0
    b = group(op="bounds", group_id=gid)["bounds"]
    g = sess.flows.get_node(gid)
    assert (round(b["x1"]), round(b["y1"])) == (g["x"], g["y"])


def test_move_node_out_of_group(sess):
    group(op="move_node", node_id=DEBUG, group_id=None)
    assert "g" not in sess.flows.get_node(DEBUG)
    assert DEBUG not in sess.flows.get_node(GROUP)["nodes"]


def test_shift_group_moves_members_too(sess):
    before = sess.flows.get_node(INJECT)["x"]
    group(op="shift", group_id=GROUP, dx=100, dy=0)
    assert sess.flows.get_node(INJECT)["x"] == before + 100


def test_refit_reports_no_change_on_a_tight_box(sess):
    assert group(op="refit", group_id=GROUP)["changed"] is False


def test_group_ops_validate_required_args(sess):
    with pytest.raises(ValueError, match="requires the 'node_ids'"):
        group(op="create", name="X", tab_id=TAB)


# ── layout ──


def test_free_region_lands_clear_of_existing_content(sess):
    origin = layout_tool(op="free_region", tab_id=TAB, w=400, h=100)
    rects = layout_tool(op="occupied", tab_id=TAB)["rects"]
    bottom = max(r["y2"] for r in rects)
    assert origin["y"] > bottom


def test_free_region_needs_positive_size(sess):
    with pytest.raises(ValueError, match="positive w and h"):
        layout_tool(op="free_region", tab_id=TAB)


def test_check_is_clean_on_the_fixture(sess):
    res = layout_tool(op="check")
    assert res["ok"] and res["errors"] == 0


def test_fix_refuses_while_edits_are_pending(sess):
    edit(op="rename_node", node_id=FUNC, name="pending")
    with pytest.raises(ValueError, match="uncommitted edit"):
        layout_tool(op="fix")


def test_fix_dry_run_writes_nothing(sess, on_disk):
    before = on_disk()
    res = layout_tool(op="fix", apply=False)
    assert res["applied"] is False
    assert on_disk() == before


# ── the layout gate ──


def _overlapping_group(sess):
    """Build a second group whose box sits on top of the fixture's group."""
    g = sess.flows.get_node(GROUP)
    ids = []
    for i, nid in enumerate((SWITCH, ORPHAN)):
        sess.flows.update_node(nid, x=g["x"] + 60 + i * 120, y=g["y"] + 40)
        ids.append(nid)
    return group(op="create", name="Collider", tab_id=TAB, node_ids=ids, reflow=False)["group_id"]


def test_save_is_refused_when_the_edit_overlaps_groups(sess, on_disk):
    _overlapping_group(sess)
    res = session_tool(op="save")
    assert res["ok"] is False
    assert res["error"] == "layout_gate"
    assert not any(n.get("name") == "Collider" for n in on_disk())


def test_allow_overlap_overrides_the_gate(sess, on_disk):
    _overlapping_group(sess)
    assert session_tool(op="save", allow_overlap=True)["ok"] is True
    assert any(n.get("name") == "Collider" for n in on_disk())


def test_reflow_avoids_the_overlap_without_an_override(sess):
    g = sess.flows.get_node(GROUP)
    for i, nid in enumerate((SWITCH, ORPHAN)):
        sess.flows.update_node(nid, x=g["x"] + 60 + i * 120, y=g["y"] + 40)
    group(op="create", name="Reflowed", tab_id=TAB, node_ids=[SWITCH, ORPHAN], reflow=True)
    assert session_tool(op="save")["ok"] is True


# ── the staleness guard ──


def test_save_is_refused_when_the_file_changed_underneath(sess, flows_file, on_disk):
    edit(op="rename_node", node_id=FUNC, name="mine")
    nodes = on_disk()
    for n in nodes:
        if n["id"] == FUNC:
            n["name"] = "theirs"
    flows_file.write_text(json.dumps(nodes, indent=4))

    res = session_tool(op="save")
    assert res["ok"] is False and res["error"] == "stale_file"
    assert any(n.get("name") == "theirs" for n in on_disk())


def test_force_overwrites_a_changed_file(sess, flows_file, on_disk):
    edit(op="rename_node", node_id=FUNC, name="mine")
    nodes = on_disk()
    for n in nodes:
        if n["id"] == FUNC:
            n["name"] = "theirs"
    flows_file.write_text(json.dumps(nodes, indent=4))

    assert session_tool(op="save", force=True)["ok"] is True
    assert any(n.get("name") == "mine" for n in on_disk())


def test_session_assert_writable_raises_directly(sess, flows_file):
    sess.flows  # load
    flows_file.write_text(flows_file.read_text() + "\n")
    with pytest.raises(StaleFlowsError):
        sess.assert_writable()


# ── deploy ──


def test_deploy_runs_the_configured_restart_command(sess, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr("nodered_mcp.persist.subprocess.run", fake_run)
    edit(op="rename_node", node_id=FUNC, name="deployed")
    assert session_tool(op="deploy")["ok"] is True

    assert calls[0][:2] == ["docker", "cp"]
    assert calls[0][3].startswith("test-nodered:/data/flows.json")
    assert calls[-1] == ["true"]


# ── tabs ──


def test_create_tab_shows_up_in_the_tab_list(sess):
    out = tab(op="create", label="Second")
    assert out["ok"]
    assert "Second" in query(op="tabs")
    assert out["tab_id"] in query(op="tabs")


def test_rename_tab(sess):
    tab(op="rename", tab_id=TAB, label="Renamed")
    assert "Renamed" in query(op="tabs")


def test_set_tab_enabled_toggles_disabled(sess):
    tab(op="set_enabled", tab_id=TAB, enabled=False)
    assert get_node(TAB)["node"]["disabled"] is True
    tab(op="set_enabled", tab_id=TAB, enabled=True)
    assert get_node(TAB)["node"]["disabled"] is False


def test_delete_tab_refuses_to_orphan_its_nodes(sess):
    with pytest.raises(ValueError, match="delete_nodes"):
        tab(op="delete", tab_id=TAB)
    assert get_node(INJECT)["node"]["id"] == INJECT


def test_delete_tab_with_its_nodes(sess):
    out = tab(op="delete", tab_id=TAB, delete_nodes=True)
    assert out["nodes_deleted"] == 7
    assert query(op="tabs") == "(no tabs)"
    assert sess.flows.get_node(INJECT) is None


def test_delete_empty_tab_needs_no_flag(sess):
    new_tab = tab(op="create", label="Empty")["tab_id"]
    tab(op="delete", tab_id=new_tab)
    assert "Empty" not in query(op="tabs")


def test_reorder_tabs_rewrites_the_order(sess):
    second = tab(op="create", label="Second")["tab_id"]
    tab(op="reorder", tab_ids=[second, TAB])
    order = [n["id"] for n in sess.flows.nodes if n.get("type") == "tab"]
    assert order == [second, TAB]


def test_reorder_tabs_needs_every_tab(sess):
    tab(op="create", label="Second")
    with pytest.raises(ValueError, match="every tab id exactly once"):
        tab(op="reorder", tab_ids=[TAB])


def test_tab_ops_validate_required_args(sess):
    with pytest.raises(ValueError, match="label"):
        tab(op="create")
    with pytest.raises(ValueError, match="tab_id"):
        tab(op="rename", label="x")


# ── config nodes ──


def test_create_config_node_has_no_canvas_presence(sess):
    cid = edit(op="create_config_node", node_type="server", name="Home Assistant")["node_id"]
    node = get_node(cid)["node"]
    assert node["type"] == "server"
    assert not any(key in node for key in ("z", "x", "y", "wires", "g"))


def test_configs_report_counts_references(sess):
    cid = edit(op="create_config_node", node_type="server", name="Home Assistant")["node_id"]
    edit(
        op="create_node",
        tab_id=TAB,
        node_type="api-call-service",
        name="turn on",
        config={"server": cid},
        x=100,
        y=900,
    )
    out = query(op="configs")
    assert "Home Assistant" in out
    assert "1 reference(s)" in out
    assert "via .server" in out


def test_config_nodes_stay_out_of_find_nodes(sess):
    edit(op="create_config_node", node_type="server", name="Home Assistant")
    names = [n["name"] for n in find_nodes()["nodes"]]
    assert "Home Assistant" not in names


def test_an_unlisted_config_type_is_not_content_either(sess):
    """CONFIG_TYPES names a handful of types; being off that list is not proof
    of being flow content. A node with no tab and no coordinates is a config
    node whatever it calls itself."""
    cid = edit(op="create_config_node", node_type="tls-config", name="certs")["node_id"]
    assert cid not in [n["id"] for n in find_nodes()["nodes"]]
    assert "certs" not in query(op="ungrouped")
    assert "certs" in query(op="configs")


# ── enable, bulk update, splice, replace ──


def test_set_enabled_round_trips_the_d_flag(sess):
    edit(op="set_enabled", node_id=FUNC, enabled=False)
    assert get_node(FUNC)["node"]["d"] is True
    edit(op="set_enabled", node_id=FUNC, enabled=True)
    assert "d" not in get_node(FUNC)["node"]


def test_update_many_applies_to_every_node(sess):
    out = edit(op="update_many", node_ids=[SWITCH, ORPHAN], props={"info": "touched"})
    assert out["count"] == 2
    assert get_node(SWITCH)["node"]["info"] == "touched"
    assert get_node(ORPHAN)["node"]["info"] == "touched"


def test_update_many_is_atomic_on_a_bad_id(sess):
    with pytest.raises(ValueError, match="not found"):
        edit(op="update_many", node_ids=[SWITCH, "nope"], props={"info": "touched"})
    assert "info" not in get_node(SWITCH)["node"]


def test_insert_between_reroutes_the_wire(sess):
    new_id = edit(op="create_node", tab_id=TAB, node_type="delay", name="wait", x=260, y=100)["node_id"]
    out = edit(op="insert_between", source_id=INJECT, target_id=FUNC, new_node_id=new_id)
    assert out["ports"] == [0]
    assert get_node(INJECT)["node"]["wires"] == [[new_id]]
    assert get_node(new_id)["node"]["wires"] == [[FUNC]]


def test_insert_between_rejects_a_wire_that_is_not_there(sess):
    new_id = edit(op="create_node", tab_id=TAB, node_type="delay", name="wait", x=260, y=900)["node_id"]
    with pytest.raises(ValueError, match="No wire from"):
        edit(op="insert_between", source_id=INJECT, target_id=DEBUG, new_node_id=new_id)


def test_bypass_wires_upstream_straight_to_downstream(sess):
    out = edit(op="bypass", node_id=FUNC)
    assert out["inbound"] == 1 and out["downstream"] == 1
    assert get_node(INJECT)["node"]["wires"] == [[DEBUG]]
    assert get_node(FUNC)["node"]["wires"] == [[DEBUG]]


def test_bypass_can_delete_the_node_too(sess):
    edit(op="bypass", node_id=FUNC, delete=True)
    assert sess.flows.get_node(FUNC) is None
    assert get_node(INJECT)["node"]["wires"] == [[DEBUG]]


def test_replace_node_keeps_identity_and_wiring(sess):
    before = get_node(FUNC)["node"]
    edit(op="replace_node", node_id=FUNC, node_type="change", config={"rules": []})
    after = get_node(FUNC)["node"]
    assert after["id"] == FUNC and after["type"] == "change"
    assert after["wires"] == before["wires"]
    assert (after["x"], after["y"]) == (before["x"], before["y"])
    assert "func" not in after
    assert get_node(INJECT)["node"]["wires"] == [[FUNC]]


# ── deleting a group ──


def test_delete_group_releases_its_members(sess):
    out = group(op="delete", group_id=GROUP)
    assert out["members"] == 3
    assert sess.flows.get_node(INJECT) is not None
    assert "g" not in get_node(INJECT)["node"]
    assert get_node(INJECT)["context"]["group"] is None


def test_delete_group_can_take_its_members_with_it(sess):
    group(op="delete", group_id=GROUP, delete_nodes=True)
    for nid in (INJECT, FUNC, DEBUG, GROUP):
        assert sess.flows.get_node(nid) is None


# ── diff ──


def test_diff_is_empty_on_a_clean_session(sess):
    out = session_tool(op="diff")
    assert out["dirty"] is False
    assert out["added"] == [] and out["removed"] == [] and out["changed"] == []


def test_diff_reports_added_removed_and_changed(sess):
    new_id = edit(op="create_node", tab_id=TAB, node_type="debug", name="extra", x=100, y=900)["node_id"]
    edit(op="delete_node", node_id=ORPHAN)
    edit(op="rename_node", node_id=SWITCH, name="renamed gate")

    out = session_tool(op="diff")
    assert out["dirty"] is True
    assert [a["id"] for a in out["added"]] == [new_id]
    assert [r["id"] for r in out["removed"]] == [ORPHAN]
    changed = {c["id"]: c["props"] for c in out["changed"]}
    assert changed[SWITCH]["name"] == {"from": "loose gate", "to": "renamed gate"}


def test_diff_clears_after_a_save(sess):
    edit(op="rename_node", node_id=SWITCH, name="renamed gate")
    assert session_tool(op="diff")["dirty"] is True
    session_tool(op="save")
    assert session_tool(op="diff")["dirty"] is False


# ── undo ──


def test_undo_reverts_the_last_edit(sess):
    new_id = edit(op="create_node", tab_id=TAB, node_type="debug", name="extra", x=100, y=900)["node_id"]
    out = session_tool(op="undo")
    assert out["ok"] is True
    assert out["undone"] == [f"create_node debug 'extra' -> {new_id}"]
    assert sess.flows.get_node(new_id) is None
    assert session_tool(op="status")["pending_edits"] == []


def test_undo_walks_back_one_call_at_a_time(sess):
    edit(op="rename_node", node_id=SWITCH, name="first")
    edit(op="rename_node", node_id=SWITCH, name="second")
    session_tool(op="undo")
    assert get_node(SWITCH)["node"]["name"] == "first"
    session_tool(op="undo")
    assert get_node(SWITCH)["node"]["name"] == "loose gate"


def test_undo_restores_a_whole_group_delete(sess):
    group(op="delete", group_id=GROUP, delete_nodes=True)
    session_tool(op="undo")
    assert sess.flows.get_node(INJECT) is not None
    assert get_node(INJECT)["context"]["group"] == "Demo Chain"


def test_undo_reports_when_there_is_nothing_to_undo(sess):
    out = session_tool(op="undo")
    assert out["ok"] is False
    assert "Nothing to undo" in out["message"]


def test_a_rejected_edit_leaves_no_undo_step(sess):
    with pytest.raises(ValueError, match="not found"):
        edit(op="rename_node", node_id="nope", name="x")
    assert session_tool(op="status")["undo_steps"] == 0


def test_save_clears_the_undo_stack(sess):
    edit(op="rename_node", node_id=SWITCH, name="renamed gate")
    session_tool(op="save")
    assert session_tool(op="status")["undo_steps"] == 0
    assert session_tool(op="undo")["ok"] is False


# ── arranging a tab ──


@pytest.fixture
def three_groups(sess):
    """The fixture tab with three groups, and one node deliberately left loose."""
    group(op="create", name="Gate", tab_id=TAB, node_ids=[SWITCH])
    group(op="create", name="Dead End", tab_id=TAB, node_ids=[ORPHAN])
    return sess


def test_arrange_needs_at_least_two_groups(sess):
    out = layout_tool(op="arrange", tab_id=TAB)
    assert out["moves"] == 0
    assert "fewer than two groups" in out["output"]


def test_arrange_dry_run_moves_nothing(three_groups):
    before = {g["id"]: (g["x"], g["y"]) for g in three_groups.flows.group_nodes}
    out = layout_tool(op="arrange", tab_id=TAB)
    assert out["applied"] is False
    assert "DRY RUN" in out["output"]
    assert {g["id"]: (g["x"], g["y"]) for g in three_groups.flows.group_nodes} == before


def test_arrange_packs_the_groups_into_columns(three_groups):
    out = layout_tool(op="arrange", tab_id=TAB, apply=True)
    assert out["applied"] is True
    assert out["columns"] >= 1
    assert out["moves"]
    for move in out["moves"]:
        moved = three_groups.flows.by_id[move["group_id"]]
        assert [moved["x"], moved["y"]] == move["to"]


def test_arrange_never_moves_an_ungrouped_node(three_groups):
    before = (three_groups.flows.by_id[HA_NODE]["x"], three_groups.flows.by_id[HA_NODE]["y"])
    layout_tool(op="arrange", tab_id=TAB, apply=True)
    after = (three_groups.flows.by_id[HA_NODE]["x"], three_groups.flows.by_id[HA_NODE]["y"])
    assert after == before


def test_arrange_introduces_no_new_layout_errors(three_groups):
    layout_tool(op="arrange", tab_id=TAB, apply=True)
    assert layout_tool(op="check")["errors"] == 0


def test_arrange_honours_an_explicit_column_count(three_groups):
    assert layout_tool(op="arrange", tab_id=TAB, columns=3)["columns"] == 3


def test_arrange_rejects_an_unknown_sort(three_groups):
    with pytest.raises(ValueError, match="sort must be one of"):
        layout_tool(op="arrange", tab_id=TAB, sort="sideways")


def test_arrange_predicts_the_footprint_it_produces(three_groups):
    predicted = layout_tool(op="arrange", tab_id=TAB)["after"]
    layout_tool(op="arrange", tab_id=TAB, apply=True)
    actual = three_groups.flows.tab_footprint(TAB)
    assert (round(actual["w"]), round(actual["h"])) == (predicted["w"], predicted["h"])


def test_arrange_is_undoable(three_groups):
    before = {g["id"]: (g["x"], g["y"]) for g in three_groups.flows.group_nodes}
    layout_tool(op="arrange", tab_id=TAB, apply=True)
    assert session_tool(op="undo")["ok"] is True
    assert {g["id"]: (g["x"], g["y"]) for g in three_groups.flows.group_nodes} == before


def test_arrange_dry_run_records_no_pending_edit(three_groups):
    pending = len(session_tool(op="status")["pending_edits"])
    layout_tool(op="arrange", tab_id=TAB)
    assert len(session_tool(op="status")["pending_edits"]) == pending


def test_audit_reports_every_tab_with_content(three_groups):
    rows = layout_tool(op="audit")["tabs"]
    assert [r["tab"] for r in rows] == ["Demo"]
    row = rows[0]
    assert row["groups"] == 3
    assert row["loose_nodes"] == 1
    assert row["overlaps"] == 0
    assert row["recommended_columns"] >= 1


def test_audit_filters_by_tab_name(three_groups):
    assert layout_tool(op="audit", tab="nosuchtab")["tabs"] == []


# ── decoupling groups ──


@pytest.fixture
def crossed_groups(sess):
    """Two groups with a wire running between them, plus a wire to a loose node."""
    gate = group(op="create", name="Gate", tab_id=TAB, node_ids=[SWITCH])["group_id"]
    edit(op="wire", source_id=FUNC, target_id=SWITCH)
    edit(op="wire", source_id=SWITCH, target_id=HA_NODE)
    return sess, gate


def test_decouple_requires_a_scope(sess):
    with pytest.raises(ValueError, match="requires tab_id or group_id"):
        group(op="decouple")


def test_decouple_dry_run_reports_the_crossing_and_changes_nothing(crossed_groups):
    sess, _ = crossed_groups
    out = group(op="decouple", tab_id=TAB)
    assert out["applied"] is False
    assert out["crossings"] == 1
    assert "DRY RUN" in out["output"]
    assert SWITCH in sess.flows.by_id[FUNC]["wires"][0]
    assert not [n for n in sess.flows.nodes if n.get("type") == "link out"]


def test_decouple_swaps_the_wire_for_a_reciprocal_link_pair(crossed_groups):
    sess, gate = crossed_groups
    pair = group(op="decouple", tab_id=TAB, apply=True)["pairs"][0]
    lout = sess.flows.by_id[pair["link_out_id"]]
    lin = sess.flows.by_id[pair["link_in_id"]]

    assert SWITCH not in sess.flows.by_id[FUNC]["wires"][0]
    assert lout["type"] == "link out" and lout["mode"] == "link" and lout["wires"] == []
    assert lin["type"] == "link in" and lin["wires"] == [[SWITCH]]
    assert lout["links"] == [lin["id"]] and lin["links"] == [lout["id"]]
    assert lout["id"] in sess.flows.by_id[FUNC]["wires"][0]


def test_decouple_puts_each_link_node_in_the_group_it_belongs_to(crossed_groups):
    sess, gate = crossed_groups
    pair = group(op="decouple", tab_id=TAB, apply=True)["pairs"][0]
    lout, lin = sess.flows.by_id[pair["link_out_id"]], sess.flows.by_id[pair["link_in_id"]]
    assert lout["g"] == GROUP and lout["id"] in sess.flows.by_id[GROUP]["nodes"]
    assert lin["g"] == gate and lin["id"] in sess.flows.by_id[gate]["nodes"]


def test_decouple_leaves_a_wire_to_an_ungrouped_node_alone(crossed_groups):
    sess, _ = crossed_groups
    group(op="decouple", tab_id=TAB, apply=True)
    assert sess.flows.by_id[SWITCH]["wires"][0] == [HA_NODE]


def test_decouple_leaves_a_wire_into_a_nested_group_alone(sess):
    inner = group(op="create", name="Inner", tab_id=TAB, node_ids=[ORPHAN])["group_id"]
    group(op="create", name="Outer", tab_id=TAB, node_ids=[inner, HA_NODE])
    edit(op="wire", source_id=HA_NODE, target_id=ORPHAN)
    assert group(op="decouple", tab_id=TAB)["crossings"] == 0


def test_decouple_refits_the_boxes_around_the_new_links(crossed_groups):
    sess, _ = crossed_groups
    group(op="decouple", tab_id=TAB, apply=True)
    escapes = [f for f in layout_tool(op="check")["findings"] if f["check"] == "group-escape"]
    assert escapes == []


def test_decouple_gives_a_group_one_link_column_however_many_wires_cross(crossed_groups):
    sess, _ = crossed_groups
    edit(op="wire", source_id=INJECT, target_id=SWITCH)
    pairs = group(op="decouple", tab_id=TAB, apply=True)["pairs"]
    assert len(pairs) == 2
    outs = [sess.flows.by_id[p["link_out_id"]] for p in pairs]
    ins = [sess.flows.by_id[p["link_in_id"]] for p in pairs]
    assert len({n["x"] for n in outs}) == 1
    assert len({n["y"] for n in outs}) == 2
    assert len({n["x"] for n in ins}) == 1
    assert len({n["y"] for n in ins}) == 2


def test_decouple_leaves_nothing_to_decouple(crossed_groups):
    group(op="decouple", tab_id=TAB, apply=True)
    assert group(op="decouple", tab_id=TAB)["crossings"] == 0


def test_decouple_scoped_to_one_group_ignores_other_tabs_worth_of_crossings(crossed_groups):
    sess, gate = crossed_groups
    assert group(op="decouple", group_id=gate)["crossings"] == 1
    other = group(op="create", name="Elsewhere", tab_id=TAB, node_ids=[ORPHAN])["group_id"]
    assert group(op="decouple", group_id=other)["crossings"] == 0


def test_decouple_is_undoable(crossed_groups):
    sess, _ = crossed_groups
    before = len(sess.flows.nodes)
    group(op="decouple", tab_id=TAB, apply=True)
    assert session_tool(op="undo")["ok"] is True
    assert len(sess.flows.nodes) == before
    assert SWITCH in sess.flows.by_id[FUNC]["wires"][0]


def test_decouple_dry_run_records_no_pending_edit(crossed_groups):
    pending = len(session_tool(op="status")["pending_edits"])
    group(op="decouple", tab_id=TAB)
    assert len(session_tool(op="status")["pending_edits"]) == pending


# ── stdout hygiene: nothing may leak onto the MCP framing channel ──


def test_tools_do_not_print_to_stdout(sess, capsys):
    query(op="summary")
    query(op="groups")
    query(op="configs")
    edit(op="create_node", tab_id=TAB, node_type="debug", name="quiet", x=100, y=900)
    edit(op="create_config_node", node_type="server", name="quiet server")
    edit(op="bypass", node_id=FUNC)
    group(op="create", name="Quiet", tab_id=TAB, node_ids=[SWITCH])
    group(op="decouple", tab_id=TAB)
    group(op="delete", group_id=GROUP)
    tab(op="create", label="Quiet tab")
    layout_tool(op="check")
    layout_tool(op="audit")
    layout_tool(op="arrange", tab_id=TAB)
    session_tool(op="diff")
    session_tool(op="undo")
    assert capsys.readouterr().out == ""
