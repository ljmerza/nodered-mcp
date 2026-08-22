"""Defaults, styles, and the one exception type shared across the engine.

Nothing here is hardcoded to a single machine: every path-ish value reads from
the environment and can be overridden by the caller or the CLI.
"""

import os
import shlex

FLOWS_PATH = os.environ.get("NODERED_FLOWS_PATH") or None
CONTAINER_NAME = os.environ.get("NODERED_CONTAINER", "nodered")
CONTAINER_FLOWS_PATH = os.environ.get("NODERED_CONTAINER_FLOWS_PATH", "/data/flows.json")


def default_restart_cmd(container):
    """Command used to restart Node-RED after a deploy.

    NODERED_RESTART_CMD is a shell-style string; "{container}" in it is
    substituted. Unset falls back to a plain `docker restart <container>`.
    """
    raw = os.environ.get("NODERED_RESTART_CMD")
    if raw:
        return [part.format(container=container) for part in shlex.split(raw)]
    return ["docker", "restart", container]


# The house style for every group. Node-RED groups in this project are always
# dark-filled; do not invent per-group colours.
DEFAULT_GROUP_STYLE = {
    "stroke": "#2e333a",
    "stroke-opacity": "1",
    "fill": "#2e333a",
    "fill-opacity": "0.75",
    "label": True,
    "label-position": "nw",
    "color": "#a4a4a4",
}

# Config nodes: real entries in flows.json, but they never sit on the canvas
# and never count as flow content.
CONFIG_TYPES = {
    "server",
    "mqtt-broker",
    "websocket-listener",
    "websocket-client",
    "pushbullet-config",
    "GSheetConnection",
    "ha-device-config",
    "ha-entity-config",
    "gauth",
}


class LayoutError(Exception):
    """Raised by save() when an edit introduces a new layout collision."""
