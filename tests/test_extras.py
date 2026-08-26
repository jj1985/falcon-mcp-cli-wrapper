"""Tests for the Fusion/Foundry extras modules (fully offline, stub client)."""

import json

import pytest

from falcon_mcp_cli import cli, core, extras


class StubClient:
    """Records command() calls and returns canned responses per operation."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def command(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        return self.responses.get(
            operation, {"status_code": 200, "body": {"resources": [{"ok": True}]}}
        )

    async def command_async(self, operation, **kwargs):
        return self.command(operation, **kwargs)


# --- catalog integration -----------------------------------------------------


def test_extras_modules_merged_into_catalog():
    catalog = core.Catalog()
    modules = {t.module for t in catalog.tools()}
    assert "workflows" in modules
    assert "foundry" in modules


def test_extras_do_not_collide_with_upstream():
    from falcon_mcp import registry

    upstream_tools = set(registry.get_tool_module_map())
    merged = extras.merged_tool_module_map()
    extra_tools = {t for t, m in merged.items() if m in ("workflows", "foundry")}
    assert extra_tools, "extras registered no tools"
    assert not (extra_tools & upstream_tools)
    # upstream fusion tools still resolve to fusion, not to our extras
    assert merged["falcon_execute_workflow"] == "fusion"


def test_extras_annotations():
    catalog = core.Catalog()
    assert catalog.tool("falcon_export_workflow").read_only is True
    imp = catalog.tool("falcon_import_workflow")
    assert imp.read_only is False and imp.destructive is False
    delete = catalog.tool("falcon_delete_workflow")
    assert delete.read_only is False and delete.destructive is True
    assert catalog.tool("falcon_run_foundry_search").read_only is True
    assert catalog.tool("falcon_execute_api_integration").destructive is True


def test_readonly_guardrail_refuses_extra_write_tool(monkeypatch):
    monkeypatch.setenv("FALCON_CLI_READ_ONLY", "true")
    parser = cli.build_parser()
    args = parser.parse_args(["call", "falcon_delete_workflow", 'ids:=["x"]'])
    with pytest.raises(core.UsageError, match="read-only"):
        args.func(args)


# --- workflows module --------------------------------------------------------


def test_export_workflow_returns_yaml_from_bytes():
    stub = StubClient({"WorkflowDefinitionsExport": b"name: my-workflow\nactions: []\n"})
    result = core.execute_tool("falcon_export_workflow", {"id": "abc"}, stub)
    assert result["yaml"].startswith("name: my-workflow")
    op, kwargs = stub.calls[0]
    assert op == "WorkflowDefinitionsExport"
    assert kwargs["parameters"]["id"] == "abc"
    assert kwargs["parameters"]["sanitize"] is True


def test_import_workflow_uploads_yaml_as_multipart():
    stub = StubClient()
    core.execute_tool(
        "falcon_import_workflow",
        {"yaml": "name: wf\nactions: []", "validate_only": True},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "WorkflowDefinitionsImport"
    field, (_filename, content, content_type) = kwargs["files"][0]
    assert field == "data_file"
    assert content == b"name: wf\nactions: []"
    assert content_type == "application/x-yaml"
    assert kwargs["parameters"]["validate_only"] is True


def test_update_workflow_sends_body():
    stub = StubClient()
    definition = {"definition_id": "abc", "name": "wf"}
    core.execute_tool("falcon_update_workflow", {"definition": definition}, stub)
    op, kwargs = stub.calls[0]
    assert op == "WorkflowDefinitionsUpdate"
    assert kwargs["body"] == definition


def test_delete_workflow_sends_ids():
    stub = StubClient()
    core.execute_tool("falcon_delete_workflow", {"ids": ["a", "b"]}, stub)
    op, kwargs = stub.calls[0]
    assert op == "WorkflowDefinitionsDelete"
    assert kwargs["parameters"]["ids"] == ["a", "b"]


def test_search_workflow_activities_envelope():
    stub = StubClient(
        {
            "WorkflowActivitiesCombined": {
                "status_code": 200,
                "body": {
                    "resources": [{"name": "My Foundry Function"}],
                    "meta": {"pagination": {"total": 1}},
                },
            }
        }
    )
    result = core.execute_tool("falcon_search_workflow_activities", {"limit": 5}, stub)
    assert result["results"][0]["name"] == "My Foundry Function"
    assert result["pagination"]["total"] == 1


def test_workflow_definition_action_enable_disable():
    stub = StubClient()
    core.execute_tool(
        "falcon_workflow_definition_action",
        {"action_name": "disable", "ids": ["d1"]},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "WorkflowDefinitionsAction"
    assert kwargs["parameters"] == {"action_name": "disable"}
    assert kwargs["body"] == {"ids": ["d1"]}


def test_search_workflow_activity_content_envelope():
    stub = StubClient(
        {
            "WorkflowActivitiesContentCombined": {
                "status_code": 200,
                "body": {"resources": [{"name": "act"}], "meta": {"pagination": {"total": 1}}},
            }
        }
    )
    result = core.execute_tool("falcon_search_workflow_activity_content", {"limit": 5}, stub)
    assert result["results"][0]["name"] == "act"
    assert result["pagination"]["total"] == 1


def test_workflow_execution_action():
    stub = StubClient()
    core.execute_tool(
        "falcon_workflow_execution_action",
        {"action_name": "resume", "ids": ["e1"]},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "WorkflowExecutionsAction"
    assert kwargs["parameters"] == {"action_name": "resume"}
    assert kwargs["body"] == {"ids": ["e1"]}


def test_update_workflow_human_input():
    stub = StubClient()
    core.execute_tool(
        "falcon_update_workflow_human_input",
        {"id": "h1", "input": "approve", "note": "ok"},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "WorkflowUpdateHumanInputV1"
    assert kwargs["parameters"] == {"id": "h1"}
    assert kwargs["body"] == {"input": "approve", "note": "ok"}


def test_workflow_api_error_maps_to_exit_error():
    stub = StubClient(
        {
            "WorkflowDefinitionsImport": {
                "status_code": 403,
                "body": {"errors": [{"message": "denied"}]},
            }
        }
    )
    with pytest.raises(core.ToolExecutionError) as exc:
        core.execute_tool("falcon_import_workflow", {"yaml": "a: b"}, stub)
    assert "error" in json.dumps(exc.value.result)


# --- system workflow definitions ---------------------------------------------


def test_provision_system_workflow():
    stub = StubClient()
    template = {"name": "T", "template_id": "t1", "parameters": {}}
    core.execute_tool("falcon_provision_system_workflow", {"template": template}, stub)
    op, kwargs = stub.calls[0]
    assert op == "WorkflowSystemDefinitionsProvision"
    assert kwargs["body"] == template


def test_deprovision_system_workflow_prunes_falsey():
    stub = StubClient()
    core.execute_tool(
        "falcon_deprovision_system_workflow",
        {"definition_id": "d1", "deprovision_all": False},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "WorkflowSystemDefinitionsDeProvision"
    # deprovision_all=False and the unset template fields are pruned
    assert kwargs["body"] == {"definition_id": "d1"}


# --- foundry lookup files ----------------------------------------------------


def test_upload_foundry_lookup_file_multipart():
    stub = StubClient()
    core.execute_tool(
        "falcon_upload_foundry_lookup_file",
        {"name": "block.csv", "content": "a,b\n1,2", "repo": "myrepo"},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "CreateFileV1"
    assert kwargs["data"] == {"name": "block.csv", "repo": "myrepo"}
    field, (fname, content) = kwargs["files"][0]
    assert field == "file"
    assert fname == "block.csv"
    assert content == b"a,b\n1,2"


def test_update_foundry_lookup_file_without_content_sends_no_file():
    stub = StubClient()
    core.execute_tool(
        "falcon_update_foundry_lookup_file",
        {"id": "f1", "description": "updated"},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "UpdateFileV1"
    assert kwargs["data"] == {"id": "f1", "description": "updated"}
    assert "files" not in kwargs


# --- RTR admin content -------------------------------------------------------


def test_create_rtr_script_formdata():
    stub = StubClient()
    core.execute_tool(
        "falcon_create_rtr_script",
        {
            "name": "cleanup",
            "content": "Write-Host hi",
            "description": "test",
            "permission_type": "group",
            "platform": "windows",
        },
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "RTR_CreateScripts"
    assert kwargs["data"]["name"] == "cleanup"
    assert kwargs["data"]["permission_type"] == "group"
    assert kwargs["data"]["content"] == "Write-Host hi"


def test_update_rtr_script_prunes_unset():
    stub = StubClient()
    core.execute_tool(
        "falcon_update_rtr_script", {"id": "s1", "description": "new desc"}, stub
    )
    op, kwargs = stub.calls[0]
    assert op == "RTR_UpdateScripts"
    assert kwargs["data"] == {"id": "s1", "description": "new desc"}


def test_create_rtr_put_file_multipart():
    stub = StubClient()
    core.execute_tool(
        "falcon_create_rtr_put_file",
        {"name": "tool.exe", "content": "MZ...", "description": "helper"},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "RTR_CreatePut_Files"
    assert kwargs["data"] == {"name": "tool.exe", "description": "helper"}
    field, (fname, content) = kwargs["files"][0]
    assert field == "file" and fname == "tool.exe" and content == b"MZ..."


def test_rtr_admin_module_in_catalog():
    catalog = core.Catalog()
    modules = {t.module for t in catalog.tools()}
    assert "rtradmin" in modules
    assert catalog.tool("falcon_delete_rtr_script").destructive is True
    assert catalog.tool("falcon_list_rtr_scripts").read_only is True


def test_rtradmin_does_not_shadow_upstream_rtr():
    # upstream ships an `rtr` module; ours is `rtradmin`, so upstream rtr tools
    # still resolve to rtr.
    merged = extras.merged_tool_module_map()
    rtr_tools = {t for t, m in merged.items() if m == "rtr"}
    assert rtr_tools, "upstream rtr tools missing"
    assert all(t.startswith("falcon_") for t in rtr_tools)


# --- foundry module ----------------------------------------------------------


def test_get_foundry_object_path_params_and_bytes():
    stub = StubClient({"GetObject": b'{"k": 1}'})
    result = core.execute_tool(
        "falcon_get_foundry_object",
        {"collection_name": "col", "object_key": "key1"},
        stub,
    )
    assert result["data"] == '{"k": 1}'
    op, kwargs = stub.calls[0]
    assert op == "GetObject"
    assert kwargs["collection_name"] == "col"
    assert kwargs["object_key"] == "key1"


def test_put_foundry_object():
    stub = StubClient()
    core.execute_tool(
        "falcon_put_foundry_object",
        {"collection_name": "col", "object_key": "k", "data": {"a": 1}, "dry_run": True},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "PutObject"
    assert kwargs["body"] == {"a": 1}
    assert kwargs["parameters"]["dry_run"] is True


def test_list_foundry_collections_describe_mode():
    stub = StubClient()
    core.execute_tool("falcon_list_foundry_collections", {"names": ["c1"]}, stub)
    assert stub.calls[0][0] == "DescribeCollections"
    stub2 = StubClient()
    core.execute_tool("falcon_list_foundry_collections", {}, stub2)
    assert stub2.calls[0][0] == "ListCollections"


def test_run_foundry_search_body():
    stub = StubClient()
    core.execute_tool(
        "falcon_run_foundry_search",
        {"query": "#event_simpleName=*", "repo_or_view": "search-all", "start": "1h"},
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "CreateSavedSearchesDynamicExecuteV1"
    assert kwargs["body"]["search_query"] == "#event_simpleName=*"
    assert kwargs["body"]["repo_or_view"] == "search-all"


def test_execute_api_integration_body():
    stub = StubClient()
    core.execute_tool(
        "falcon_execute_api_integration",
        {
            "definition_id": "d1",
            "operation_id": "op1",
            "request": {"params": {"q": 1}},
        },
        stub,
    )
    op, kwargs = stub.calls[0]
    assert op == "ExecuteCommand"
    resource = kwargs["body"]["resources"][0]
    assert resource == {
        "definition_id": "d1",
        "operation_id": "op1",
        "request": {"params": {"q": 1}},
    }
