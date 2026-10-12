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

An MCP server for Node-RED. Read, edit, and deploy your flows from an MCP client.

## About

This server lets an MCP client work with a Node-RED instance: tabs, nodes,
wires, groups, subflows, and config nodes. It can read them, edit them, and
deploy the result to the running instance.

It connects to Node-RED through the Admin API, or reads and writes a
`flows.json` file directly. Either way, the tools understand the Node-RED
format. They can tell a flow node from a config node, follow a wire across
tabs, and size a group box the same way the editor does.

## Features

- **Reading.** List tabs, groups, subflows, orphaned nodes, and config nodes
  along with what references them. Search by name, type, or tab. Trace a wire
  from node to node.
- **Editing nodes.** Create, update, rename, duplicate, and delete nodes.
  Enable and disable them. Wire and unwire them, insert one into an existing
  wire, or bypass one so traffic routes around it.
- **Editing groups and tabs.** Create, populate, restyle, and delete groups.
  Create, rename, reorder, and delete tabs. Import and export sets of nodes.
- **Layout.** Ask for a free region before placing nodes, so you do not have
  to guess coordinates. Check the canvas for overlapping nodes and groups, fix
  them, and repack a tab's groups into columns.
- **Batching.** Edits stay in memory until you save or deploy, so a build with
  many nodes lands as a single change. Use `diff` to see what will change, and
  `undo` to step back one call at a time.
- **Safety checks.** A layout gate blocks any write that creates a new
  overlap. A staleness check blocks any write that would overwrite a deploy
  someone made from the browser while you were working.

## Requirements

- Python 3.11 or newer. The only dependency is `fastmcp`.
- Either a reachable Node-RED, or a `flows.json` on this filesystem.

Docker is only needed for the fallback deploy route. You can ignore it if
Node-RED's Admin API is reachable.

## Installation

You do not need to install anything. `uvx` will fetch and run it:

```bash
uvx nodered-mcp --nodered-url http://localhost:1880
```

