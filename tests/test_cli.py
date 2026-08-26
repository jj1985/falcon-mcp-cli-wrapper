"""Tests for the falcon-cli wrapper.

Everything here runs offline: catalog operations never touch the Falcon API,
and execution tests use a stub client so the whole in-process call path
(FastMCP tool dispatch included) is exercised without credentials.
"""

import io
import json

import pytest

from falcon_mcp_cli import cli, core

# --- argument parsing -------------------------------------------------------


def test_parse_string_and_json_args():
    args = cli.parse_tool_arguments(
        ['filter=platform_name:"Windows"', "limit:=5", "flag:=true", "ids:=[1,2]"],
        None,
    )
    assert args == {
        "filter": 'platform_name:"Windows"',
        "limit": 5,
        "flag": True,
        "ids": [1, 2],
    }


def test_parse_input_json_merged_and_overridden():
    args = cli.parse_tool_arguments(["limit:=10"], '{"filter": "x", "limit": 1}')
    assert args == {"filter": "x", "limit": 10}


def test_parse_equals_inside_value_stays_string():
    args = cli.parse_tool_arguments(["a=b:=c"], None)
    assert args == {"a": "b:=c"}


def test_parse_rejects_bare_token():
    with pytest.raises(core.UsageError):
        cli.parse_tool_arguments(["not-a-pair"], None)


def test_parse_rejects_bad_json_value():
    with pytest.raises(core.UsageError):
        cli.parse_tool_arguments(["limit:=not-json"], None)


def test_parse_rejects_non_object_input():
    with pytest.raises(core.UsageError):
        cli.parse_tool_arguments([], "[1, 2]")


def test_parse_input_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"limit": 3}'))
    assert cli.parse_tool_arguments([], "-") == {"limit": 3}


# --- catalog (no credentials) ----------------------------------------------


@pytest.fixture(scope="module")
def catalog():
    return core.Catalog()


def test_catalog_lists_tools(catalog):
    tools = catalog.tools()
    names = {t.name for t in tools}
    assert "falcon_search_hosts" in names
    assert len(tools) > 100


def test_catalog_tool_schema(catalog):
    info = catalog.tool("falcon_search_hosts")
    assert info.module == "hosts"
    assert info.read_only is True
    assert "filter" in info.input_schema.get("properties", {})


def test_catalog_flags_destructive_tools(catalog):
    info = catalog.tool("falcon_execute_workflow")
    assert info.read_only is False
    assert info.destructive is True


def test_catalog_unknown_tool_suggests(catalog):
    with pytest.raises(core.UsageError, match="falcon_search_hosts"):
        catalog.tool("falcon_search_hostz")


def test_catalog_module_filter():
    hosts_only = core.Catalog(modules=["hosts"])
    assert set(hosts_only.tool_to_module.values()) == {"hosts"}


def test_catalog_rejects_unknown_module():
    with pytest.raises(core.UsageError, match="Unknown modules"):
        core.Catalog(modules=["nope"])


def test_catalog_guides_readable(catalog):
    guides = catalog.guides()
    uris = {g.uri for g in guides}
    assert "falcon://hosts/search/fql-guide" in uris
    text = catalog.read_guide("falcon://hosts/search/fql-guide")
    assert "FQL" in text


# --- execution with a stub client ------------------------------------------


class StubFalconClient:
    """Looks enough like FalconClient for module code paths used in tests."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def command(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        return self.responses.get(
            operation, {"status_code": 200, "body": {"resources": []}}
        )

    async def command_async(self, operation, **kwargs):
        return self.command(operation, **kwargs)


def test_execute_tool_success():
    stub = StubFalconClient(
        {
            "QueryDevicesByFilter": {
                "status_code": 200,
                "body": {"resources": ["device-1"], "meta": {"pagination": {"total": 1}}},
            },
            "PostDeviceDetailsV2": {
                "status_code": 200,
                "body": {"resources": [{"device_id": "device-1", "hostname": "PC-1"}]},
            },
        }
    )
    result = core.execute_tool("falcon_search_hosts", {"limit": 1}, stub)
    text = json.dumps(result)
    assert "device-1" in text
    assert any(op == "QueryDevicesByFilter" for op, _ in stub.calls)


def test_execute_tool_api_error_raises():
    stub = StubFalconClient(
        {
            "QueryDevicesByFilter": {
                "status_code": 403,
                "body": {"errors": [{"message": "access denied"}]},
            }
        }
    )
    with pytest.raises(core.ToolExecutionError):
        core.execute_tool("falcon_search_hosts", {"limit": 1}, stub)


def test_execute_tool_unknown_name():
    with pytest.raises(core.UsageError):
        core.execute_tool("falcon_totally_fake", {}, StubFalconClient({}))


# --- CLI surface ------------------------------------------------------------


def test_cli_tools_json(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["tools", "--module", "hosts", "--json"])
    assert args.func(args) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] >= 3
    assert all(t["module"] == "hosts" for t in payload["tools"])


def test_cli_describe(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["describe", "falcon_search_hosts"])
    assert args.func(args) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "falcon_search_hosts"
    assert payload["input_schema"]["properties"]


def test_cli_readonly_refuses_write_tool(monkeypatch):
    monkeypatch.setenv("FALCON_CLI_READ_ONLY", "true")
    parser = cli.build_parser()
    args = parser.parse_args(["call", "falcon_execute_workflow", "definition_id=x"])
    with pytest.raises(core.UsageError, match="read-only"):
        args.func(args)
