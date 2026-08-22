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


# ── stdout hygiene: nothing may leak onto the MCP framing channel ──


def test_tools_do_not_print_to_stdout(sess, capsys):
    query(op="summary")
    query(op="groups")
    edit(op="create_node", tab_id=TAB, node_type="debug", name="quiet", x=100, y=900)
    group(op="create", name="Quiet", tab_id=TAB, node_ids=[SWITCH])
    layout_tool(op="check")
    assert capsys.readouterr().out == ""
