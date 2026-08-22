"""FastMCP server exposing a Node-RED flows.json as MCP tools.

Seven tools, each dispatching on an `op` argument:

    nodered_query      read-only reports (summary, tab, group, search, trace...)
    nodered_find_nodes structured node search
    nodered_get_node   one node's raw JSON plus its context
    nodered_edit       node-level mutations (create/update/delete/wire/import)
    nodered_group      group-level mutations (create/add/move/refit/shift)
    nodered_layout     canvas geometry: lint, claim free space, repair
    nodered_session    status / save / deploy / reload

Edits accumulate in memory. Nothing reaches flows.json until
`nodered_session(op="save")` or `op="deploy"`, so a multi-node build is
committed as one unit and the layout gate runs once against the finished
canvas -- the same lifecycle the hand-written build scripts use.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from . import layout
from .config import build_parser, settings_from_args
from .flows import DEFAULT_GROUP_STYLE, LayoutError
from .session import FlowsSession, StaleFlowsError, capture, run

mcp = FastMCP(name="nodered", instructions=__doc__)

# Populated by main(); tests inject their own.
SESSION: FlowsSession | None = None


def session() -> FlowsSession:
    if SESSION is None:
        raise RuntimeError("nodered-mcp: no session configured (server not started)")
    return SESSION


def _need(value: Any, op: str, name: str) -> Any:
    if value is None or value == "":
        raise ValueError(f"op '{op}' requires the '{name}' argument")
    return value


def _node_summary(session_: FlowsSession, node: dict) -> dict:
    f = session_.flows
    group = f.node_to_group.get(node["id"])
    return {
        "id": node["id"],
        "type": node.get("type"),
        "name": node.get("name", ""),
        "tab": f.tabs.get(node.get("z", ""), node.get("z", "")),
        "tab_id": node.get("z"),
        "group": group.get("name") if group else None,
        "group_id": group["id"] if group else None,
        "x": node.get("x"),
        "y": node.get("y"),
        "entity": node.get("entityid") or node.get("entity_id") or None,
    }


# ──────────────────────────────────────────────────────────────────────
# Reads
# ──────────────────────────────────────────────────────────────────────


@mcp.tool
def nodered_query(
    op: Annotated[
        Literal[
            "summary",
            "groups",
            "tab",
            "group",
            "search",
            "ungrouped",
            "orphans",
            "subflows",
            "styles",
            "entities",
            "inspect",
            "connections",
            "trace",
            "tabs",
        ],
        Field(
            description=(
                "summary: counts per tab. groups: every group by tab. tab/group/search: "
                "need `target` (substring match). ungrouped/orphans/subflows/styles/"
                "entities/tabs: no target. inspect/connections/trace: need `target` = a "
                "node id."
            )
        ),
    ],
    target: Annotated[
        str | None, Field(description="Tab name, group name, search term, or node id, depending on op.")
    ] = None,
    max_depth: Annotated[int, Field(description="trace only: how far to follow wires.")] = 20,
) -> str:
    """Read-only reports over the current flows, as human-readable text.

    Reflects pending in-memory edits, not just what is on disk.
    """
    s = session()
    f = s.flows

    if op == "tabs":
        lines = [f"{tid}  {label}" for tid, label in sorted(f.tabs.items(), key=lambda x: x[1])]
        return "\n".join(lines) or "(no tabs)"

    simple = {
        "summary": f.summary,
        "groups": f.groups,
        "ungrouped": f.ungrouped,
        "orphans": f.orphans,
        "subflows": f.subflows,
        "styles": f.styles,
        "entities": f.entities,
    }
    if op in simple:
        text, _ = run(simple[op])
        return text or "(no output)"

    target = _need(target, op, "target")
    if op == "trace":
        text, _ = run(f.trace, target, max_depth=max_depth)
    else:
        text, _ = run(
            {
                "tab": f.tab,
                "group": f.group,
                "search": f.search,
                "inspect": f.inspect,
                "connections": f.connections,
            }[op],
            target,
        )
    return text or "(no output)"


@mcp.tool
def nodered_find_nodes(
    tab_id: Annotated[str | None, Field(description="Restrict to this tab id.")] = None,
    node_type: Annotated[str | None, Field(description="Exact node type, e.g. 'function'.")] = None,
    name_contains: Annotated[str | None, Field(description="Case-insensitive substring of the name.")] = None,
    limit: Annotated[int, Field(description="Max results (default 100).")] = 100,
) -> dict:
    """Find nodes matching criteria. Returns structured records, not text.

    Use this when you need node ids to feed into nodered_edit / nodered_group.
    """
    s = session()
    _, nodes = run(s.flows.find_nodes, tab_id=tab_id, node_type=node_type, name_contains=name_contains)
    return {
        "count": len(nodes),
        "truncated": len(nodes) > limit,
        "nodes": [_node_summary(s, n) for n in nodes[:limit]],
    }


@mcp.tool
def nodered_get_node(node_id: Annotated[str, Field(description="The node id to fetch.")]) -> dict:
    """One node's complete raw JSON, plus its tab, group, and wiring context."""
    s = session()
    f = s.flows
    node = f.get_node(node_id)
    if node is None:
        raise ValueError(f"Node {node_id} not found")

    inbound = []
    for n in f.nodes:
        for i, wire_set in enumerate(n.get("wires", []) or []):
            if node_id in wire_set:
                inbound.append(
                    {"from": n["id"], "name": n.get("name", ""), "type": n.get("type"), "output": i}
                )
    outbound = []
    for i, wire_set in enumerate(node.get("wires", []) or []):
        for tid in wire_set:
            t = f.get_node(tid)
            outbound.append(
                {
                    "output": i,
                    "to": tid,
                    "name": (t or {}).get("name", ""),
                    "type": (t or {}).get("type"),
                    "missing": t is None,
                }
            )
    return {"context": _node_summary(s, node), "node": node, "wires_in": inbound, "wires_out": outbound}


