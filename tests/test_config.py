import pytest

from nodered_mcp.config import build_parser, settings_from_args


def parse(argv, env=None, monkeypatch=None):
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    return build_parser().parse_args(argv)


def test_flows_path_is_required(monkeypatch):
    monkeypatch.delenv("NODERED_FLOWS_PATH", raising=False)
    args = build_parser().parse_args([])
    with pytest.raises(SystemExit, match="no flows.json path"):
        settings_from_args(args)


def test_missing_file_is_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv("NODERED_FLOWS_PATH", raising=False)
    args = build_parser().parse_args(["--flows-path", str(tmp_path / "nope.json")])
    with pytest.raises(SystemExit, match="not found"):
        settings_from_args(args)


def test_env_supplies_the_path(monkeypatch, flows_file):
    monkeypatch.setenv("NODERED_FLOWS_PATH", str(flows_file))
    s = settings_from_args(build_parser().parse_args([]))
    assert s.flows_path == str(flows_file)


def test_cli_flag_beats_env(monkeypatch, flows_file, tmp_path):
    other = tmp_path / "other.json"
    other.write_text("[]")
    monkeypatch.setenv("NODERED_FLOWS_PATH", str(other))
    s = settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))
    assert s.flows_path == str(flows_file)


def test_restart_cmd_defaults_to_docker_restart(monkeypatch, flows_file):
    monkeypatch.delenv("NODERED_RESTART_CMD", raising=False)
    args = build_parser().parse_args(["--flows-path", str(flows_file), "--container", "nr"])
    assert settings_from_args(args).restart_cmd == ["docker", "restart", "nr"]


def test_restart_cmd_substitutes_container(monkeypatch, flows_file):
    monkeypatch.setenv("NODERED_RESTART_CMD", "/opt/dcwrap restart {container}")
    args = build_parser().parse_args(["--flows-path", str(flows_file), "--container", "nr"])
    assert settings_from_args(args).restart_cmd == ["/opt/dcwrap", "restart", "nr"]
