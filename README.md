# nodered-mcp

<p align="center">
<img src="https://img.shields.io/github/stars/ljmerza/nodered-mcp?style=for-the-badge&label=Stars&color=orange" alt="Stars">
<a href="https://github.com/ljmerza/nodered-mcp/releases/latest"><img src="https://img.shields.io/github/v/release/ljmerza/nodered-mcp?style=for-the-badge&color=purple" alt="Version"></a>
<a href="https://pypi.org/project/nodered-mcp/"><img src="https://img.shields.io/pypi/v/nodered-mcp?style=for-the-badge&label=PyPI&color=blue" alt="PyPI"></a>
<a href="https://github.com/ljmerza/nodered-mcp/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/ljmerza/nodered-mcp/ci.yml?style=for-the-badge&label=CI" alt="CI"></a>
<a href="https://github.com/ljmerza/nodered-mcp/blob/main/LICENSE"><img src="https://img.shields.io/github/license/ljmerza/nodered-mcp?style=for-the-badge&label=License&color=green" alt="License"></a>
</p>

<p align="center">
<a href="https://www.buymeacoffee.com/JMISm06AD"><img src="https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee"></a>
</p>

Explore, edit, and deploy Node-RED flows from an MCP client.

## About

An MCP server for Node-RED. It gives an MCP client tools to explore and edit a
flow set -- tabs, nodes, wires, groups, subflows and config nodes -- and to
deploy the result to the running instance.

It reaches Node-RED over its Admin API, or works directly against a
`flows.json` on disk. Either way the tools understand the format rather than
treating it as generic JSON: they tell a flow node from a config node, follow a
wire path across tabs, and reproduce the editor's own geometry, so a group box
this server draws is the box Node-RED would have drawn.

## Features

- Read tabs, groups, orphaned nodes, subflows, config nodes and who references
  them, referenced Home Assistant entities, and wire traces through a flow.
- Create, update, delete, rename, and duplicate nodes. Enable or disable them,
  wire and unwire them, splice one into an existing wire, or route traffic
  around it.
- Create, populate, restyle, and delete groups; create, rename, reorder, and
  delete tabs; import and export node sets.
- Claim empty canvas before creating nodes instead of guessing coordinates,
  lint the canvas for collisions, repair overlaps, and repack a tab's groups
  into columns instead of one tall stack.
- Edits accumulate in memory and reach Node-RED only when you commit, so a
  multi-node build lands as one unit. `diff` shows what they would change
  before you commit, and `undo` walks them back one call at a time.
- Two guards on every write: a layout gate that refuses writes introducing new
  collisions, and a staleness check that refuses to overwrite flows someone
  deployed from the browser in the meantime.

## Requirements

- Python 3.11+ and nothing outside the standard library beyond `fastmcp`
- One of: a reachable Node-RED, or a `flows.json` on this filesystem

That is the whole list. Docker is needed only by the fallback deploy route,
which you can ignore entirely if Node-RED's Admin API is reachable.

## Installation

No install step -- `uvx` fetches and runs it:

```bash
uvx nodered-mcp --nodered-url http://localhost:1880
```

Or `pip install nodered-mcp`. To work on the package itself:

```bash
git clone https://github.com/ljmerza/nodered-mcp
cd nodered-mcp
uv sync
```

## Usage

The server needs to know where the flows are. Point it at a running Node-RED
and it reads them over the Admin API -- no file access, and Node-RED can be on
another machine:

```bash
nodered-mcp --nodered-url http://localhost:1880
```

Or point it at the file:

```bash
nodered-mcp --flows-path /path/to/nodered/data/flows.json
```

With neither, it looks in `~/.node-red/` and uses the flow file it finds there.
That covers a stock `npm install -g node-red`, where the file is named after
the hostname (`flows_raspberrypi.json`) rather than `flows.json` -- only the
Docker image uses the plain name.

### Register with an MCP client