# ──────────────────────────────────────────────────────────────────────
# Node-level edits
# ──────────────────────────────────────────────────────────────────────


@mcp.tool
def nodered_edit(
    op: Annotated[
        Literal[
            "create_node",
            "update_node",
            "delete_node",
            "rename_node",
            "duplicate_node",
            "wire",
            "unwire",
            "import_nodes",
            "export_group",
        ],
        Field(
            description=(
                "create_node: tab_id + node_type + name (+ config/x/y/group_id). "
                "update_node: node_id + props. delete_node/rename_node/duplicate_node: "
                "node_id. wire/unwire: source_id + target_id (+ output_index). "
                "import_nodes: json_path + tab_id. export_group: name (+ output_path)."
            )
        ),
    ],
    node_id: Annotated[str | None, Field(description="Target node id.")] = None,
    tab_id: Annotated[str | None, Field(description="Tab id to place nodes on.")] = None,
    node_type: Annotated[str | None, Field(description="Node-RED type, e.g. 'switch'.")] = None,
    name: Annotated[str | None, Field(description="Node name, or group name for export_group.")] = None,
    config: Annotated[
        dict | None,
        Field(description="create_node: extra node properties merged in (func, rules, server, ...)."),
    ] = None,
    props: Annotated[
        dict | None, Field(description="update_node: properties to set on the existing node.")
    ] = None,
    x: Annotated[int, Field(description="create_node: canvas x.")] = 200,
    y: Annotated[int, Field(description="create_node: canvas y.")] = 200,
    group_id: Annotated[str | None, Field(description="Group to add the new/imported nodes to.")] = None,
    source_id: Annotated[str | None, Field(description="wire/unwire: source node id.")] = None,
    target_id: Annotated[str | None, Field(description="wire/unwire: target node id.")] = None,
    output_index: Annotated[
        int | None, Field(description="wire: output port (default 0). unwire: omit to cut every port.")
    ] = None,
    x_offset: Annotated[int, Field(description="duplicate_node: x offset for the clone.")] = 50,
    y_offset: Annotated[int, Field(description="duplicate_node: y offset for the clone.")] = 50,
    json_path: Annotated[str | None, Field(description="import_nodes: path to a JSON node list.")] = None,
    output_path: Annotated[
        str | None, Field(description="export_group: file to write (omit to return the nodes inline).")
    ] = None,
    remap_ids: Annotated[bool, Field(description="import_nodes: assign fresh ids (default true).")] = True,
) -> dict:
    """Mutate nodes in memory. Nothing is written until nodered_session(op='save').

    Coordinates: do not hand-pick x/y. Call nodered_layout(op='free_region')
    first and place nodes relative to the origin it returns.
    """
    s = session()
    f = s.flows

    if op == "create_node":
        _need(tab_id, op, "tab_id")
        _need(node_type, op, "node_type")
        text, new_id = run(
            f.create_node, tab_id, node_type, name or "", config=config, x=x, y=y, group_id=group_id
        )
        s.journal.record(f"create_node {node_type} '{name}' -> {new_id}")
        return {"ok": True, "node_id": new_id, "output": text}

    if op == "update_node":
        _need(node_id, op, "node_id")
        _need(props, op, "props")
        text, _ = run(f.update_node, node_id, **props)
        s.journal.record(f"update_node {node_id} {sorted(props)}")
        return {"ok": True, "node_id": node_id, "updated": sorted(props), "output": text}

    if op == "delete_node":
        _need(node_id, op, "node_id")
        label = (f.get_node(node_id) or {}).get("name", node_id)
        text, _ = run(f.delete_node, node_id)
        s.journal.record(f"delete_node {node_id} '{label}'")
        return {"ok": True, "node_id": node_id, "output": text}

    if op == "rename_node":
        _need(node_id, op, "node_id")
        _need(name, op, "name")
        text, _ = run(f.set_node_name, node_id, name)
        s.journal.record(f"rename_node {node_id} -> '{name}'")
        return {"ok": True, "node_id": node_id, "output": text}

    if op == "duplicate_node":
        _need(node_id, op, "node_id")
        text, new_id = run(f.duplicate_node, node_id, x_offset=x_offset, y_offset=y_offset)
        s.journal.record(f"duplicate_node {node_id} -> {new_id}")
        return {"ok": True, "node_id": new_id, "output": text}

    if op == "wire":
        _need(source_id, op, "source_id")
        _need(target_id, op, "target_id")
        idx = 0 if output_index is None else output_index
        text, _ = run(f.wire_nodes, source_id, target_id, output_index=idx)
        s.journal.record(f"wire {source_id}[{idx}] -> {target_id}")
        return {"ok": True, "output": text}

    if op == "unwire":
        _need(source_id, op, "source_id")
        _need(target_id, op, "target_id")
        text, _ = run(f.unwire_nodes, source_id, target_id, output_index=output_index)
        s.journal.record(f"unwire {source_id} -x-> {target_id}")
        return {"ok": True, "output": text}

    if op == "import_nodes":
        _need(json_path, op, "json_path")
        _need(tab_id, op, "tab_id")
        text, id_map = run(f.import_nodes, json_path, tab_id, group_id=group_id, remap_ids=remap_ids)
        s.journal.record(f"import_nodes {json_path} -> tab {tab_id}")
        return {"ok": True, "id_map": id_map, "output": text}

    if op == "export_group":
        _need(name, op, "name")
        text, exported = run(f.export_group, name, output_path)
        return {
            "ok": True,
            "count": len(exported),
            "nodes": None if output_path else exported,
            "output": text if output_path else f"exported {len(exported)} nodes",
        }

    raise ValueError(f"unknown op {op!r}")


