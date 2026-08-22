"""The vendored modules must still work as plain CLIs, MCP aside."""

from nodered_mcp.flows import main as flows_main
from nodered_mcp.layout import main as layout_main


def test_flows_cli_summary(flows_file, capsys):
    assert flows_main(["summary", "--flows-path", str(flows_file)]) == 0
    assert "Demo" in capsys.readouterr().out


def test_flows_cli_without_a_path_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setattr("nodered_mcp.flows.FLOWS_PATH", None)
    assert flows_main(["summary"]) == 2
    assert "No flows.json path" in capsys.readouterr().out


def test_layout_cli_lints_clean(flows_file, capsys):
    assert layout_main(["--path", str(flows_file)]) == 0
    assert "layout clean" in capsys.readouterr().out


def test_layout_cli_json_output(flows_file, capsys):
    layout_main(["--path", str(flows_file), "--json"])
    assert capsys.readouterr().out.strip() == "[]"
