# Node-RED MCP

This add-on runs [nodered-mcp](https://github.com/ljmerza/nodered-mcp), an MCP
server for Node-RED. An MCP client can use it to read your flows, edit them,
and deploy the result.

It talks to Node-RED over the Admin API, the same endpoint the editor uses when
you press Deploy, so there is no restart and no downtime.

## Setup with the Node-RED add-on

1. Install and start the community **Node-RED** add-on.
2. Install this add-on and open its **Configuration** tab.
3. Set `username` and `password` to a Home Assistant user. The Node-RED add-on
   turns off Node-RED's own login and puts a proxy in front of port 1880 that
   checks a Home Assistant login on every request. A dedicated user for this
   add-on is a good idea.
4. If the Node-RED add-on has `ssl: true`, change `nodered_url` to
   `https://a0d7b954-nodered:1880` and turn off `verify_ssl`. The certificate
   is issued for your domain, not the add-on's hostname.
5. Set `auth_token` to a long random string (see [Security](#security)).
6. Start the add-on and check its log for `Loaded N nodes`.

## Connecting a client

The MCP endpoint is streamable HTTP on port 8765 of your Home Assistant host,
at `/mcp`. You can change the port on the **Network** section of the
Configuration tab.

Claude Code:

```bash
claude mcp add --transport http nodered http://homeassistant.local:8765/mcp \
  --header "Authorization: Bearer <auth_token>"
```

Clients that take a JSON config:

```json
{
  "mcpServers": {
    "nodered": {
      "type": "http",
      "url": "http://homeassistant.local:8765/mcp",
      "headers": { "Authorization": "Bearer <auth_token>" }
    }
  }
}
```

## Options

| Option | Default | Purpose |
|---|---|---|
| `nodered_url` | `http://a0d7b954-nodered:1880` | Base URL of Node-RED |
| `username` / `password` | | Home Assistant login, sent as HTTP Basic auth |
| `nodered_token` | | Admin API token, for a Node-RED with its own `adminAuth`. Not needed for the add-on |
| `verify_ssl` | `true` | Verify Node-RED's TLS certificate |
| `read_only` | `false` | Only expose the read tools |
| `auth_token` | | Bearer token MCP clients must send |
| `group_style` | | JSON merged over the default style for new groups |

## Security

The MCP endpoint can rewrite and deploy every flow, and Node-RED flows can run
arbitrary code. Without `auth_token`, anything that can reach the port has that
power. Set one, or turn on `read_only`, and do not forward the port to the
internet.

## How edits work

Edits stay in memory until the client calls `nodered_session(op="deploy")`, so
a build lands as a single deploy. If someone deploys from the browser while the
client has edits pending, Node-RED rejects the client's deploy instead of
overwriting that work.

`save` has nothing to do here, because the flows come over the Admin API rather
than from a file. Use `deploy`. Restarting the add-on discards edits that were
not deployed.
