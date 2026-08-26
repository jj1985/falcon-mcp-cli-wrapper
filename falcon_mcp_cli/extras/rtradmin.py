"""Real Time Response admin content management (extra module).

Manages the reusable RTR content a response workflow draws on: custom scripts
and put-files (files stagable to hosts). This is the content-library side of
RTR — not live session command execution — so an automation engineer can
version the scripts and files their Fusion workflows and analysts invoke.

Upstream falcon-mcp's ``rtr`` module handles live sessions and command
execution; this module adds the admin content library, which upstream does not
cover.

Required API scopes:
    - Real Time Response Admin: Read  (list/get scripts and put-files)
    - Real Time Response Admin: Write (create/update/delete scripts and put-files)

Note: put-files and scripts here are content definitions. Actually staging a
file onto a host or running a script happens in an RTR session (upstream's
``rtr`` module / the RTR command tools), which is where the real, host-side
blast radius lives.
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

DESTRUCTIVE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


class RtradminModule(BaseModule):
    """RTR content library: custom scripts and put-files."""

    def register_tools(self, server: FastMCP) -> None:
        self._add_tool(server, self.list_rtr_scripts, "list_rtr_scripts")
        self._add_tool(server, self.get_rtr_scripts, "get_rtr_scripts")
        self._add_tool(
            server, self.create_rtr_script, "create_rtr_script", annotations=WRITE_ANNOTATIONS
        )
        self._add_tool(
            server, self.update_rtr_script, "update_rtr_script", annotations=WRITE_ANNOTATIONS
        )
        self._add_tool(
            server,
            self.delete_rtr_script,
            "delete_rtr_script",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        self._add_tool(server, self.list_rtr_put_files, "list_rtr_put_files")
        self._add_tool(server, self.get_rtr_put_files, "get_rtr_put_files")
        self._add_tool(
            server,
            self.create_rtr_put_file,
            "create_rtr_put_file",
            annotations=WRITE_ANNOTATIONS,
        )
        self._add_tool(
            server,
            self.delete_rtr_put_file,
            "delete_rtr_put_file",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )

    # --- scripts --------------------------------------------------------------

    def list_rtr_scripts(
        self,
        filter: str | None = Field(default=None, description="FQL filter over scripts."),
        limit: int = Field(default=50, ge=1, le=5000, description="Max IDs. [1-5000]"),
        offset: int | None = Field(default=None, description="Pagination offset."),
        sort: str | None = Field(default=None, description="Sort expression."),
    ) -> dict[str, Any] | list[Any]:
        """List custom RTR script IDs, filtered by FQL.

        Returns script IDs; pass them to `falcon_get_rtr_scripts` for details.
        """
        response = self.client.command(
            "RTR_ListScripts",
            parameters=prepare_api_parameters(
                {"filter": filter, "limit": limit, "offset": offset, "sort": sort}
            ),
        )
        return handle_api_response(
            response, operation="RTR_ListScripts", error_message="Failed to list RTR scripts"
        )

    def get_rtr_scripts(
        self,
        ids: list[str] = Field(description="Script IDs to retrieve."),
    ) -> dict[str, Any] | list[Any]:
        """Get details of custom RTR scripts by ID (metadata, platform, permission type)."""
        response = self.client.command("RTR_GetScriptsV2", parameters={"ids": ids})
        return handle_api_response(
            response, operation="RTR_GetScriptsV2", error_message="Failed to get RTR scripts"
        )

    def create_rtr_script(
        self,
        name: str = Field(description="Script name."),
        content: str = Field(
            description="The script source. From the shell: content=\"$(cat s.ps1)\"."
        ),
        description: str = Field(description="Script description (required by the API)."),
        permission_type: str = Field(
            default="group",
            description=(
                "Who may run it: 'private' (uploader only), 'group' (RTR "
                "admins), or 'public' (active responders and admins)."
            ),
        ),
        platform: str = Field(
            default="windows", description="Target platform: windows, mac, or linux."
        ),
    ) -> dict[str, Any] | list[Any]:
        """Create a custom RTR script in the content library.

        The script becomes runnable in RTR sessions (subject to permission_type)
        and callable from response workflows. Creating it does not run it — that
        happens in an RTR session.
        """
        response = self.client.command(
            "RTR_CreateScripts",
            data={
                "name": name,
                "content": content,
                "description": description,
                "permission_type": permission_type,
                "platform": platform,
            },
        )
        return handle_api_response(
            response, operation="RTR_CreateScripts", error_message="Failed to create RTR script"
        )

    def update_rtr_script(
        self,
        id: str = Field(description="Script ID to update."),
        content: str | None = Field(default=None, description="New script source, if changing."),
        name: str | None = Field(default=None, description="New name."),
        description: str | None = Field(default=None, description="New description."),
        permission_type: str | None = Field(default=None, description="New permission type."),
        platform: str | None = Field(default=None, description="New target platform."),
    ) -> dict[str, Any] | list[Any]:
        """Update an existing custom RTR script's content or metadata."""
        fields = {
            key: value
            for key, value in {
                "id": id,
                "content": content,
                "name": name,
                "description": description,
                "permission_type": permission_type,
                "platform": platform,
            }.items()
            if value is not None
        }
        response = self.client.command("RTR_UpdateScripts", data=fields)
        return handle_api_response(
            response, operation="RTR_UpdateScripts", error_message="Failed to update RTR script"
        )

    def delete_rtr_script(
        self,
        ids: str = Field(description="Script ID to delete."),
    ) -> dict[str, Any] | list[Any]:
        """Delete a custom RTR script from the content library. This cannot be undone."""
        response = self.client.command("RTR_DeleteScripts", parameters={"ids": ids})
        return handle_api_response(
            response,
            operation="RTR_DeleteScripts",
            error_message="Failed to delete RTR script",
            default_result={"deleted": ids},
        )

    # --- put-files ------------------------------------------------------------

    def list_rtr_put_files(
        self,
        filter: str | None = Field(default=None, description="FQL filter over put-files."),
        limit: int = Field(default=50, ge=1, le=5000, description="Max IDs. [1-5000]"),
        offset: int | None = Field(default=None, description="Pagination offset."),
        sort: str | None = Field(default=None, description="Sort expression."),
    ) -> dict[str, Any] | list[Any]:
        """List RTR put-file IDs (files stagable to hosts), filtered by FQL."""
        response = self.client.command(
            "RTR_ListPut_Files",
            parameters=prepare_api_parameters(
                {"filter": filter, "limit": limit, "offset": offset, "sort": sort}
            ),
        )
        return handle_api_response(
            response, operation="RTR_ListPut_Files", error_message="Failed to list RTR put-files"
        )

    def get_rtr_put_files(
        self,
        ids: list[str] = Field(description="Put-file IDs to retrieve."),
    ) -> dict[str, Any] | list[Any]:
        """Get details of RTR put-files by ID."""
        response = self.client.command("RTR_GetPut_FilesV2", parameters={"ids": ids})
        return handle_api_response(
            response,
            operation="RTR_GetPut_FilesV2",
            error_message="Failed to get RTR put-files",
        )

    def create_rtr_put_file(
        self,
        name: str = Field(description="Put-file name as hosts will see it."),
        content: str = Field(
            description="File content. From the shell: content=\"$(cat payload.txt)\"."
        ),
        description: str = Field(description="File description (required by the API)."),
        comments_for_audit_log: str | None = Field(
            default=None, description="Comment recorded in the audit log."
        ),
    ) -> dict[str, Any] | list[Any]:
        """Create an RTR put-file — a file that can be staged to hosts in RTR sessions.

        Uploading it to the library does not place it on any host; the `put`
        command in an RTR session does that.
        """
        fields = {"name": name, "description": description}
        if comments_for_audit_log:
            fields["comments_for_audit_log"] = comments_for_audit_log
        response = self.client.command(
            "RTR_CreatePut_Files",
            data=fields,
            files=[("file", (name, content.encode()))],
        )
        return handle_api_response(
            response,
            operation="RTR_CreatePut_Files",
            error_message="Failed to create RTR put-file",
        )

    def delete_rtr_put_file(
        self,
        ids: str = Field(description="Put-file ID to delete."),
    ) -> dict[str, Any] | list[Any]:
        """Delete an RTR put-file from the content library. This cannot be undone."""
        response = self.client.command("RTR_DeletePut_Files", parameters={"ids": ids})
        return handle_api_response(
            response,
            operation="RTR_DeletePut_Files",
            error_message="Failed to delete RTR put-file",
            default_result={"deleted": ids},
        )
