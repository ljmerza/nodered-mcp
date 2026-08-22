# nodered-mcp

An MCP server that reads, queries, and edits a Node-RED `flows.json`.

It is a port of the `flows_util.py` / `layout_util.py` pair from
[`hass/ai/nodered-flows`](../ai/nodered-flows) — same query engine, same canvas
geometry, same layout gate — with every host-specific path turned into a
parameter and the whole thing wrapped in seven MCP tools.

## What it does

* **Query** a flows file — tabs, groups, orphans, subflows, referenced Home
  Assistant entities, wire traces.
* **Edit** it — create/update/delete nodes, wire and unwire, create and refit
  groups, import and export node sets.
* **Lay out** the canvas correctly — claim empty space before placing nodes
  instead of guessing coordinates, lint for collisions, repair overlaps.
* **Commit** — write `flows.json`, or write and restart the container.

Edits accumulate **in memory**. Nothing touches disk until you call
`nodered_session(op="save")`, so a multi-node build lands as one unit and the
layout gate runs once against the finished canvas.

## Install

```bash
uv venv
uv pip install -e ".[dev]"
```

## Run

The flows.json path is the only required setting — there is no sensible default,
so the server refuses to start without one.

```bash
nodered-mcp --flows-path /path/to/nodered/data/flows.json
```

### Configuration

Every setting resolves **CLI flag > environment variable > default**.

| Flag | Env | Default | Meaning |
|---|---|---|---|
| `--flows-path` | `NODERED_FLOWS_PATH` | *(required)* | Path to `flows.json` on the host |
| `--container` | `NODERED_CONTAINER` | `nodered` | Container name used by `deploy` |
| `--container-flows-path` | `NODERED_CONTAINER_FLOWS_PATH` | `/data/flows.json` | Path inside the container |
| `--restart-cmd` | `NODERED_RESTART_CMD` | `docker restart <container>` | Restart command; `{container}` is substituted |
| `--transport` | `NODERED_MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse` |
| `--host` / `--port` | `NODERED_MCP_HOST` / `NODERED_MCP_PORT` | `127.0.0.1:8080` | Bind address for `http`/`sse` |

### Register with an MCP client

```json
{
  "mcpServers": {
    "nodered": {
      "type": "stdio",
      "command": "nodered-mcp",
      "args": ["--flows-path", "/home/you/docker/volumes/nodered/data/flows.json"],
      "env": {}
    }
  }
}
```

Running from a checkout without installing:

```json
{
  "mcpServers": {
    "nodered": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/nodered-mcp", "nodered-mcp"],
      "env": { "NODERED_FLOWS_PATH": "/home/you/docker/volumes/nodered/data/flows.json" }
    }
  }
}
```

## Tools

| Tool | Ops |
|---|---|
| `nodered_query` | `summary`, `tabs`, `groups`, `tab`, `group`, `search`, `ungrouped`, `orphans`, `subflows`, `styles`, `entities`, `inspect`, `connections`, `trace` |
| `nodered_find_nodes` | structured search by tab / type / name substring |
| `nodered_get_node` | one node's raw JSON plus its wiring context |
| `nodered_edit` | `create_node`, `update_node`, `delete_node`, `rename_node`, `duplicate_node`, `wire`, `unwire`, `import_nodes`, `export_group` |
| `nodered_group` | `create`, `add`, `move_node`, `rename`, `set_style`, `normalize_styles`, `refit`, `shift`, `bounds` |
| `nodered_layout` | `check`, `free_region`, `occupied`, `fix` |
| `nodered_session` | `status`, `save`, `deploy`, `reload` |

### Typical build

```text
nodered_query(op="tabs")                                   -> tab ids
nodered_layout(op="free_region", tab_id=TAB, w=800, h=200) -> {x: 100, y: 3240}
nodered_edit(op="create_node", tab_id=TAB, node_type="inject", name="tick",
             x=100, y=3240)                                -> node id
nodered_edit(op="create_node", tab_id=TAB, node_type="switch", name="gate",
             x=300, y=3240)                                -> node id
nodered_edit(op="wire", source_id=..., target_id=...)
nodered_group(op="create", name="My Flow", tab_id=TAB, node_ids=[...])
nodered_session(op="save")
```

## The layout gate

`save` and `deploy` lint the canvas before and after your edit and **refuse to
write** if the edit introduces a *new* ERROR-severity finding:

* `group-overlap` — a group box landing on another group box
* `group-escape` — a group box that no longer covers its own nodes

Problems that already existed on disk never block. Warnings (`node-overlap`,
`stray-in-group`) are reported but do not block.

When the gate fires, the fix is almost always one of:

* `nodered_layout(op="free_region")` to claim clear canvas, then place there
* `nodered_group(op="refit", group_id=...)` to resize a group around its nodes
* `nodered_session(op="save", allow_overlap=true)` if the overlap is deliberate

Geometry is ported from the Node-RED editor's own sizing rules, so group maths
is exact. Node maths is exact apart from label text measurement, which is
approximated from Helvetica metrics — that is why node-level findings are only
ever warnings.

## The staleness guard

Node-RED rewrites `flows.json` whenever someone hits Deploy in the browser.
The session records the file's mtime at load and re-checks it before every
write. If the file moved underneath you, the commit is refused rather than
silently reverting that work — `reload` and redo, or pass `force=true`.

## Standalone use

Both modules still work as libraries and CLIs, independent of MCP:

```bash
python -m nodered_mcp.flows summary --flows-path /path/to/flows.json
python -m nodered_mcp.layout --path /path/to/flows.json --fix boxes,move
```

```python
from nodered_mcp.flows import Flows

f = Flows("/path/to/flows.json")
ox, oy = f.free_region(tab_id, w=1600, h=300)
f.create_node(tab_id, "inject", "tick", x=ox, y=oy)
f.save()
```

## Tests

```bash
uv run pytest
```

## License

MIT