# ──────────────────────────────────────────────────────────────────────
# Group-level edits
# ──────────────────────────────────────────────────────────────────────


@mcp.tool
def nodered_group(
    op: Annotated[
        Literal[
            "create",
            "add",
            "move_node",
            "rename",
            "set_style",
            "normalize_styles",
            "refit",
            "shift",
            "bounds",
        ],
        Field(
            description=(
                "create: name + tab_id + node_ids. add: group_id + node_ids. "
                "move_node: node_id + group_id (null group_id ungroups). rename: "
                "group_id + name. set_style/refit/bounds: group_id. shift: group_id "
                "+ dx/dy. normalize_styles: no args."
            )
        ),
    ],
    group_id: Annotated[str | None, Field(description="Target group id.")] = None,
    name: Annotated[str | None, Field(description="Group label.")] = None,
    tab_id: Annotated[str | None, Field(description="Tab to create the group on.")] = None,
    node_ids: Annotated[list[str] | None, Field(description="Member node ids.")] = None,
    node_id: Annotated[str | None, Field(description="move_node: the node to move.")] = None,
    style: Annotated[
        dict | None,
        Field(description="Group style. Omit to use the house dark style -- do not invent fills."),
    ] = None,
    reflow: Annotated[
        bool, Field(description="create: relocate the group if its box lands on another one (default true).")
    ] = True,
    dx: Annotated[int, Field(description="shift: horizontal delta.")] = 0,
    dy: Annotated[int, Field(description="shift: vertical delta.")] = 0,
) -> dict:
    """Create and manipulate groups. In memory until nodered_session(op='save').

    New groups get the standard dark style unless you pass `style` explicitly.
    """
    s = session()
    f = s.flows

    if op == "create":
        _need(name, op, "name")
        _need(tab_id, op, "tab_id")
        _need(node_ids, op, "node_ids")
        text, gid = run(f.create_group, name, tab_id, node_ids, style=style, reflow=reflow)
        s.journal.record(f"create_group '{name}' ({len(node_ids)} nodes) -> {gid}")
        return {"ok": True, "group_id": gid, "output": text}

    if op == "add":
        _need(group_id, op, "group_id")
        _need(node_ids, op, "node_ids")
        text, _ = run(f.add_to_group, group_id, node_ids)
        s.journal.record(f"add_to_group {group_id} += {len(node_ids)} nodes")
        return {"ok": True, "output": text}

    if op == "move_node":
        _need(node_id, op, "node_id")
        text, _ = run(f.move_to_group, node_id, group_id)
        s.journal.record(f"move_to_group {node_id} -> {group_id or '(ungrouped)'}")
        return {"ok": True, "output": text}

    if op == "rename":
        _need(group_id, op, "group_id")
        _need(name, op, "name")
        text, _ = run(f.set_group_name, group_id, name)
        s.journal.record(f"rename_group {group_id} -> '{name}'")
        return {"ok": True, "output": text}

    if op == "set_style":
        _need(group_id, op, "group_id")
        text, _ = run(f.set_group_style, group_id, style)
        s.journal.record(f"set_group_style {group_id}")
        return {"ok": True, "style": style or DEFAULT_GROUP_STYLE, "output": text}

    if op == "normalize_styles":
        text, _ = run(f.normalize_group_styles, style)
        s.journal.record("normalize_group_styles")
        return {"ok": True, "output": text}

    if op == "refit":
        _need(group_id, op, "group_id")
        text, changed = run(f.refit_group, group_id)
        if changed:
            s.journal.record(f"refit_group {group_id}")
        return {"ok": True, "changed": bool(changed), "output": text}

    if op == "shift":
        _need(group_id, op, "group_id")
        text, _ = run(f.shift_group, group_id, dx, dy)
        s.journal.record(f"shift_group {group_id} by ({dx:+d},{dy:+d})")
        return {"ok": True, "output": text}

    if op == "bounds":
        _need(group_id, op, "group_id")
        _, box = run(f.group_bounds, group_id)
        if box is None:
            return {"ok": True, "bounds": None, "note": "group has no members with canvas size"}
        return {
            "ok": True,
            "bounds": {
                "x1": box[0],
                "y1": box[1],
                "x2": box[2],
                "y2": box[3],
                "w": box[2] - box[0],
                "h": box[3] - box[1],
            },
        }

    raise ValueError(f"unknown op {op!r}")


