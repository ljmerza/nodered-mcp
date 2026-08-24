import pytest

from nodered_mcp.config import build_parser, settings_from_args


def parse(argv, env=None, monkeypatch=None):
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    return build_parser().parse_args(argv)


def test_a_source_is_required(monkeypatch):
    monkeypatch.delenv("NODERED_FLOWS_PATH", raising=False)
    monkeypatch.delenv("NODERED_URL", raising=False)
    args = build_parser().parse_args([])
    with pytest.raises(SystemExit, match="no flows to read"):
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


# ── Admin API deploy route ──


def test_no_url_means_the_docker_deploy_route(monkeypatch, flows_file):
    monkeypatch.delenv("NODERED_URL", raising=False)
    s = settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))
    assert s.admin_url == ""
    assert s.deploy_route == "docker"


def test_nodered_url_switches_to_the_admin_api_route(monkeypatch, flows_file):
    args = build_parser().parse_args(
        ["--flows-path", str(flows_file), "--nodered-url", "http://pi.local:1880"]
    )
    s = settings_from_args(args)
    assert s.admin_url == "http://pi.local:1880"
    assert s.deploy_route == "admin_api"


def test_nodered_url_comes_from_the_environment(monkeypatch, flows_file):
    monkeypatch.setenv("NODERED_URL", "http://pi.local:1880")
    s = settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))
    assert s.deploy_route == "admin_api"


def test_a_trailing_slash_on_the_url_is_dropped(monkeypatch, flows_file):
    args = build_parser().parse_args(
        ["--flows-path", str(flows_file), "--nodered-url", "http://pi.local:1880/"]
    )
    assert settings_from_args(args).admin_url == "http://pi.local:1880"


def test_token_comes_from_flag_or_environment(monkeypatch, flows_file):
    monkeypatch.setenv("NODERED_TOKEN", "from-env")
    base = ["--flows-path", str(flows_file), "--nodered-url", "http://pi.local:1880"]
    assert settings_from_args(build_parser().parse_args(base)).admin_token == "from-env"
    args = build_parser().parse_args([*base, "--nodered-token", "from-flag"])
    assert settings_from_args(args).admin_token == "from-flag"


def test_describe_never_echoes_the_token(monkeypatch, flows_file):
    args = build_parser().parse_args(
        [
            "--flows-path",
            str(flows_file),
            "--nodered-url",
            "http://pi.local:1880",
            "--nodered-token",
            "s3cret",
        ]
    )
    described = settings_from_args(args).describe()
    assert "s3cret" not in repr(described)
    assert described["authenticated"] is True


# ── finding the flow file ──


def test_a_single_discovered_flow_file_is_used(monkeypatch, tmp_path, capsys):
    """A native install names it after the hostname, which nobody guesses."""
    monkeypatch.delenv("NODERED_FLOWS_PATH", raising=False)
    monkeypatch.delenv("NODERED_URL", raising=False)
    userdir = tmp_path / ".node-red"
    userdir.mkdir()
    (userdir / "flows_raspberrypi.json").write_text("[]")
    (userdir / "flows_raspberrypi_cred.json").write_text("{}")
    monkeypatch.setattr("nodered_mcp.config.DEFAULT_USER_DIR", userdir)

    s = settings_from_args(build_parser().parse_args([]))
    assert s.flows_path == str(userdir / "flows_raspberrypi.json")
    assert "using discovered flow file" in capsys.readouterr().err


def test_several_discovered_flow_files_ask_rather_than_guess(monkeypatch, tmp_path):
    monkeypatch.delenv("NODERED_FLOWS_PATH", raising=False)
    monkeypatch.delenv("NODERED_URL", raising=False)
    userdir = tmp_path / ".node-red"
    userdir.mkdir()
    (userdir / "flows_one.json").write_text("[]")
    (userdir / "flows_two.json").write_text("[]")
    monkeypatch.setattr("nodered_mcp.config.DEFAULT_USER_DIR", userdir)

    with pytest.raises(SystemExit, match="several flow files"):
        settings_from_args(build_parser().parse_args([]))


def test_the_credentials_sidecar_is_never_a_candidate(monkeypatch, tmp_path):
    monkeypatch.delenv("NODERED_FLOWS_PATH", raising=False)
    monkeypatch.delenv("NODERED_URL", raising=False)
    userdir = tmp_path / ".node-red"
    userdir.mkdir()
    (userdir / "flows_cred.json").write_text("{}")
    monkeypatch.setattr("nodered_mcp.config.DEFAULT_USER_DIR", userdir)

    with pytest.raises(SystemExit, match="no flows to read"):
        settings_from_args(build_parser().parse_args([]))


def test_a_url_alone_is_enough(monkeypatch):
    monkeypatch.delenv("NODERED_FLOWS_PATH", raising=False)
    args = build_parser().parse_args(["--nodered-url", "http://pi.local:1880"])
    s = settings_from_args(args)
    assert s.flows_path == "" and s.source == "admin_api"


# ── group style ──


def test_group_style_defaults_to_the_editor_theme(monkeypatch, flows_file):
    monkeypatch.delenv("NODERED_GROUP_STYLE", raising=False)
    s = settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))
    assert s.group_style == {"label": True, "label-position": "nw"}


def test_group_style_json_is_merged_over_the_default(monkeypatch, flows_file):
    monkeypatch.setenv("NODERED_GROUP_STYLE", '{"fill": "#2e333a", "fill-opacity": "0.75"}')
    s = settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))
    assert s.group_style["fill"] == "#2e333a"
    assert s.group_style["label-position"] == "nw"


def test_bad_group_style_json_fails_at_startup(monkeypatch, flows_file):
    monkeypatch.setenv("NODERED_GROUP_STYLE", "{not json")
    with pytest.raises(SystemExit, match="not valid JSON"):
        settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))


def test_group_style_must_be_an_object(monkeypatch, flows_file):
    monkeypatch.setenv("NODERED_GROUP_STYLE", '["not", "an", "object"]')
    with pytest.raises(SystemExit, match="must be a JSON object"):
        settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))


# ── read-only ──


def test_read_only_off_by_default(monkeypatch, flows_file):
    monkeypatch.delenv("NODERED_READ_ONLY", raising=False)
    s = settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)]))
    assert s.read_only is False
    assert "read_only" not in s.describe()


def test_read_only_from_flag_or_env(monkeypatch, flows_file):
    args = build_parser().parse_args(["--flows-path", str(flows_file), "--read-only"])
    assert settings_from_args(args).read_only is True

    monkeypatch.setenv("NODERED_READ_ONLY", "true")
    assert settings_from_args(build_parser().parse_args(["--flows-path", str(flows_file)])).read_only