```json
{
  "mcpServers": {
    "nodered": {
      "type": "stdio",
      "command": "uvx",
      "args": ["nodered-mcp"],
      "env": {
        "NODERED_URL": "http://localhost:1880"
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
| `--nodered-url` | `NODERED_URL` | *(unset)* | Base URL of the running Node-RED. Reads the flows and deploys over its Admin API |
| `--flows-path` | `NODERED_FLOWS_PATH` | *(discovered)* | Path to `flows.json`. Optional when `--nodered-url` is set |
| `--nodered-token` | `NODERED_TOKEN` | *(unset)* | Admin API token, if `adminAuth` is enabled |
| `--read-only` | `NODERED_READ_ONLY` | `false` | Expose only the read tools. Nothing can edit, save, or deploy |
| `--group-style` | `NODERED_GROUP_STYLE` | *(editor theme)* | JSON merged over Node-RED's default style for groups this server creates |
| `--transport` | `NODERED_MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse` |
| `--host` / `--port` | `NODERED_MCP_HOST` / `NODERED_MCP_PORT` | `127.0.0.1` / `8080` | Bind address for `http` and `sse` |

Only used by the fallback deploy route, below:

| Flag | Environment variable | Default | Purpose |
|---|---|---|---|
| `--container` | `NODERED_CONTAINER` | `nodered` | Container name used by `deploy` |
| `--container-flows-path` | `NODERED_CONTAINER_FLOWS_PATH` | `/data/flows.json` | Path to `flows.json` inside the container |
| `--restart-cmd` | `NODERED_RESTART_CMD` | `docker restart <container>` | Restart command; `{container}` is substituted |

### Where the flows come from

Set `--nodered-url` and the flows are read with `GET /flows` and written back
with `POST /flows`. This host needs no filesystem access to Node-RED at all,
so it works against a container, a systemd service, a Home Assistant add-on,
or a box across the network, all the same way.

Node-RED stamps each flow revision, and that revision is what guards your
edits: this server sends back the `rev` it loaded, and if anyone deployed in
the meantime Node-RED answers `409` and the edit is refused rather than
silently reverting their work. `session(op='deploy', force=true)` overrides it.

Set `--flows-path` instead and the flows are read from the file. The
equivalent guard compares the file against what was loaded, so a Deploy from
the browser is caught the same way.

### Group styling

Groups this server creates carry no colours, which is what Node-RED itself
does -- the editor computes stroke and fill from the active theme's CSS, so a
group looks right whether the person opening it uses the light or dark theme.
To force a specific look, merge in the keys you care about:

```bash
NODERED_GROUP_STYLE='{"fill": "#2e333a", "fill-opacity": "0.75"}'
```

### Read-only mode

`--read-only` unregisters `nodered_edit`, `nodered_group` and `nodered_tab`
entirely, so a client never sees them, and refuses the mutating ops of the
other two tools. Useful for pointing a model at a production Node-RED to ask
questions about it.

### How `deploy` reaches Node-RED

`save` only writes `flows.json`. Node-RED reads that file at startup and not
again, so a save on its own changes nothing in the running instance -- and
with no local file there is nothing for it to do, so it refuses. `deploy` is
what makes the flows live, and it has two routes.

**Admin API (set `--nodered-url`).** The flows are POSTed to Node-RED's own
[Admin API](https://nodered.org/docs/api/admin/), the same endpoint the editor
uses when you press Deploy. Node-RED swaps them in place, so there is no
restart and no downtime, and this host needs no container runtime. If a local
`flows.json` is also configured it is written first, so the file stays in step.

Node credentials are untouched: they live in `flows_cred.json`, keyed by node
id, and a deploy that carries no `credentials` key leaves them exactly as they
were (verified against Node-RED 5.0.4).

If `settings.js` sets `httpAdminRoot`, include it (`http://localhost:1880/admin`).
If it sets `adminAuth`, add a token. Node-RED mints one from your editor
credentials, and it expires -- 7 days by default:

```bash
curl -s http://localhost:1880/auth/token \
  --data 'client_id=node-red-admin&grant_type=password&scope=*&username=admin&password=secret'
# {"access_token":"...","expires_in":604800,"token_type":"Bearer"}

NODERED_TOKEN="..."
```

**Docker (no `--nodered-url`).** The fallback: `docker cp` the flows into the
container, then run `--restart-cmd`. This restarts the whole container and
needs the Docker CLI plus socket access. Point `--restart-cmd` at whatever
actually manages the container:

```bash
NODERED_RESTART_CMD="docker compose restart {container}"
```

`session(op='status')` reports which route is configured.

## Tools

Eight tools, each dispatching on an `op` argument.

| Tool | Ops |
|---|---|
| `nodered_query` | `summary`, `tabs`, `groups`, `tab`, `group`, `search`, `ungrouped`, `orphans`, `subflows`, `styles`, `configs`, `entities`, `inspect`, `connections`, `trace` |
| `nodered_find_nodes` | Structured search by tab, type, or name substring |
| `nodered_get_node` | One node's raw JSON plus its wiring context |
| `nodered_edit` | `create_node`, `create_config_node`, `update_node`, `update_many`, `delete_node`, `rename_node`, `set_enabled`, `duplicate_node`, `replace_node`, `wire`, `unwire`, `insert_between`, `bypass`, `import_nodes`, `export_group` |
| `nodered_group` | `create`, `add`, `move_node`, `delete`, `rename`, `set_style`, `normalize_styles`, `refit`, `shift`, `bounds`, `decouple` |
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
nearest `target_ratio` (1.6 by default, roughly a widescreen viewport). Pass
`columns` to force it. `sort` picks the placement order: `packed` (tallest
first, densest, but it reorders the tab), `current` (keeps the existing
reading order), or `name`.

