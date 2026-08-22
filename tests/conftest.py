import json
import shutil
from pathlib import Path

import pytest

from nodered_mcp import server
from nodered_mcp.config import Settings
from nodered_mcp.session import FlowsSession

FIXTURE = Path(__file__).parent / "fixtures" / "sample_flows.json"

TAB = "tab0000000000001"
GROUP = "grp0000000000001"
INJECT = "inj0000000000001"
FUNC = "fn00000000000001"
DEBUG = "dbg0000000000001"
SWITCH = "sw00000000000001"
ORPHAN = "orp0000000000001"
HA_NODE = "ha00000000000001"


@pytest.fixture
def flows_file(tmp_path):
    """A disposable copy of the fixture flows.json."""
    target = tmp_path / "flows.json"
    shutil.copy(FIXTURE, target)
    return target


@pytest.fixture
def settings(flows_file):
    return Settings(
        flows_path=str(flows_file),
        container="test-nodered",
        restart_cmd=["true"],
    )


@pytest.fixture
def sess(settings, monkeypatch):
    """A live session wired into the server module, as main() would."""
    s = FlowsSession(settings)
    monkeypatch.setattr(server, "SESSION", s)
    return s


@pytest.fixture
def on_disk(flows_file):
    """Re-read flows.json from disk as a list of node dicts."""
    def read():
        return json.loads(flows_file.read_text())
    return read