# ──────────────────────────────────────────────────────────────────────
# Layout
# ──────────────────────────────────────────────────────────────────────


@mcp.tool
def nodered_layout(
    op: Annotated[
        Literal["check", "free_region", "occupied", "fix"],
        Field(
            description=(
                "check: lint the canvas. free_region: claim empty space, needs "
                "tab_id + w + h. occupied: rects already in use on tab_id. fix: "
                "repair overlaps (dry run unless apply=true)."
            )
        ),
    ],
    tab_id: Annotated[str | None, Field(description="Tab id (free_region, occupied).")] = None,
    tab: Annotated[str | None, Field(description="Tab label to limit check/fix to.")] = None,
    w: Annotated[int, Field(description="free_region: block width to reserve.")] = 0,
    h: Annotated[int, Field(description="free_region: block height to reserve.")] = 0,
    margin: Annotated[int, Field(description="free_region: clearance from existing content.")] = 40,
    strategy: Annotated[
        Literal["below", "gap"],
        Field(description="free_region: 'below' drops under everything (safe); 'gap' packs into a hole."),
    ] = "below",
    grid: Annotated[int, Field(description="free_region: snap grid (Node-RED uses 20).")] = 20,
    fix_ops: Annotated[
        str,
        Field(
            description="fix: 'boxes', 'move', or 'boxes,move'. Run 'boxes' alone at your peril "
            "-- refitting alone grows boxes so they swallow neighbouring nodes."
        ),
    ] = "boxes,move",
    apply: Annotated[bool, Field(description="fix: actually write (default false = dry run).")] = False,
) -> dict:
    """Canvas geometry: lint it, claim empty space, or repair collisions.

    Always call op='free_region' before creating nodes, and lay them out
    relative to the returned origin instead of guessing coordinates.
    """
    s = session()
    f = s.flows

    if op == "check":
        text, findings = run(f.layout_check, tab=tab)
        errors = [x for x in findings if x.severity == "ERROR"]
        return {
            "ok": not errors,
            "errors": len(errors),
            "warnings": len(findings) - len(errors),
            "findings": [x.as_dict() for x in findings],
            "output": text,
        }

    if op == "free_region":
        _need(tab_id, op, "tab_id")
        if w <= 0 or h <= 0:
            raise ValueError("op 'free_region' requires positive w and h")
        _, (ox, oy) = run(f.free_region, tab_id, w, h, margin=margin, strategy=strategy, grid=grid)
        return {
            "ok": True,
            "x": ox,
            "y": oy,
            "note": "place nodes relative to this origin, e.g. x=x+200 for the next node",
        }

    if op == "occupied":
        _need(tab_id, op, "tab_id")
        _, rects = run(f.occupied_rects, tab_id)
        return {
            "ok": True,
            "count": len(rects),
            "rects": [{"x1": r[0], "y1": r[1], "x2": r[2], "y2": r[3]} for r in rects],
        }

    if op == "fix":
        if s.journal.dirty:
            raise ValueError(
                f"op 'fix' reads flows.json from disk, so it cannot run with "
                f"{len(s.journal.entries)} uncommitted edit(s) pending. "
                f"Save them first, or discard with nodered_session(op='reload')."
            )
        ops = tuple(o.strip() for o in fix_ops.split(",") if o.strip())
        bad = [o for o in ops if o not in ("boxes", "move")]
        if bad:
            raise ValueError(f"unknown fix op(s): {','.join(bad)}")
        text, (before, after) = run(layout.fix, s.settings.flows_path, ops=ops, apply=apply, tab_filter=tab)
        if apply:
            s.reload()
        return {
            "ok": True,
            "applied": apply,
            "findings_before": len(before),
            "findings_after": len(after),
            "output": text,
        }

    raise ValueError(f"unknown op {op!r}")


