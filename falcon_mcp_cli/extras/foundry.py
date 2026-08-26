"""Falcon Foundry platform tools (extra module).

Covers the Foundry capabilities with public APIs that upstream falcon-mcp does
not include: custom-storage collections (the data store Foundry apps and
functions use), Foundry/LogScale saved-search execution, and API integrations
(the plugins Foundry apps define around third-party APIs).

Note on Foundry *functions*: deploying or invoking function code directly has
no public API — functions are managed with CrowdStrike's Foundry CLI and, once
deployed, surface as workflow *activities*; discover and run them through
`falcon_search_workflow_activities` and the workflow execution tools.

Required API scopes:
    - Custom Storage: Read / Write   (collections and objects)
    - Foundry Platform: Read / Write (saved-search execution, repos/views)
    - API Integrations: Read / Write (plugin configs, plugin execution)
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
    idempotentHint=True,
    openWorldHint=True,
)

DESTRUCTIVE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


class FoundryModule(BaseModule):
    """Falcon Foundry: custom storage, LogScale search, API integrations."""

    def register_tools(self, server: FastMCP) -> None:
        self._add_tool(server, self.list_foundry_collections, "list_foundry_collections")
        self._add_tool(server, self.list_foundry_objects, "list_foundry_objects")
        self._add_tool(server, self.search_foundry_objects, "search_foundry_objects")
        self._add_tool(server, self.get_foundry_object, "get_foundry_object")
        self._add_tool(
            server, self.put_foundry_object, "put_foundry_object", annotations=WRITE_ANNOTATIONS
        )
        self._add_tool(
            server,
            self.delete_foundry_object,
            "delete_foundry_object",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        self._add_tool(server, self.list_foundry_repos, "list_foundry_repos")
        self._add_tool(server, self.run_foundry_search, "run_foundry_search")
        self._add_tool(server, self.get_foundry_search_results, "get_foundry_search_results")
        self._add_tool(server, self.list_api_integrations, "list_api_integrations")
        self._add_tool(
            server,
            self.execute_api_integration,
            "execute_api_integration",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )

    # --- custom storage (collections) ----------------------------------------

    def list_foundry_collections(
        self,
        limit: int = Field(default=100, ge=1, le=500, description="Max records. [1-500]"),
        start: str | None = Field(
            default=None, description="Pagination: start key from a previous page."
        ),
        names: list[str] | None = Field(
            default=None,
            description=(
                "Optional collection names to describe in detail (schemas and "
                "metadata) instead of listing names."
            ),
        ),
    ) -> dict[str, Any] | list[Any]:
        """List Foundry custom-storage collections, or describe named ones.

        Collections are the key-value/document stores Foundry apps and
        functions read and write. Without `names`, returns the collection
        names; with `names`, returns each collection's full description
        including its schema.
        """
        if names:
            response = self.client.command("DescribeCollections", parameters={"names": names})
            operation = "DescribeCollections"
        else:
            response = self.client.command(
                "ListCollections",
                parameters=prepare_api_parameters({"limit": limit, "start": start}),
            )
            operation = "ListCollections"
        return handle_api_response(
            response, operation=operation, error_message="Failed to list Foundry collections"
        )

    def list_foundry_objects(
        self,
        collection_name: str = Field(description="Collection to list objects from."),
        limit: int = Field(default=100, ge=1, le=500, description="Max records. [1-500]"),
        start: str | None = Field(
            default=None, description="Pagination: start key from a previous page."
        ),
    ) -> dict[str, Any] | list[Any]:
        """List object keys in a Foundry custom-storage collection."""
        response = self.client.command(
            "ListObjects",
            collection_name=collection_name,
            parameters=prepare_api_parameters({"limit": limit, "start": start}),
        )
        return handle_api_response(
            response, operation="ListObjects", error_message="Failed to list Foundry objects"
        )

    def search_foundry_objects(
        self,
        collection_name: str = Field(description="Collection to search."),
        filter: str = Field(
            description=(
                "FQL filter over the collection's indexed fields, e.g. "
                "field:'value'. Which fields are searchable is defined by the "
                "collection's schema (see falcon_list_foundry_collections with names=)."
            ),
        ),
        limit: int = Field(default=100, ge=1, le=500, description="Max records. [1-500]"),
        offset: int | None = Field(default=None, description="Pagination offset."),
        sort: str | None = Field(default=None, description="Sort expression."),
    ) -> dict[str, Any] | list[Any]:
        """Search objects in a Foundry custom-storage collection by indexed field values."""
        response = self.client.command(
            "SearchObjects",
            collection_name=collection_name,
            parameters=prepare_api_parameters(
                {"filter": filter, "limit": limit, "offset": offset, "sort": sort}
            ),
        )
        return handle_api_response(
            response, operation="SearchObjects", error_message="Failed to search Foundry objects"
        )

    def get_foundry_object(
        self,
        collection_name: str = Field(description="Collection holding the object."),
        object_key: str = Field(description="Key of the object to fetch."),
    ) -> dict[str, Any] | list[Any]:
        """Fetch one object from a Foundry custom-storage collection by key."""
        response = self.client.command(
            "GetObject", collection_name=collection_name, object_key=object_key
        )
        # Object content comes back as the raw document (bytes) rather than a
        # JSON envelope on some content types.
        if isinstance(response, bytes):
            text = response.decode("utf-8", errors="replace")
            return {"collection_name": collection_name, "object_key": object_key, "data": text}
        return handle_api_response(
            response, operation="GetObject", error_message="Failed to get Foundry object"
        )

    def put_foundry_object(
        self,
        collection_name: str = Field(description="Collection to write into."),
        object_key: str = Field(description="Key to write the object under."),
        data: dict[str, Any] = Field(description="The object content, as a JSON object."),
        dry_run: bool = Field(
            default=False, description="Validate against the schema without writing."
        ),
    ) -> dict[str, Any] | list[Any]:
        """Create or replace an object in a Foundry custom-storage collection.

        Overwrites any existing object under the same key. Use dry_run=true to
        schema-validate the payload without writing.
        """
        response = self.client.command(
            "PutObject",
            collection_name=collection_name,
            object_key=object_key,
            body=data,
            parameters=prepare_api_parameters({"dry_run": dry_run}),
        )
        return handle_api_response(
            response, operation="PutObject", error_message="Failed to write Foundry object"
        )

    def delete_foundry_object(
        self,
        collection_name: str = Field(description="Collection holding the object."),
        object_key: str = Field(description="Key of the object to delete."),
        dry_run: bool = Field(
            default=False, description="Report what would be deleted without deleting."
        ),
    ) -> dict[str, Any] | list[Any]:
        """Delete an object from a Foundry custom-storage collection. This cannot be undone."""
        response = self.client.command(
            "DeleteObject",
            collection_name=collection_name,
            object_key=object_key,
            parameters=prepare_api_parameters({"dry_run": dry_run}),
        )
        return handle_api_response(
            response,
            operation="DeleteObject",
            error_message="Failed to delete Foundry object",
            default_result={"deleted": object_key},
        )

    # --- LogScale search ------------------------------------------------------

    def list_foundry_repos(self) -> dict[str, Any] | list[Any]:
        """List the Foundry/LogScale repositories and views available to search."""
        repos = self.client.command("ListReposV1")
        views = self.client.command("ListViewV1")
        repos_result = handle_api_response(
            repos, operation="ListReposV1", error_message="Failed to list Foundry repos"
        )
        if self._is_error(repos_result):
            return repos_result
        views_result = handle_api_response(
            views, operation="ListViewV1", error_message="Failed to list Foundry views"
        )
        return {
            "repos": repos_result,
            "views": views_result if not self._is_error(views_result) else [],
        }

    def run_foundry_search(
        self,
        query: str = Field(
            description="The LogScale (CQL) query string to execute."
        ),
        repo_or_view: str = Field(
            description=(
                "Repository or view to search (from falcon_list_foundry_repos)."
            ),
        ),
        start: str = Field(
            default="24h",
            description="Search window start, relative (e.g. 24h, 7d) or absolute.",
        ),
        end: str | None = Field(default=None, description="Search window end (optional)."),
        mode: str | None = Field(
            default=None,
            description="Execution mode: sync (wait for results) or async (job id).",
        ),
    ) -> dict[str, Any] | list[Any]:
        """Run an ad-hoc LogScale (CQL) query against a Foundry repository or view.

        Read-only: executes a search, changes nothing. In async mode the
        response carries a job id — fetch results with
        `falcon_get_foundry_search_results`. For CQL syntax the upstream
        `falcon://ngsiem/...` guides apply (`falcon-cli guides`).
        """
        body: dict[str, Any] = {
            "search_query": query,
            "repo_or_view": repo_or_view,
            "start": start,
        }
        if end:
            body["end"] = end
        response = self.client.command(
            "CreateSavedSearchesDynamicExecuteV1",
            body=body,
            parameters=prepare_api_parameters({"mode": mode}),
        )
        return handle_api_response(
            response,
            operation="CreateSavedSearchesDynamicExecuteV1",
            error_message="Failed to run Foundry search",
        )

    def get_foundry_search_results(
        self,
        job_id: str = Field(description="Job ID returned by falcon_run_foundry_search."),
        limit: int | None = Field(default=None, description="Max events to return."),
        offset: str | None = Field(default=None, description="Pagination offset."),
        job_status_only: bool = Field(
            default=False, description="Return only the job status, not events."
        ),
    ) -> dict[str, Any] | list[Any]:
        """Fetch the status/results of a Foundry LogScale search job."""
        response = self.client.command(
            "GetSavedSearchesExecuteV1",
            parameters=prepare_api_parameters(
                {
                    "job_id": job_id,
                    "limit": limit,
                    "offset": offset,
                    "job_status_only": job_status_only,
                }
            ),
        )
        return handle_api_response(
            response,
            operation="GetSavedSearchesExecuteV1",
            error_message="Failed to get Foundry search results",
        )

    # --- API integrations (plugins) -------------------------------------------

    def list_api_integrations(
        self,
        filter: str | None = Field(default=None, description="FQL filter over plugin configs."),
        limit: int = Field(default=100, ge=1, le=500, description="Max records. [1-500]"),
        offset: int | None = Field(default=None, description="Pagination offset."),
    ) -> dict[str, Any] | list[Any]:
        """List Foundry API-integration (plugin) configs and the operations each exposes.

        Each config describes a third-party API integration installed in the
        tenant and the named operations `falcon_execute_api_integration` can
        invoke on it.
        """
        response = self.client.command(
            "GetCombinedPluginConfigs",
            parameters=prepare_api_parameters(
                {"filter": filter, "limit": limit, "offset": offset}
            ),
        )
        return handle_api_response(
            response,
            operation="GetCombinedPluginConfigs",
            error_message="Failed to list API integrations",
        )

    def execute_api_integration(
        self,
        definition_id: str = Field(description="Plugin definition ID."),
        operation_id: str = Field(description="The plugin operation to invoke."),
        config_id: str | None = Field(
            default=None, description="Specific plugin config ID (when several exist)."
        ),
        request: dict[str, Any] | None = Field(
            default=None,
            description=(
                "Operation request: JSON object with the fields the operation "
                "expects (e.g. {'params': {...}, 'json': {...}})."
            ),
        ),
    ) -> dict[str, Any] | list[Any]:
        """Execute an operation on a Foundry API integration (plugin).

        This calls out to the third-party service behind the integration; the
        effect is whatever that operation does there, so treat it as
        irreversible unless you know the operation is a pure read.
        """
        resource: dict[str, Any] = {
            "definition_id": definition_id,
            "operation_id": operation_id,
        }
        if config_id:
            resource["id"] = config_id
        if request:
            resource["request"] = request
        response = self.client.command(
            "ExecuteCommand", body={"resources": [resource]}
        )
        return handle_api_response(
            response,
            operation="ExecuteCommand",
            error_message="Failed to execute API integration operation",
        )
