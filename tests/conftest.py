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


@pytest.fixture(autouse=True)
def isolated_user_dir(tmp_path, monkeypatch):
    """Never let flow-file discovery see the real ~/.node-red.

    Autouse: a developer who happens to run Node-RED natively would otherwise
    get different results from this suite than CI does.
    """
    empty = tmp_path / "no-node-red"
    empty.mkdir()
    monkeypatch.setattr("nodered_mcp.config.DEFAULT_USER_DIR", empty)
    return empty


@pytest.fixture
def flows_file(tmp_path):
    """A disposable copy of the fixture flows.json."""
    target = tmp_path / "flows.json"
    shutil.copy(FIXTURE, target)
    return target


@pytest.fixture
def settings(flows_file):
    return Settings(flows_path=str(flows_file), container="test-nodered", restart_cmd=["true"])


@pytest.fixture
def admin_settings(flows_file):
    """Settings that route deploy over the Node-RED Admin API."""
    return Settings(flows_path=str(flows_file), admin_url="http://nodered.test:1880")


@pytest.fixture
def api_only_settings():
    """Settings with no local file at all -- flows come from the Admin API."""
    return Settings(flows_path="", admin_url="http://nodered.test:1880")


@pytest.fixture
def api_flows(fake_urlopen):
    """Serve the fixture flows over the fake Admin API, v2 style.

    Returns the mutable dict backing the responses, so a test can change the
    served rev to simulate somebody else deploying.
    """
    state = {"rev": "rev-1", "flows": json.loads(FIXTURE.read_text())}

    def body(req):
        if req.get_method() == "GET":
            return json.dumps(state).encode()
        return json.dumps({"rev": "rev-2"}).encode()

    fake_urlopen.body = body
    fake_urlopen.status = 200
    return state


@pytest.fixture
def fake_urlopen(monkeypatch):
    """Capture Admin API requests instead of making them.

    Returns the list the captured urllib Requests land in; set .fail on it to
    an exception to make urlopen raise that instead.
    """

    class Response:
        status = 204
        body = b""

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Calls(list):
        fail = None  # set to an exception to make the next urlopen raise
        status = 204
        body = b""  # bytes, or a callable(req) -> bytes for per-request bodies

    calls = Calls()
    calls.contexts = []  # the ssl context passed with each request, None if none

    def urlopen(req, timeout=None, context=None):
        calls.append(req)
        calls.contexts.append(context)
        if calls.fail is not None:
            raise calls.fail
        resp = Response()
        resp.status = calls.status
        resp.body = calls.body(req) if callable(calls.body) else calls.body
        return resp

    monkeypatch.setattr("nodered_mcp.persist.urllib.request.urlopen", urlopen)
    return calls


@pytest.fixture
def sess(settings, monkeypatch):
    """A live session wired into the server module, as main() would."""
    s = FlowsSession(settings)
    monkeypatch.setattr(server, "SESSION", s)
    return s


@pytest.fixture
def admin_sess(admin_settings, monkeypatch):
    """A live session whose deploy goes over the Admin API."""
    s = FlowsSession(admin_settings)
    monkeypatch.setattr(server, "SESSION", s)
    return s


@pytest.fixture
def on_disk(flows_file):
    """Re-read flows.json from disk as a list of node dicts."""

    def read():
        return json.loads(flows_file.read_text())

    return read
