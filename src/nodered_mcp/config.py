"""Configuration for the Node-RED MCP server.

Resolution order for every setting, highest priority first:

    CLI flag  >  environment variable  >  built-in default

The one setting with no built-in default is the flows.json path -- there is no
sane guess for it, so the server refuses to start without one.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shlex
import sys
from dataclasses import dataclass, field

from .constants import default_group_style

DEFAULT_CONTAINER = "nodered"
DEFAULT_CONTAINER_FLOWS_PATH = "/data/flows.json"

# Where a stock Node-RED keeps its user data. Only the Docker image names the
# flow file `flows.json` (it sets FLOWS=flows.json); a plain `npm install -g
# node-red` writes flows_<hostname>.json instead. Nobody guesses that, so look.
DEFAULT_USER_DIR = pathlib.Path.home() / ".node-red"


def discover_flow_files(user_dir=None):
    """Flow files in the default Node-RED user dir, newest-looking first.

    Excludes the `_cred.json` sidecars, which are encrypted credentials and
    never a flow set.
    """
    d = pathlib.Path(user_dir or DEFAULT_USER_DIR)
    try:
        return sorted(p for p in d.glob("flows*.json") if not p.name.endswith("_cred.json"))
    except OSError:
        return []


@dataclass
class Settings:
    """Everything the server needs to know about this Node-RED install."""

    flows_path: str = ""
    container: str = DEFAULT_CONTAINER
    container_flows_path: str = DEFAULT_CONTAINER_FLOWS_PATH
    restart_cmd: list[str] = field(default_factory=list)
    admin_url: str = ""
    admin_token: str = ""
    admin_username: str = ""
    admin_password: str = ""
    admin_insecure: bool = False
    group_style: dict = field(default_factory=dict)
    read_only: bool = False

    def __post_init__(self):
        if not self.restart_cmd:
            self.restart_cmd = ["docker", "restart", self.container]
        if not self.group_style:
            self.group_style = default_group_style()

    @property
    def source(self) -> str:
        """Where the flows are read from."""
        return "file" if self.flows_path else "admin_api"

    @property
    def deploy_route(self) -> str:
        """Which mechanism `deploy` will use to reach Node-RED."""
        return "admin_api" if self.admin_url else "docker"

    def describe(self) -> dict:
        d = {"source": self.source, "deploy_route": self.deploy_route}
        if self.flows_path:
            d["flows_path"] = self.flows_path
        if self.admin_url:
            # Never echo the token; only whether one is configured.
            d["nodered_url"] = self.admin_url
            d["authenticated"] = bool(self.admin_token or self.admin_username)
            if self.admin_insecure:
                d["tls_verify"] = False
        else:
            d["container"] = self.container
            d["container_flows_path"] = self.container_flows_path
            d["restart_cmd"] = " ".join(self.restart_cmd)
        if self.read_only:
            d["read_only"] = True
        return d


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="nodered-mcp", description="MCP server for reading and editing a Node-RED flows.json."
    )
    ap.add_argument(
        "--flows-path",
        default=os.environ.get("NODERED_FLOWS_PATH"),
        help="Path to flows.json. Optional if --nodered-url is set, in which case the "
        "flows are read over the Admin API. Env: NODERED_FLOWS_PATH",
    )
    ap.add_argument(
        "--container",
        default=os.environ.get("NODERED_CONTAINER", DEFAULT_CONTAINER),
        help=f"Node-RED container name for deploy (default: {DEFAULT_CONTAINER}). Env: NODERED_CONTAINER",
    )
    ap.add_argument(
        "--container-flows-path",
        default=os.environ.get("NODERED_CONTAINER_FLOWS_PATH", DEFAULT_CONTAINER_FLOWS_PATH),
        help=f"flows.json path inside the container (default: {DEFAULT_CONTAINER_FLOWS_PATH}). "
        "Env: NODERED_CONTAINER_FLOWS_PATH",
    )
    ap.add_argument(
        "--restart-cmd",
        default=os.environ.get("NODERED_RESTART_CMD"),
        help="Shell-style restart command; '{container}' is substituted. "
        "Default: docker restart <container>. Env: NODERED_RESTART_CMD",
    )
    ap.add_argument(
        "--nodered-url",
        default=os.environ.get("NODERED_URL"),
        help="Base URL of the running Node-RED, e.g. http://localhost:1880. Set this and "
        "deploy goes over the Admin API instead of docker cp + restart -- no container "
        "runtime needed, and Node-RED may be on another host. Env: NODERED_URL",
    )
    ap.add_argument(
        "--nodered-token",
        default=os.environ.get("NODERED_TOKEN"),
        help="Admin API access token, if Node-RED has adminAuth enabled. Env: NODERED_TOKEN",
    )
    ap.add_argument(
        "--nodered-username",
        default=os.environ.get("NODERED_USERNAME"),
        help="Username sent as HTTP Basic auth, for a Node-RED behind a proxy that asks "
        "for a login (the Home Assistant add-on does). Env: NODERED_USERNAME",
    )
    ap.add_argument(
        "--nodered-password",
        default=os.environ.get("NODERED_PASSWORD"),
        help="Password for --nodered-username. Env: NODERED_PASSWORD",
    )
    ap.add_argument(
        "--nodered-insecure",
        action="store_true",
        default=os.environ.get("NODERED_INSECURE", "").lower() in ("1", "true", "yes"),
        help="Skip TLS certificate verification for --nodered-url, e.g. when reaching "
        "Node-RED by a hostname its certificate does not cover. Env: NODERED_INSECURE",
    )
    ap.add_argument(
        "--group-style",
        default=os.environ.get("NODERED_GROUP_STYLE"),
        help="JSON object merged over Node-RED's default style for groups this server "
        'creates, e.g. \'{"fill": "#2e333a"}\'. Unset follows the editor theme. '
        "Env: NODERED_GROUP_STYLE",
    )
    ap.add_argument(
        "--read-only",
        action="store_true",
        default=os.environ.get("NODERED_READ_ONLY", "").lower() in ("1", "true", "yes"),
        help="Expose only the read tools. Nothing can edit, save, or deploy. Env: NODERED_READ_ONLY",
    )
    ap.add_argument(
        "--transport",
        default=os.environ.get("NODERED_MCP_TRANSPORT", "stdio"),
        choices=["stdio", "http", "sse"],
        help="MCP transport (default: stdio). Env: NODERED_MCP_TRANSPORT",
    )
    ap.add_argument(
        "--host",
        default=os.environ.get("NODERED_MCP_HOST", "127.0.0.1"),
        help="Bind host for http/sse transports (default: 127.0.0.1).",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NODERED_MCP_PORT", "8080")),
        help="Bind port for http/sse transports (default: 8080).",
    )
    ap.add_argument(
        "--auth-token",
        default=os.environ.get("NODERED_MCP_AUTH_TOKEN"),
        help="Require MCP clients to send 'Authorization: Bearer <token>'. Only applies "
        "to the http and sse transports. Env: NODERED_MCP_AUTH_TOKEN",
    )
    return ap


def settings_from_args(args: argparse.Namespace) -> Settings:
    """Turn parsed args into Settings, resolving where the flows come from."""
    admin_url = (args.nodered_url or "").rstrip("/")
    flows_path = _resolve_flows_path(args.flows_path, admin_url)

    restart_cmd = []
    if args.restart_cmd:
        restart_cmd = [p.format(container=args.container) for p in shlex.split(args.restart_cmd)]

    try:
        group_style = default_group_style(args.group_style)
    except ValueError as exc:
        raise SystemExit(f"nodered-mcp: --group-style: {exc}") from exc

    return Settings(
        flows_path=flows_path,
        container=args.container,
        container_flows_path=args.container_flows_path,
        restart_cmd=restart_cmd,
        admin_url=admin_url,
        admin_token=args.nodered_token or "",
        admin_username=args.nodered_username or "",
        admin_password=args.nodered_password or "",
        admin_insecure=bool(args.nodered_insecure),
        group_style=group_style,
        read_only=bool(args.read_only),
    )


def _resolve_flows_path(given: str | None, admin_url: str) -> str:
    """Settle on a local flows.json, or "" to read over the Admin API.

    An explicit path always wins and must exist. With no path but a URL, the
    flows come from Node-RED itself. With neither, look in the default user
    dir before giving up -- a native install's flow file is named after the
    hostname, which nobody guesses on the first try.
    """
    if given:
        if not os.path.isfile(given):
            raise SystemExit(f"nodered-mcp: flows.json not found: {given}")
        return os.path.abspath(given)

    if admin_url:
        return ""  # read over GET /flows

    found = discover_flow_files()
    if len(found) == 1:
        print(f"nodered-mcp: using discovered flow file {found[0]}", file=sys.stderr)
        return str(found[0])

    if len(found) > 1:
        listing = "\n".join(f"    --flows-path {p}" for p in found)
        raise SystemExit(f"nodered-mcp: several flow files in {DEFAULT_USER_DIR}. Pick one:\n{listing}")

    raise SystemExit(
        "nodered-mcp: no flows to read.\n"
        "  Point at a running Node-RED and read them over its Admin API:\n"
        "    --nodered-url http://localhost:1880   (env: NODERED_URL)\n"
        "  Or point at the file directly:\n"
        "    --flows-path /path/to/flows.json      (env: NODERED_FLOWS_PATH)\n"
        f"  Nothing matching flows*.json in {DEFAULT_USER_DIR}. A Docker install keeps "
        "it in the container's /data; a native one names it after the hostname."
    )