# ──────────────────────────────────────────────────────────────────────
# Session / commit
# ──────────────────────────────────────────────────────────────────────


@mcp.tool
def nodered_session(
    op: Annotated[
        Literal["status", "save", "deploy", "reload"],
        Field(
            description=(
                "status: config + pending edits + whether the file changed on disk. "
                "save: write flows.json. deploy: write, docker cp into the container, "
                "and restart it. reload: discard pending edits and re-read from disk."
            )
        ),
    ] = "status",
    allow_overlap: Annotated[
        bool,
        Field(
            description="save/deploy: write even though the edit introduces a NEW layout "
            "error. "
            "Only for deliberate overlaps -- fix the placement first."
        ),
    ] = False,
    force: Annotated[
        bool,
        Field(
            description="save/deploy: overwrite flows.json even though it changed on disk "
            "since this session loaded it. Discards whoever wrote it."
        ),
    ] = False,
) -> dict:
    """Inspect and commit the editing session.

    save() runs a layout gate: it lints the canvas before and after the edit
    and refuses to write if the edit introduces a NEW error-level problem
    (groups overlapping, a group box that no longer covers its own nodes).
    Pre-existing problems never block. deploy() runs the same gate, then
    restarts Node-RED.
    """
    s = session()

    if op == "status":
        return s.status()

    if op == "reload":
        return {"ok": True, "output": s.reload()}

    try:
        text = s.commit(deploy=(op == "deploy"), allow_overlap=allow_overlap, force=force)
    except LayoutError as exc:
        return {
            "ok": False,
            "error": "layout_gate",
            "message": str(exc),
            "hint": "Use nodered_layout(op='free_region') to claim clear canvas, "
            "nodered_group(op='refit') to resize a group around its nodes, "
            "or pass allow_overlap=true if the overlap is deliberate.",
        }
    except StaleFlowsError as exc:
        return {"ok": False, "error": "stale_file", "message": str(exc)}

    return {"ok": True, "op": op, "output": text}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings_from_args(args)

    global SESSION
    SESSION = FlowsSession(settings)

    # Fail fast on an unreadable/invalid flows.json rather than at first tool call.
    # reload() RETURNS its summary; capture() is only here to swallow anything the
    # engine might print, which must never reach stdout on stdio transport.
    with capture():
        try:
            loaded = SESSION.reload()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"nodered-mcp: cannot read {settings.flows_path}: {exc}", file=sys.stderr)
            return 2
    print(f"nodered-mcp: {loaded}", file=sys.stderr)

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
