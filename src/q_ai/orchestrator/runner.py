"""Core workflow runner for q-ai orchestration.

Manages the parent workflow run, child run creation, status transitions,
event broadcasting, and human-in-the-loop waiting.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.core.db import (
    create_run,
    get_connection,
    get_run,
    get_target,
    update_run_status,
)
from q_ai.core.models import RunStatus, Target

if TYPE_CHECKING:
    from q_ai.server.websocket import ConnectionManager


class WorkflowRunner:
    """Orchestrates a multi-module workflow run.

    Provides shared primitives for parent/child run management, status
    transitions, WebSocket event emission, and human-in-the-loop pausing.

    The runner is usable without a WebSocket manager (events are silently
    dropped) and without an active_workflows dict (registration is skipped),
    making it suitable for CLI or test usage.
    """

    def __init__(  # noqa: PLR0913 — runner wires together independent runtime refs; a config object would obscure intent
        self,
        workflow_id: str,
        config: dict[str, Any],
        ws_manager: ConnectionManager | None = None,
        active_workflows: dict[str, WorkflowRunner] | None = None,
        db_path: Path | None = None,
        source: str | None = None,
        app_state: Any = None,
    ) -> None:
        """Initialize the workflow runner.

        Args:
            workflow_id: ID of the workflow being executed.
            config: Configuration dict for this workflow run.
            ws_manager: Optional WebSocket connection manager for event
                broadcasting. When None, events are silently dropped.
            active_workflows: Optional shared dict for active runner tracking.
                When provided, start() registers self and complete()/fail()
                deregister. When None, registration is skipped.
            db_path: Optional database path override.
            source: Optional provenance tag (e.g. "web", "cli").
            app_state: Optional reference to the FastAPI ``app.state``
                namespace so workflow executors can reach cross-cutting
                runtime state (managed-listener registry, ``~/.qai``
                override). ``None`` for CLI / unit-test usage.
        """
        self._workflow_id = workflow_id
        self._config = config
        self._ws_manager = ws_manager
        self._active_workflows = active_workflows
        self._db_path = db_path
        self._source = source
        self._app_state = app_state
        self._run_id = uuid.uuid4().hex
        self._wait_event = asyncio.Event()
        self._resume_data: dict[str, Any] | None = None

    @property
    def app_state(self) -> Any:
        """FastAPI ``app.state`` reference, or ``None`` outside web usage.

        Exposed so workflow executors can inspect the managed-listener
        registry or ``qai_dir`` without importing server internals.
        """
        return self._app_state

    @property
    def run_id(self) -> str:
        """The parent run ID for this workflow."""
        return self._run_id

    # --- Lifecycle ---

    async def start(self) -> str:
        """Create parent workflow run in DB, set status RUNNING, emit event.

        Sources ``target_id`` from ``config['target_id']`` when present so
        parent workflow rows are born with their target binding set on the
        column. Rows created without a ``target_id`` in config fall through
        with NULL and are still caught by the ``_migrate_unbound_runs``
        lifespan safety net.

        Returns:
            The parent run_id.
        """
        target_id = self._config.get("target_id") if isinstance(self._config, dict) else None
        with get_connection(self._db_path) as conn:
            create_run(
                conn,
                module="workflow",
                name=self._workflow_id,
                target_id=target_id,
                config=self._config,
                run_id=self._run_id,
                source=self._source,
            )
            update_run_status(conn, self._run_id, RunStatus.RUNNING)

        if self._active_workflows is not None:
            self._active_workflows[self._run_id] = self

        await self.emit(
            {
                "type": "run_status",
                "run_id": self._run_id,
                "status": int(RunStatus.RUNNING),
                "module": "workflow",
            }
        )
        return self._run_id

    async def complete(self, status: RunStatus = RunStatus.COMPLETED) -> None:
        """Set parent run to completed status with finished_at timestamp.

        Args:
            status: Terminal status to set. Defaults to COMPLETED.
        """
        with get_connection(self._db_path) as conn:
            update_run_status(conn, self._run_id, status)

        if self._active_workflows is not None:
            self._active_workflows.pop(self._run_id, None)

        await self.emit(
            {
                "type": "run_status",
                "run_id": self._run_id,
                "status": int(status),
                "module": "workflow",
            }
        )

    async def fail(self, error: str | None = None) -> None:
        """Set parent run to FAILED with optional error in config JSON.

        Args:
            error: Optional error message to store.
        """
        with get_connection(self._db_path) as conn:
            update_run_status(conn, self._run_id, RunStatus.FAILED)
            if error:
                conn.execute(
                    "UPDATE runs SET config = json_set(COALESCE(config, '{}'), '$.error', ?) "
                    "WHERE id = ?",
                    (error, self._run_id),
                )

        if self._active_workflows is not None:
            self._active_workflows.pop(self._run_id, None)

        await self.emit(
            {
                "type": "run_status",
                "run_id": self._run_id,
                "status": int(RunStatus.FAILED),
                "module": "workflow",
            }
        )

    # --- Child runs ---

    async def create_child_run(
        self,
        module: str,
        name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Create a child run linked to the parent.

        Args:
            module: Module name for the child run.
            name: Optional human-readable name.
            config: Optional configuration dict.

        Returns:
            The child run_id.
        """
        with get_connection(self._db_path) as conn:
            return create_run(
                conn,
                module=module,
                name=name,
                parent_run_id=self._run_id,
                config=config,
                source=self._source,
            )

    async def update_child_status(self, run_id: str, status: RunStatus) -> None:
        """Update a child run's status and emit run_status event.

        Args:
            run_id: ID of the child run to update.
            status: New status value.
        """
        with get_connection(self._db_path) as conn:
            update_run_status(conn, run_id, status)
            run = get_run(conn, run_id)
        module = run.module if run else "unknown"
        await self.emit(
            {
                "type": "run_status",
                "run_id": run_id,
                "status": int(status),
                "module": module,
            }
        )

    # --- Events ---

    async def emit(self, event: dict[str, Any]) -> None:
        """Broadcast a JSON event to all connected WebSocket clients.

        Silently does nothing when no WebSocket manager is configured.

        Args:
            event: JSON-serializable dict to broadcast.
        """
        if self._ws_manager is not None:
            await self._ws_manager.broadcast(event)

    async def emit_progress(self, run_id: str, message: str) -> None:
        """Emit a progress event.

        Args:
            run_id: The run this progress relates to.
            message: Progress message.
        """
        await self.emit({"type": "progress", "run_id": run_id, "message": message})

    async def emit_finding(
        self,
        finding_id: str,
        run_id: str,
        module: str,
        severity: int,
        title: str,
    ) -> None:
        """Emit a finding event.

        Args:
            finding_id: ID of the new finding.
            run_id: The run that produced the finding.
            module: Module that produced the finding.
            severity: Severity level as int.
            title: Finding title.
        """
        await self.emit(
            {
                "type": "finding",
                "finding_id": finding_id,
                "run_id": run_id,
                "module": module,
                "severity": severity,
                "title": title,
            }
        )

    # --- Human-in-the-loop ---

    async def wait_for_user(self, message: str) -> dict[str, Any]:
        """Pause the workflow for human action.

        Sets the parent run status to WAITING_FOR_USER, emits a waiting event,
        and blocks until resume() is called.

        Note: This does not survive server restart. If the server dies while
        waiting, the run stays in WAITING_FOR_USER and the user must re-run.

        Args:
            message: Instructions for the user.

        Returns:
            Data submitted by the user on resume, or empty dict.
        """
        self._wait_event.clear()
        self._resume_data = None

        with get_connection(self._db_path) as conn:
            update_run_status(conn, self._run_id, RunStatus.WAITING_FOR_USER)

        await self.emit({"type": "waiting", "run_id": self._run_id, "message": message})

        await self._wait_event.wait()
        return self._resume_data or {}

    async def resume(self, data: dict[str, Any] | None = None) -> None:
        """Resume a waiting workflow.

        Idempotent — if the run is not in WAITING_FOR_USER, this is a no-op.

        Args:
            data: Optional data to pass back to the waiting workflow function.
        """
        with get_connection(self._db_path) as conn:
            run = get_run(conn, self._run_id)
            if run is None or run.status != RunStatus.WAITING_FOR_USER:
                return
            update_run_status(conn, self._run_id, RunStatus.RUNNING)

        await self.emit({"type": "resumed", "run_id": self._run_id})

        self._resume_data = data
        self._wait_event.set()

    def unblock(self) -> None:
        """Unblock the wait event so the adapter coroutine exits cleanly.

        Called by the conclude endpoint to release a workflow that is blocked
        on wait_for_user() without going through the normal resume() path.
        """
        self._wait_event.set()

    # --- Target resolution ---

    async def resolve_target(self, target_id: str) -> Target:
        """Load a Target from the DB by ID.

        Args:
            target_id: ID of the target to load.

        Returns:
            The Target instance.

        Raises:
            ValueError: If the target is not found.
        """
        with get_connection(self._db_path) as conn:
            target = get_target(conn, target_id)
        if target is None:
            raise ValueError(f"Target not found: {target_id}")
        return target