You can also `pip install nodered-mcp`, run the [Docker image](#docker), or
install the [Home Assistant add-on](#home-assistant-add-on). To work on the
package itself:

```bash
git clone https://github.com/ljmerza/nodered-mcp
cd nodered-mcp
uv sync
```

## Usage

The server needs to know where your flows are. The simplest option is to point
it at a running Node-RED, which reads them over the Admin API. Node-RED can be
on another machine:

```bash
nodered-mcp --nodered-url http://localhost:1880
```

You can point it at the file instead:

```bash
nodered-mcp --flows-path /path/to/nodered/data/flows.json
```

If you give it neither, it looks in `~/.node-red/` and uses the flow file it
finds. This helps with a normal `npm install -g node-red`, which names the file
after your hostname (`flows_raspberrypi.json`). Only the Docker image calls it
`flows.json`.

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

Each setting can come from a CLI flag, an environment variable, or a default,
in that order of priority.

| Flag | Environment variable | Default | Purpose |
|---|---|---|---|
| `--nodered-url` | `NODERED_URL` | *(unset)* | Base URL of the running Node-RED. Reads flows and deploys over its Admin API |
| `--flows-path` | `NODERED_FLOWS_PATH` | *(discovered)* | Path to `flows.json`. Optional when `--nodered-url` is set |
| `--nodered-token` | `NODERED_TOKEN` | *(unset)* | Admin API token, if `adminAuth` is enabled |
| `--nodered-username` / `--nodered-password` | `NODERED_USERNAME` / `NODERED_PASSWORD` | *(unset)* | HTTP Basic login, for a Node-RED behind a proxy that asks for one |
| `--nodered-insecure` | `NODERED_INSECURE` | `false` | Skip TLS certificate verification for `--nodered-url` |
| `--read-only` | `NODERED_READ_ONLY` | `false` | Only expose the read tools |
| `--group-style` | `NODERED_GROUP_STYLE` | *(editor theme)* | JSON merged over Node-RED's default style for new groups |
| `--transport` | `NODERED_MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse` |
| `--host` / `--port` | `NODERED_MCP_HOST` / `NODERED_MCP_PORT` | `127.0.0.1` / `8080` | Bind address for `http` and `sse` |
| `--auth-token` | `NODERED_MCP_AUTH_TOKEN` | *(unset)* | Bearer token MCP clients must send on `http` and `sse` |

These only apply to the fallback deploy route described below:

| Flag | Environment variable | Default | Purpose |
|---|---|---|---|
| `--container` | `NODERED_CONTAINER` | `nodered` | Container name used by `deploy` |
| `--container-flows-path` | `NODERED_CONTAINER_FLOWS_PATH` | `/data/flows.json` | Path to `flows.json` inside the container |
| `--restart-cmd` | `NODERED_RESTART_CMD` | `docker restart <container>` | Restart command. `{container}` is substituted |

### Where the flows come from

If you set `--nodered-url`, the server reads your flows with `GET /flows` and
writes them back with `POST /flows`. It needs no filesystem access, so it works
the same way against a container, a systemd service, a Home Assistant add-on,
or a machine across the network.

Node-RED assigns a revision to each version of the flows. The server sends back
the revision it loaded, so if someone else deploys while you are working,
Node-RED returns a `409` and your edit is refused instead of overwriting their
work. You can override this with `session(op='deploy', force=true)`.

If you set `--flows-path` instead, the server reads the file. The equivalent
check compares the file against what was loaded, which catches a deploy from
the browser the same way.

### Serving over HTTP

With `--transport http`, the MCP endpoint is at `http://<host>:<port>/mcp`.
Anything that can reach it can edit and deploy your flows, so set
`--auth-token` and clients will need to send `Authorization: Bearer <token>`:

```bash
claude mcp add --transport http nodered http://nodered-mcp.lan:8080/mcp \
  --header "Authorization: Bearer <token>"
```

### Group styling

Groups created by this server carry no colors, which is what Node-RED itself
does. The editor calculates the stroke and fill from the current theme, so a
group looks correct whether the person viewing it uses the light or dark theme.

If you want a specific look, set the keys you care about:

```bash
NODERED_GROUP_STYLE='{"fill": "#2e333a", "fill-opacity": "0.75"}'
```

### Read-only mode

`--read-only` removes `nodered_edit`, `nodered_group`, and `nodered_tab` from
the tool list entirely, so a client never sees them. It also refuses the
editing operations of the other two tools. This is useful if you want to point
a model at a production Node-RED to ask questions about it.

### How `deploy` reaches Node-RED

`save` only writes `flows.json`. Node-RED reads that file when it starts and
not again, so saving on its own does not change anything in the running
instance. If there is no local file, `save` has nothing to do and will tell you
so. `deploy` is what makes your flows live, and it has two routes.

**Admin API**, used when you set `--nodered-url`. The flows are sent to
Node-RED's [Admin API](https://nodered.org/docs/api/admin/), the same endpoint
the editor uses when you press Deploy. Node-RED swaps them in place, so there
is no restart and no downtime, and this machine needs no container runtime. If
you also configured a local `flows.json`, it is written first so the file stays
in sync.

Your node credentials are not affected. They live in `flows_cred.json`, keyed
by node id, and a deploy that carries no credentials leaves them alone. This
was tested against Node-RED 5.0.4.

If your `settings.js` sets `httpAdminRoot`, include it in the URL
(`http://localhost:1880/admin`). If it enables `adminAuth`, you will need a
token. Node-RED creates one from your editor credentials, and it expires after
7 days by default:

```bash
curl -s http://localhost:1880/auth/token \
  --data 'client_id=node-red-admin&grant_type=password&scope=*&username=admin&password=secret'
# {"access_token":"...","expires_in":604800,"token_type":"Bearer"}

NODERED_TOKEN="..."
```

**Docker**, used when you do not set `--nodered-url`. The server copies the
flows into the container with `docker cp` and then runs `--restart-cmd`. This
restarts the whole container and needs the Docker CLI plus socket access. Point
`--restart-cmd` at whatever manages the container:

```bash
NODERED_RESTART_CMD="docker compose restart {container}"
```

Run `session(op='status')` to see which route is configured.

## Docker

The image serves MCP over HTTP on port 8080 and reads the flows over the Admin
API. It is published for amd64 and arm64:

```bash
docker run -d --name nodered-mcp -p 8080:8080 \
  -e NODERED_URL=http://nodered:1880 \
  -e NODERED_MCP_AUTH_TOKEN=change-me \
  ghcr.io/ljmerza/nodered-mcp
```

Every setting in the [configuration table](#configuration) works as an
environment variable. The image has no Docker CLI, so it cannot use the
fallback deploy route; give it `NODERED_URL`. If you mount a `flows.json` and
set `NODERED_FLOWS_PATH` too, the file is kept in sync on each deploy.

## Home Assistant add-on

This repository is also a Home Assistant add-on repository. It connects to the
community Node-RED add-on and exposes the MCP endpoint on port 8765.

1. Go to **Settings → Add-ons → Add-on Store**, open the ⋮ menu, choose
   **Repositories**, and add `https://github.com/ljmerza/nodered-mcp`.
2. Install **Node-RED MCP**.
3. On its **Configuration** tab, set `username` and `password` to a Home
   Assistant user, and `auth_token` to a long random string.

The Node-RED add-on turns off Node-RED's own login and instead puts a proxy on
port 1880 that checks a Home Assistant login, which is why the add-on uses
`--nodered-username` rather than a token. See
[`home-assistant/DOCS.md`](home-assistant/DOCS.md) for the rest.

## Tools

There are eight tools. Each one takes an `op` argument that selects what it
does.

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

Everything above stays in memory until that final `save`.

### Arranging a tab

Tabs tend to grow into one tall column over time, because each new flow gets
added below everything else. `arrange` repacks them.

```text
nodered_layout(op="audit")                            -> worst tabs first
nodered_layout(op="arrange", tab_id=TAB)              -> the plan, nothing moved
nodered_layout(op="arrange", tab_id=TAB, apply=true)  -> groups repacked
nodered_session(op="save")
```

Groups get packed into columns, each one going into whichever column is
currently shortest. The server picks a column count that brings the tab's
overall shape closest to `target_ratio`, which defaults to 1.6 (roughly a
widescreen viewport). You can pass `columns` to set it yourself. The `sort`
argument controls placement order: `packed` puts the tallest first and packs
most densely but reorders the tab, `current` keeps the existing reading order,
and `name` sorts alphabetically.

Nodes that do not belong to a group are never moved. If the packed block would
land on one of them, the whole block drops below instead. A tab with a scratch
node parked in the middle will arrange around it.

`arrange` is a dry run unless you pass `apply=true`. It reports the footprint
it will produce beforehand, and it can be undone like any other edit.

### Decoupling groups

When a wire runs from a node in one group to a node in another, the two groups
are tied together. Moving one stretches the wire across the tab, so you cannot
arrange them independently. `decouple` replaces each of those wires with a
`link out` and `link in` pair, so the wire ends at the edge of its own group
and resumes inside the other one.

```text
nodered_group(op="decouple", tab_id=TAB)              -> the crossings, nothing changed
nodered_group(op="decouple", tab_id=TAB, apply=true)  -> one link pair per crossing
nodered_layout(op="arrange", tab_id=TAB, apply=true)  -> now safe to repack
nodered_session(op="save")
```

You get one pair per wire, named after the node at the other end (`-> compute`,
`tick ->`). The link out goes in a column just right of the source group's
nodes, and the link in just left of the target group's, so each group grows by
one column no matter how many wires cross it. Both boxes are resized
afterwards.

Two kinds of wire are skipped. Wires touching an ungrouped node are left alone,
because there is no second group to decouple from. So are wires between a group
and its own parent or child, because those already move together.

Use `tab_id` to scope it to a whole tab, or `group_id` for just the crossings
that touch one group. Like `arrange`, it is a dry run unless you pass
`apply=true`, and it can be undone. Widening the boxes can push a group into a
neighbor, which the layout gate would block when you save, so the operation
reports any overlap it creates and `arrange` can repack them.

## How it protects your flows

### The layout gate

`save` and `deploy` check the canvas before and after your edit. If the edit
introduces a new error-level problem, the write is refused.

| Finding | Severity | Meaning |
|---|---|---|
| `group-overlap` | error | A group box landed on another group box |
| `group-escape` | error | A group box no longer covers its own nodes |
| `stray-in-group` | warning | A node sits inside a group box it is not a member of |
| `node-overlap` | warning | Two nodes occupy the same space |

Problems that were already there do not block anything. Only new ones do. When
the gate fires, the fix is usually one of these:

- `nodered_layout(op="free_region")` to claim clear canvas, then place there
- `nodered_group(op="refit", group_id=...)` to resize a group around its nodes
- `nodered_session(op="save", allow_overlap=true)` if the overlap is deliberate

Group geometry is exact, because the sizing rules come from the Node-RED
editor, so a computed box matches what the editor draws. Node geometry is exact
except for label text width, which is estimated from Helvetica metrics. That is
why node-level findings are only ever warnings.

### The staleness check

If someone presses Deploy in the browser while you have edits pending, your
commit would silently undo their work. Both sources guard against this, using
whatever the source can verify.

With the **Admin API**, Node-RED assigns a revision to every version of the
flows. The server sends back the revision it loaded, and Node-RED returns a
`409` if that is no longer current. Because Node-RED performs the check itself,
nothing can slip in between the check and the write.

With a **file**, the server records the modification time and size when it
loads, then checks again before every write. It uses nanosecond precision,
because a float timestamp only resolves to about a microsecond and a write
landing in the same tick as the load would look identical. If the timestamp
does not match, the server compares the file's actual contents before calling
it a conflict. A rewrite that produces the same flows you already have is not
someone else's work: after a deploy, Node-RED saves those same flows back to
the file with a new timestamp.

In both cases the commit is refused rather than overwriting the other change.
You can either `reload` and redo your edits, or pass `force=true`.

## Standalone use

Both engine modules work as libraries and as CLIs, without MCP.

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

> Running `--fix boxes` on its own makes things worse. Refitting grows some
> boxes so that they swallow neighboring nodes that are not members. Run
> `boxes,move` together, and read the dry run before passing `--apply`.

## Project layout

```
src/nodered_mcp/
├── server.py       FastMCP server: the eight tools
├── session.py      in-memory session, stdout capture, staleness guard
├── config.py       CLI flags and environment resolution
├── flows.py        the Flows class, composed from the mixins below
├── constants.py    defaults, group style, and the exception types
├── reports.py      ReadMixin      — summary, tab, group, search, trace
├── nodes.py        NodeEditMixin  — create/update/delete/wire nodes
├── groups.py       GroupMixin     — create, populate, and delete group boxes
├── tabs.py         TabMixin       — create, rename, reorder, and delete tabs
├── placement.py    LayoutMixin    — claim free canvas, refit boxes, arrange tabs
├── transfer.py     TransferMixin  — import and export node sets
├── persist.py      PersistMixin   — save, deploy, and the layout gate
└── layout.py       canvas geometry and linter, ported from the NR editor
home-assistant/     the Home Assistant add-on
Dockerfile          the standalone container image
```

`Flows` composes those mixins, so the public API stays flat: `f.summary()`,
`f.create_node()`, `f.free_region()`, `f.save()`.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests run against a synthetic fixture in `tests/fixtures/`, never a real flows
file. They cover configuration precedence, the read tools, the
in-memory-until-save behavior, the layout gate both blocking and overridden,
both staleness guards, both deploy routes, and read-only mode. One test checks
that no tool writes to stdout, because a stray print would corrupt the MCP
stdio framing.

CI runs the same checks through
[`ljmerza/misc-actions`](https://github.com/ljmerza/misc-actions).

A version tag publishes the package to PyPI and the image to GHCR. The
Home Assistant add-on installs the PyPI release named by its own `version`, so
bump `pyproject.toml` and `home-assistant/config.yaml` together, and add a line
to `home-assistant/CHANGELOG.md`. Home Assistant offers the update once
`config.yaml` changes on `main`, so merge the bump only after the release is on
PyPI.

## Contributing

Issues and pull requests are welcome. Please keep `ruff check`, `ruff format`,
and `pytest` green.

## Acknowledgments

- [Node-RED](https://nodered.org): the canvas geometry here is ported from its
  editor client, so group boxes match what the editor draws.
- [FastMCP](https://gofastmcp.com): the MCP server framework.

## License

MIT. See [`LICENSE`](LICENSE).
