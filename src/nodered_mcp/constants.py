"""Defaults, styles, and the exception types shared across the engine.

Nothing here is hardcoded to a single machine: every path-ish value reads from
the environment and can be overridden by the caller or the CLI.
"""

import json
import os
import shlex

FLOWS_PATH = os.environ.get("NODERED_FLOWS_PATH") or None
CONTAINER_NAME = os.environ.get("NODERED_CONTAINER", "nodered")
CONTAINER_FLOWS_PATH = os.environ.get("NODERED_CONTAINER_FLOWS_PATH", "/data/flows.json")
ADMIN_URL = os.environ.get("NODERED_URL") or ""
ADMIN_TOKEN = os.environ.get("NODERED_TOKEN") or ""

# Seconds to wait on the Admin API. A full deploy stops and restarts every
# node, so a big flow set on a Pi can legitimately take a while to answer.
ADMIN_TIMEOUT = float(os.environ.get("NODERED_TIMEOUT", "30"))

# When Node-RED drops the connection mid-deploy (it crashed or restarted while
# installing the flows), keep asking GET /flows this long to find out whether
# the deploy landed. A container restart typically takes 20-30 s.
DEPLOY_CONFIRM_TIMEOUT = float(os.environ.get("NODERED_DEPLOY_CONFIRM_TIMEOUT", "60"))


def default_restart_cmd(container):
    """Command used to restart Node-RED after a deploy.

    NODERED_RESTART_CMD is a shell-style string; "{container}" in it is
    substituted. Unset falls back to a plain `docker restart <container>`.
    """
    raw = os.environ.get("NODERED_RESTART_CMD")
    if raw:
        return [part.format(container=container) for part in shlex.split(raw)]
    return ["docker", "restart", container]


# Node-RED's own default for a new group -- deliberately colourless. The editor
# computes stroke and fill from the active theme's CSS at render time (node-red
# 5.x builds its `defaultGroupStyle` with getComputedStyle on
# .red-ui-flow-group-body), so a group stored without them follows whatever
# theme the person looking at it uses. Hardcoding colours here would force one
# palette on every install.
DEFAULT_GROUP_STYLE = {"label": True, "label-position": "nw"}


def default_group_style(raw=None):
    """Style applied to groups this server creates.

    A JSON object in NODERED_GROUP_STYLE (or --group-style) is merged over the
    Node-RED default, so `{"fill": "#2e333a", "fill-opacity": "0.75"}` restyles
    those keys and leaves everything else to the theme.
    """
    style = dict(DEFAULT_GROUP_STYLE)
    raw = raw if raw is not None else os.environ.get("NODERED_GROUP_STYLE")
    if not raw:
        return style
    if isinstance(raw, dict):
        style.update(raw)
        return style
    try:
        override = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"NODERED_GROUP_STYLE is not valid JSON: {exc}") from exc
    if not isinstance(override, dict):
        raise ValueError('NODERED_GROUP_STYLE must be a JSON object, e.g. \'{"fill": "#2e333a"}\'')
    style.update(override)
    return style


class LayoutError(Exception):
    """Raised by save() when an edit introduces a new layout collision."""


class DeployError(Exception):
    """Raised by deploy() when the flows never reached the running Node-RED."""


class ConnectionDroppedError(DeployError):
    """Node-RED closed the connection before answering.

    Unlike an unreachable host, the request was sent, so it may well have
    landed -- the usual cause is Node-RED restarting while installing it.
    """


class StaleFlowsError(RuntimeError):
    """The flows changed underneath us while edits were pending in memory.

    Lives here rather than in session.py because both guards raise it: the
    file-source one comparing flows.json against what we loaded, and the
    API-source one relaying Node-RED's own 409 revision mismatch.
    """
