"""Fusion SOAR workflow lifecycle tools (extra module).

Upstream falcon-mcp's ``fusion`` module covers searching definitions/executions,
reading execution results, and executing a workflow. This module adds the rest
of the workflow lifecycle from CrowdStrike's Workflows API: export, import
(create), update, delete, the activity catalog (the "functions" a workflow can
call, including Foundry function activities), triggers, mock executions,
execution actions (resume/retry), and human-input approvals.

Required API scopes:
    - Workflow: Read  (export, activities, triggers, human-input reads)
    - Workflow: Write (import, update, delete, mock execute, execution actions,
      human-input updates)
"""

from __future__ import annotations

from typing import Any

from falcon_mcp.common.errors import handle_api_response
from falcon_mcp.common.utils import prepare_api_parameters
from falcon_mcp.modules.base import BaseModule
from mcp.server import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

# Anything that runs or resumes real workflow actions inherits the workflow's
# blast radius, which cannot be inspected up front — same reasoning as
# upstream's falcon_execute_workflow.
DESTRUCTIVE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


class WorkflowsModule(BaseModule):
    """Fusion SOAR workflow lifecycle: export/import/update/delete and more."""

    def register_tools(self, server: FastMCP) -> None:
        self._add_tool(server, self.export_workflow, "export_workflow")
        self._add_tool(
            server, self.import_workflow, "import_workflow", annotations=WRITE_ANNOTATIONS
        )
        self._add_tool(
            server, self.update_workflow, "update_workflow", annotations=WRITE_ANNOTATIONS
        )
        self._add_tool(
            server,
            self.delete_workflow,
            "delete_workflow",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        self._add_tool(
            server,
            self.workflow_definition_action,
            "workflow_definition_action",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        self._add_tool(server, self.search_workflow_activities, "search_workflow_activities")
        self._add_tool(
            server,
            self.search_workflow_activity_content,
            "search_workflow_activity_content",
        )
        self._add_tool(server, self.search_workflow_triggers, "search_workflow_triggers")
        self._add_tool(
            server,
            self.mock_execute_workflow,
            "mock_execute_workflow",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        self._add_tool(
            server,
            self.workflow_execution_action,
            "workflow_execution_action",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        self._add_tool(server, self.get_workflow_human_inputs, "get_workflow_human_inputs")
        self._add_tool(
            server,
            self.update_workflow_human_input,
            "update_workflow_human_input",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )

    # --- definitions ---------------------------------------------------------

    def export_workflow(
        self,
        id: str = Field(description="Workflow definition ID to export."),
        sanitize: bool = Field(
            default=True,
            description="Remove PII (author names etc.) from the exported model.",
        ),
        include_mocks: bool = Field(
            default=False, description="Include activity mocks in the export."
        ),
    ) -> dict[str, Any]:
        """Export a Fusion SOAR workflow definition as a YAML model.

        Find the definition ID with `falcon_search_workflow_definitions`. The
        returned `yaml` string is the same importable model the Falcon console
        exports; feed it to `falcon_import_workflow` (optionally after editing)
        to clone or migrate a workflow, including across tenants.
        """
        response = self.client.command(
            "WorkflowDefinitionsExport",
            parameters=prepare_api_parameters(
                {"id": id, "sanitize": sanitize, "include_mocks": include_mocks}
            ),
        )
        # This endpoint answers with the YAML document itself (bytes), not a
        # JSON envelope; a JSON dict here means an error.
        if isinstance(response, bytes):
            return {"id": id, "yaml": response.decode("utf-8", errors="replace")}
        if isinstance(response, str):
            return {"id": id, "yaml": response}
        return handle_api_response(
            response,
            operation="WorkflowDefinitionsExport",
            error_message="Failed to export workflow definition",
        )

    def import_workflow(
        self,
        yaml: str = Field(
            description=(
                "The workflow definition model in YAML format — the content "
                "produced by `falcon_export_workflow` or the Falcon console's "
                "export. Pass the YAML text itself, not a file path."
            ),
        ),
        name: str | None = Field(
            default=None, description="Override the workflow name on import."
        ),
        validate_only: bool = Field(
            default=False,
            description="Validate the model without saving the workflow.",
        ),
    ) -> dict[str, Any]:
        """Import (create) a Fusion SOAR workflow from a YAML definition model.

        Creates a new workflow definition from an exported model. Set
        validate_only=true to lint a model without changing the tenant. From the
        shell, pass file contents with: yaml="$(cat workflow.yaml)".
        """
        response = self.client.command(
            "WorkflowDefinitionsImport",
            files=[("data_file", ("workflow.yaml", yaml.encode(), "application/x-yaml"))],
            parameters=prepare_api_parameters(
                {"name": name, "validate_only": validate_only}
            ),
        )
        return handle_api_response(
            response,
            operation="WorkflowDefinitionsImport",
            error_message="Failed to import workflow definition",
        )

    def update_workflow(
        self,
        definition: dict[str, Any] = Field(
            description=(
                "The full workflow definition model to save, as a JSON object. "
                "Start from the model returned by the definitions search or an "
                "export, modify it, and submit the whole object — this is a full "
                "replacement, not a patch."
            ),
        ),
        validate_only: bool = Field(
            default=False,
            description="Validate the model without saving the changes.",
        ),
    ) -> dict[str, Any]:
        """Update (modify) an existing Fusion SOAR workflow definition.

        Saves a new version of the definition; previous versions remain in the
        tenant's version history. Set validate_only=true to check a model
        without changing anything.
        """
        response = self.client.command(
            "WorkflowDefinitionsUpdate",
            body=definition,
            parameters=prepare_api_parameters({"validate_only": validate_only}),
        )
        return handle_api_response(
            response,
            operation="WorkflowDefinitionsUpdate",
            error_message="Failed to update workflow definition",
        )

    def delete_workflow(
        self,
        ids: list[str] = Field(description="Workflow definition IDs to delete."),
    ) -> dict[str, Any]:
        """Delete Fusion SOAR workflow definitions. This cannot be undone.

        Export a definition first (`falcon_export_workflow`) if you may need to
        restore it later.
        """
        response = self.client.command(
            "WorkflowDefinitionsDelete", parameters={"ids": ids}
        )
        return handle_api_response(
            response,
            operation="WorkflowDefinitionsDelete",
            error_message="Failed to delete workflow definitions",
            default_result={"deleted": ids},
        )

    def workflow_definition_action(
        self,
        action_name: str = Field(
            description=(
                "Action to perform: enable (workflow starts firing on its "
                "trigger events), disable (stops reacting to new trigger "
                "events), or cancel (stop all in-flight executions)."
            ),
        ),
        ids: list[str] = Field(description="Workflow definition IDs to act on."),
    ) -> dict[str, Any]:
        """Enable, disable, or cancel Fusion SOAR workflow definitions.

        Enabling puts real automation live; cancel irreversibly stops the
        definition's in-flight executions. Disable/enable are each other's
        undo. Find definition IDs with `falcon_search_workflow_definitions`
        (its records include the current `enabled` state).
        """
        response = self.client.command(
            "WorkflowDefinitionsAction",
            body={"ids": ids},
            parameters={"action_name": action_name},
        )
        return handle_api_response(
            response,
            operation="WorkflowDefinitionsAction",
            error_message="Failed to perform workflow definition action",
        )

    # --- building blocks: activities ("functions") and triggers --------------

    def search_workflow_activities(
        self,
        filter: str | None = Field(
            default=None,
            description=(
                "FQL filter over the activity catalog (e.g. name:*'*<keyword>*'). "
                "Leave empty to page through everything."
            ),
        ),
        limit: int = Field(default=20, ge=1, le=500, description="Max records. [1-500]"),
        offset: int | None = Field(default=None, description="Pagination offset."),
        sort: str | None = Field(default=None, description="Sort expression, e.g. name.asc"),
    ) -> dict[str, Any]:
        """Search the catalog of Fusion SOAR activities — the actions/functions a workflow can call.

        This is where custom Foundry functions surface once deployed: each
        appears as an activity alongside the built-in CrowdStrike actions, with
        its input/output schema. Use it to discover what a workflow you are
        building or editing can invoke.
        Responses include `pagination.total` — use it to answer "how many" questions.
        """
        results, pagination = self._base_search_with_meta(
            operation="WorkflowActivitiesCombined",
            search_params={"filter": filter, "limit": limit, "offset": offset, "sort": sort},
            error_message="Failed to search workflow activities",
        )
        if self._is_error(results):
            return results
        return self._build_pagination_envelope(results or [], pagination, filter)

    def search_workflow_activity_content(
        self,
        filter: str | None = Field(
            default=None, description="FQL filter over activity content records."
        ),
        limit: int = Field(default=20, ge=1, le=500, description="Max records. [1-500]"),
        offset: int | None = Field(default=None, description="Pagination offset."),
        sort: str | None = Field(default=None, description="Sort expression."),
    ) -> dict[str, Any]:
        """Search full Fusion SOAR activity content records.

        Richer than `falcon_search_workflow_activities`: returns the complete
        content model for each activity (full input/output field definitions),
        which is what a definition's action nodes must conform to when building
        or editing workflow models by hand.
        Responses include `pagination.total` — use it to answer "how many" questions.
        """
        results, pagination = self._base_search_with_meta(
            operation="WorkflowActivitiesContentCombined",
            search_params={"filter": filter, "limit": limit, "offset": offset, "sort": sort},
            error_message="Failed to search workflow activity content",
        )
        if self._is_error(results):
            return results
        return self._build_pagination_envelope(results or [], pagination, filter)

    def search_workflow_triggers(
        self,
        filter: str | None = Field(
            default=None, description="FQL filter over the trigger catalog."
        ),
        limit: int = Field(default=20, ge=1, le=500, description="Max records. [1-500]"),
        offset: int | None = Field(default=None, description="Pagination offset."),
    ) -> dict[str, Any]:
        """Search the catalog of Fusion SOAR triggers — the events that can start a workflow.

        Returns each trigger with the field schema its events carry, which is
        what a workflow definition's `trigger` block must reference.
        Responses include `pagination.total` — use it to answer "how many" questions.
        """
        results, pagination = self._base_search_with_meta(
            operation="WorkflowTriggersCombined",
            search_params={"filter": filter, "limit": limit, "offset": offset},
            error_message="Failed to search workflow triggers",
        )
        if self._is_error(results):
            return results
        return self._build_pagination_envelope(results or [], pagination, filter)

    # --- executions -----------------------------------------------------------

    def mock_execute_workflow(
        self,
        definition_id: str = Field(description="Workflow definition ID to mock-execute."),
        payload: dict[str, Any] = Field(
            default_factory=dict,
            description=(
                "Execution body: the trigger input for the run plus a `mocks` "
                "entry mapping activity names to mocked outputs."
            ),
        ),
        validate_only: bool = Field(
            default=False, description="Validate the mock execution without running it."
        ),
    ) -> dict[str, Any]:
        """Mock-execute a Fusion SOAR workflow for testing.

        Activities named in `payload.mocks` return the mocked output instead of
        running; any activity NOT mocked executes for real, so treat this with
        the same caution as a live execution unless every effectful activity is
        mocked or validate_only=true.
        """
        response = self.client.command(
            "WorkflowMockExecute",
            body=payload or {},
            parameters=prepare_api_parameters(
                {"definition_id": definition_id, "validate_only": validate_only}
            ),
        )
        return handle_api_response(
            response,
            operation="WorkflowMockExecute",
            error_message="Failed to mock-execute workflow",
        )

    def workflow_execution_action(
        self,
        action_name: str = Field(
            description="Action to perform on the executions, e.g. resume."
        ),
        ids: list[str] = Field(description="Workflow execution IDs to act on."),
        action_parameters: list[dict[str, Any]] | None = Field(
            default=None,
            description=(
                "Optional action parameters as [{'name': ..., 'value': ...}] "
                "objects, when the action requires them."
            ),
        ),
    ) -> dict[str, Any]:
        """Perform an action (e.g. resume) on Fusion SOAR workflow executions.

        Resuming continues the workflow's real actions from where it paused.
        Find execution IDs with `falcon_search_workflow_executions`.
        """
        body: dict[str, Any] = {"ids": ids}
        if action_parameters:
            body["action_parameters"] = action_parameters
        response = self.client.command(
            "WorkflowExecutionsAction",
            body=body,
            parameters={"action_name": action_name},
        )
        return handle_api_response(
            response,
            operation="WorkflowExecutionsAction",
            error_message="Failed to perform workflow execution action",
        )

    # --- human inputs ---------------------------------------------------------

    def get_workflow_human_inputs(
        self,
        ids: list[str] = Field(description="Human-input IDs to retrieve."),
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Read pending Fusion SOAR human-input (approval) requests.

        A paused execution's human-input IDs appear in its execution record
        (`falcon_search_workflow_executions`). The returned record shows the
        prompt and the input options the workflow is waiting on.
        """
        response = self.client.command("WorkflowGetHumanInputV1", parameters={"ids": ids})
        return handle_api_response(
            response,
            operation="WorkflowGetHumanInputV1",
            error_message="Failed to get workflow human inputs",
        )

    def update_workflow_human_input(
        self,
        id: str = Field(description="Human-input ID to answer."),
        input: str = Field(
            description="The response to provide, e.g. an approval option's value."
        ),
        note: str | None = Field(default=None, description="Optional note to record."),
    ) -> dict[str, Any]:
        """Answer a pending Fusion SOAR human-input (approve/deny) request.

        Providing the input resumes the workflow, which then continues its real
        actions — treat an approval as executing whatever the workflow does next.
        """
        body: dict[str, Any] = {"input": input}
        if note:
            body["note"] = note
        response = self.client.command(
            "WorkflowUpdateHumanInputV1", body=body, parameters={"id": id}
        )
        return handle_api_response(
            response,
            operation="WorkflowUpdateHumanInputV1",
            error_message="Failed to update workflow human input",
        )
