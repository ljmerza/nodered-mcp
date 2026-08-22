"""Configuration for the Node-RED MCP server.

Resolution order for every setting, highest priority first:

    CLI flag  >  environment variable  >  built-in default

The one setting with no built-in default is the flows.json path -- there is no
sane guess for it, so the server refuses to start without one.
"""

from __future__ import annotations

import argparse
import os
import shlex
from dataclasses import dataclass, field

DEFAULT_CONTAINER = "nodered"
DEFAULT_CONTAINER_FLOWS_PATH = "/data/flows.json"


@dataclass
class Settings:
    """Everything the server needs to know about this Node-RED install."""

    flows_path: str
    container: str = DEFAULT_CONTAINER
    container_flows_path: str = DEFAULT_CONTAINER_FLOWS_PATH
    restart_cmd: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.restart_cmd:
            self.restart_cmd = ["docker", "restart", self.container]

    def describe(self) -> dict:
        return {
            "flows_path": self.flows_path,
            "container": self.container,
            "container_flows_path": self.container_flows_path,
            "restart_cmd": " ".join(self.restart_cmd),
        }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="nodered-mcp",
        description="MCP server for reading and editing a Node-RED flows.json.",
    )
    ap.add_argument(
        "--flows-path",
        default=os.environ.get("NODERED_FLOWS_PATH"),
        help="Path to flows.json. Required. Env: NODERED_FLOWS_PATH",
    )
    ap.add_argument(
        "--container",
        default=os.environ.get("NODERED_CONTAINER", DEFAULT_CONTAINER),
        help=f"Node-RED container name for deploy (default: {DEFAULT_CONTAINER}). "
             "Env: NODERED_CONTAINER",
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
    return ap


def settings_from_args(args: argparse.Namespace) -> Settings:
    """Turn parsed args into Settings, validating the path."""
    if not args.flows_path:
        raise SystemExit(
            "nodered-mcp: no flows.json path.\n"
            "  Pass --flows-path /path/to/flows.json, or set NODERED_FLOWS_PATH."
        )
    if not os.path.isfile(args.flows_path):
        raise SystemExit(f"nodered-mcp: flows.json not found: {args.flows_path}")

    restart_cmd = []
    if args.restart_cmd:
        restart_cmd = [p.format(container=args.container) for p in shlex.split(args.restart_cmd)]

    return Settings(
        flows_path=os.path.abspath(args.flows_path),
        container=args.container,
        container_flows_path=args.container_flows_path,
        restart_cmd=restart_cmd,
    )