Nodes belonging to no group never move. If the packed block would land on one,
the whole block drops below them instead, so a tab with a scratch node parked
in the middle arranges around it rather than burying it.

`arrange` is a dry run unless you pass `apply=true`, it reports the footprint
it will produce before it produces it, and it goes on the undo stack like any
other edit.

### Decoupling groups

A wire that runs from a node in one group to a node in another pins the two
groups to each other: move one and the wire stretches across the tab, so the
groups can no longer be arranged independently. `decouple` swaps every such
wire for a `link out` / `link in` pair -- the wire now stops at the edge of its
own group and is picked up inside the other one.

```text
nodered_group(op="decouple", tab_id=TAB)              -> the crossings, nothing changed
nodered_group(op="decouple", tab_id=TAB, apply=true)  -> one link pair per crossing
nodered_layout(op="arrange", tab_id=TAB, apply=true)  -> now safe to repack
nodered_session(op="save")
```

One pair per wire, named after the node at the other end (`-> compute`,
`tick ->`). The link out goes in a column just right of the source group's
nodes, the link in just left of the target group's, and a group grows by one
column however many wires cross it. Both boxes are refitted afterwards.

Two kinds of wire are left alone. One touching an ungrouped node, because
there is no second group to decouple from; and one between a group and its own
parent or child, because those move together anyway.

Scope it with `tab_id` for a whole tab or `group_id` for the crossings that
touch one group. Like `arrange` it is a dry run unless you pass `apply=true`,
and it goes on the undo stack. Widening the boxes can push a group into a
neighbour, which the layout gate would block on `save` -- the op reports any
overlap it introduces, and `arrange` repacks them.

## How it protects your flows

### The layout gate

`save` and `deploy` lint the canvas before and after your edit, and refuse to
write if the edit introduces a **new** error-level finding:

| Finding | Severity | Meaning |
|---|---|---|
| `group-overlap` | error | A group box landed on another group box |
| `group-escape` | error | A group box no longer covers its own nodes |
| `stray-in-group` | warning | A node sits inside a group box it isn't a member of |
| `node-overlap` | warning | Two nodes occupy the same space |

Problems that already existed never block. Only the ones your edit
created do. When the gate fires, the fix is usually one of:

- `nodered_layout(op="free_region")` to claim clear canvas, then place there
- `nodered_group(op="refit", group_id=...)` to resize a group around its nodes
- `nodered_session(op="save", allow_overlap=true)` if the overlap is deliberate

Group geometry is exact: the sizing rules are ported from the Node-RED editor,
so a computed box matches what the editor draws. Node geometry is exact apart
from label text width, which is approximated from Helvetica metrics. That is
why node-level findings are only ever warnings.

### The staleness check

Someone pressing Deploy in the browser while you have edits pending would be
silently reverted by your commit. Both sources guard against that, each using
whatever the source itself can prove.

**Admin API.** Node-RED stamps every flow revision. The session sends back the
`rev` it loaded, and Node-RED answers `409` if that is no longer current. Its
own conflict check, so nothing can slip between the check and the write.

**File.** The session records `(mtime_ns, size)` at load and re-checks before
every write. Nanoseconds rather than `os.path.getmtime`: a float epoch only
resolves to about a microsecond, so a write landing in the same tick as the
load would compare equal and slip past. A stamp mismatch is then confirmed
against the file's actual content, because a rewrite that reproduces what you
already have is not somebody else's work -- Node-RED persists the flows a
deploy just handed it, to the same file, with a fresh mtime.

Either way the commit is refused rather than reverting the other work. Either
`reload` and redo your edits, or pass `force=true`.

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
the deploy command sequence, and that no tool writes to stdout: a stray print
would corrupt MCP's stdio framing.

CI runs the same checks through
[`ljmerza/misc-actions`](https://github.com/ljmerza/misc-actions).

## Contributing

Issues and pull requests are welcome. Please keep `ruff check`, `ruff format`,
and `pytest` green.

## Acknowledgments

- [Node-RED](https://nodered.org): the canvas geometry here is ported from its
  editor client, so group boxes match what the editor draws.
- [FastMCP](https://gofastmcp.com): the MCP server framework.

## License

MIT. See [`LICENSE`](LICENSE).
