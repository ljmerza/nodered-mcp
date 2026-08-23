# nodered-mcp

An MCP server that reads, queries, and edits a Node-RED `flows.json`.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/ljmerza/nodered-mcp/ci.yml?branch=main)](https://github.com/ljmerza/nodered-mcp/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

## About

Node-RED stores every flow, node, wire, and group box in one large JSON file.
Editing it by hand — or with `jq` and `sed` — is how you end up with dangling
wires, groups whose boxes no longer cover their own nodes, and new nodes stacked
on top of existing ones.

This server exposes that file to an MCP client as a small set of tools that
understand the format. It knows the difference between a flow node and a config
node, it can trace a wire path, and it reproduces the Node-RED editor's own
geometry so a group box it draws is the box the editor would have drawn.

It is a port of the `flows_util.py` / `layout_util.py` pair used to script
Node-RED changes in a home-automation repo, generalised so the file path,
container name, and restart command are all configuration.

## Features

- **Query** — tabs, groups, orphaned nodes, subflows, config nodes and who
  references them, referenced Home Assistant entities, and wire traces through
  a flow.
- **Edit** — create, update, delete, rename, and duplicate nodes; enable and
  disable them; wire and unwire them; splice one into an existing wire or route
  traffic around it; create, populate, restyle, and delete groups; create,
  rename, reorder, and delete tabs; import and export node sets.
- **Place** — claim empty canvas before creating nodes instead of guessing
  coordinates, lint the canvas for collisions, repair overlaps, and repack a
  tab's groups into columns instead of one tall stack.
- **Commit deliberately** — edits accumulate in memory and reach disk only when
  you ask, so a multi-node build lands as one unit.
- **Look before you write** — `diff` shows exactly what the pending edits would
  change, and `undo` walks them back one call at a time.
- **Two guards** the underlying scripts never needed: a layout gate that refuses
  writes which introduce new collisions, and a staleness check that refuses to
  overwrite a `flows.json` someone deployed from the browser.

## Requirements

- Python 3.11+
- A `flows.json` on the local filesystem
- Docker on `PATH` — only for the `deploy` tool, which copies the file into a
  container and restarts it

## Installation

```bash
git clone https://github.com/ljmerza/nodered-mcp
cd nodered-mcp
uv sync
```

## Usage

The `flows.json` path is the only required setting. There is no sensible
default, so the server refuses to start without one.

```bash
uv run nodered-mcp --flows-path /path/to/nodered/data/flows.json
```

### Register with an MCP client

```json
{
  "mcpServers": {
    "nodered": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/nodered-mcp", "nodered-mcp"],
      "env": {
        "NODERED_FLOWS_PATH": "/path/to/nodered/data/flows.json"
      }
    }
  }
}
```

See [`.mcp.json.example`](.mcp.json.example) for a fuller example.

### Configuration

Every setting resolves **CLI flag > environment variable > default**.

| Flag | Environment variable | Default | Purpose |
|---|---|---|---|
| `--flows-path` | `NODERED_FLOWS_PATH` | *(required)* | Path to `flows.json` on the host |
| `--container` | `NODERED_CONTAINER` | `nodered` | Container name used by `deploy` |
| `--container-flows-path` | `NODERED_CONTAINER_FLOWS_PATH` | `/data/flows.json` | Path to `flows.json` inside the container |
| `--restart-cmd` | `NODERED_RESTART_CMD` | `docker restart <container>` | Restart command; `{container}` is substituted |
| `--transport` | `NODERED_MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse` |
| `--host` / `--port` | `NODERED_MCP_HOST` / `NODERED_MCP_PORT` | `127.0.0.1` / `8080` | Bind address for `http` and `sse` |

If Node-RED is managed by something other than plain Docker, point
`--restart-cmd` at it:

```bash
NODERED_RESTART_CMD="docker compose restart {container}"
```

## Tools

Eight tools, each dispatching on an `op` argument.

| Tool | Ops |
|---|---|
| `nodered_query` | `summary`, `tabs`, `groups`, `tab`, `group`, `search`, `ungrouped`, `orphans`, `subflows`, `styles`, `configs`, `entities`, `inspect`, `connections`, `trace` |
| `nodered_find_nodes` | Structured search by tab, type, or name substring |
| `nodered_get_node` | One node's raw JSON plus its wiring context |
| `nodered_edit` | `create_node`, `create_config_node`, `update_node`, `update_many`, `delete_node`, `rename_node`, `set_enabled`, `duplicate_node`, `replace_node`, `wire`, `unwire`, `insert_between`, `bypass`, `import_nodes`, `export_group` |
| `nodered_group` | `create`, `add`, `move_node`, `delete`, `rename`, `set_style`, `normalize_styles`, `refit`, `shift`, `bounds` |
| `nodered_tab` | `create`, `rename`, `delete`, `reorder`, `set_enabled`, `set_info` |
| `nodered_layout` | `check`, `audit`, `free_region`, `occupied`, `arrange`, `fix` |
| `nodered_session` | `status`, `diff`, `undo`, `save`, `deploy`, `reload` |

### A typical build

```text
nodered_query(op="tabs")                                   -> tab ids
nodered_layout(op="free_region", tab_id=TAB, w=800, h=200) -> {"x": 100, "y": 3240}
nodered_edit(op="create_node", tab_id=TAB, node_type="inject",
             name="tick", x=100, y=3240)                   -> node id
nodered_edit(op="create_node", tab_id=TAB, node_type="switch",
             name="gate", x=300, y=3240)                   -> node id
nodered_edit(op="wire", source_id=..., target_id=...)
nodered_group(op="create", name="My Flow", tab_id=TAB, node_ids=[...])
nodered_session(op="save")
```

Nothing above touches `flows.json` until the final `save`.

### Arranging a tab

A tab that has been edited for a year drifts into one tall column, because
every routine that needed space took the next free spot underneath everything
else. `arrange` repacks it.

```text
nodered_layout(op="audit")                            -> worst tabs first
nodered_layout(op="arrange", tab_id=TAB)              -> the plan, nothing moved
nodered_layout(op="arrange", tab_id=TAB, apply=true)  -> groups repacked
nodered_session(op="save")
```

Groups are packed into columns, each one going into whichever column is
currently shortest. The column count is chosen to land the tab's bounding box
nearest `target_ratio` (1.6 by default, roughly a widescreen viewport) — pass
`columns` to force it. `sort` picks the placement order: `packed` (tallest
first, densest, but it reorders the tab), `current` (keeps the existing
reading order), or `name`.

Nodes belonging to no group never move. If the packed block would land on one,
the whole block drops below them instead — so a tab with a scratch node parked
in the middle arranges around it rather than burying it.

`arrange` is a dry run unless you pass `apply=true`, it reports the footprint
it will produce before it produces it, and it goes on the undo stack like any
other edit.

## How it protects the file

### The layout gate

`save` and `deploy` lint the canvas before and after your edit, and refuse to
write if the edit introduces a **new** error-level finding:

| Finding | Severity | Meaning |
|---|---|---|
| `group-overlap` | error | A group box landed on another group box |
| `group-escape` | error | A group box no longer covers its own nodes |
| `stray-in-group` | warning | A node sits inside a group box it isn't a member of |
| `node-overlap` | warning | Two nodes occupy the same space |

Problems that already existed on disk never block — only ones your edit created.
When the gate fires, the fix is usually one of:

- `nodered_layout(op="free_region")` to claim clear canvas, then place there
- `nodered_group(op="refit", group_id=...)` to resize a group around its nodes
- `nodered_session(op="save", allow_overlap=true)` if the overlap is deliberate

Group geometry is exact: the sizing rules are ported from the Node-RED editor,
so a computed box matches what the editor draws. Node geometry is exact apart
from label text width, which is approximated from Helvetica metrics — that is
why node-level findings are only ever warnings.

### The staleness check

Node-RED rewrites `flows.json` whenever someone presses Deploy in the browser.
The session records `(mtime_ns, size)` when it loads the file and re-checks
before every write. If the file changed underneath you, the commit is refused
rather than silently reverting that work. Either `reload` and redo your edits,
or pass `force=true`.

Nanoseconds rather than `os.path.getmtime`: a float epoch only resolves to about
a microsecond, so a write landing in the same tick as the load compares equal
and slips past the check.

## Standalone use

Both engine modules work as libraries and CLIs, independent of MCP.

```bash
uv run python -m nodered_mcp.flows summary --flows-path /path/to/flows.json
uv run python -m nodered_mcp.layout --path /path/to/flows.json --fix boxes,move
```

```python
from nodered_mcp.flows import Flows

f = Flows("/path/to/flows.json")
ox, oy = f.free_region(tab_id, w=1600, h=300)
f.create_node(tab_id, "inject", "tick", x=ox, y=oy)
f.save()
```

> `--fix boxes` **alone makes things worse**: refitting grows some boxes so they
> swallow neighbouring non-member nodes. Run `boxes,move` together, and read the
> dry run before passing `--apply`.

## Project layout

```
src/nodered_mcp/
├── server.py       FastMCP server: the eight tools
├── session.py      in-memory session, stdout capture, staleness guard
├── config.py       CLI flags and environment resolution
├── flows.py        the Flows class, composed from the mixins below
├── constants.py    defaults, the group style, LayoutError
├── reports.py      ReadMixin      — summary, tab, group, search, trace
├── nodes.py        NodeEditMixin  — create/update/delete/wire nodes
├── groups.py       GroupMixin     — create, populate, and delete group boxes
├── tabs.py         TabMixin       — create, rename, reorder, and delete tabs
├── placement.py    LayoutMixin    — claim free canvas, refit boxes, arrange tabs
├── transfer.py     TransferMixin  — import and export node sets
├── persist.py      PersistMixin   — save, deploy, and the layout gate
└── layout.py       canvas geometry and linter, ported from the NR editor
```

`Flows` composes the mixins, so the public API stays flat: `f.summary()`,
`f.create_node()`, `f.free_region()`, `f.save()`.

## Development

```bash
uv sync --group dev
uv run pytest                    # 93 tests
uv run ruff check .
uv run ruff format --check .
```

Tests run against a synthetic fixture in `tests/fixtures/`, never a real flows
file. They cover configuration precedence, the read tools, in-memory-until-save
semantics, the layout gate both blocking and overridden, the staleness guard,
the deploy command sequence, and that no tool writes to stdout — a stray print
would corrupt MCP's stdio framing.

CI runs the same checks through
[`ljmerza/misc-actions`](https://github.com/ljmerza/misc-actions).

## Contributing

Issues and pull requests are welcome. Please keep `ruff check`, `ruff format`,
and `pytest` green.

## Acknowledgments

- [Node-RED](https://nodered.org) — the canvas geometry here is ported from its
  editor client, so group boxes match what the editor draws.
- [FastMCP](https://gofastmcp.com) — the MCP server framework.

## License

MIT. See [`LICENSE`](LICENSE).
