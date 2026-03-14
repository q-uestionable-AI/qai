"""Shared data models for q-ai core."""
from __future__ import annotations

import datetime
import enum
import json
from dataclasses import dataclass
from typing import Self


class Severity(enum.IntEnum):
    """Finding severity levels."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class RunStatus(enum.IntEnum):
    """Run execution status."""

    PENDING = 0
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3
    CANCELLED = 4


def _parse_dt(val: str | None) -> datetime.datetime | None:
    """Parse an ISO datetime string.

    Args:
        val: ISO-format datetime string or None.

    Returns:
        Parsed datetime or None for None/empty input.
    """
    if not val:
        return None
    return datetime.datetime.fromisoformat(val)


def _dump_dt(val: datetime.datetime | None) -> str | None:
    """Serialize a datetime to an ISO string.

    Args:
        val: Datetime to serialize or None.

    Returns:
        ISO-format string or None for None input.
    """
    if val is None:
        return None
    return val.isoformat()


def _parse_json(val: str | None) -> dict | None:
    """Parse a JSON string into a dict.

    Args:
        val: JSON string or None.

    Returns:
        Parsed dict or None for None/empty input.
    """
    if not val:
        return None
    return json.loads(val)


def _dump_json(val: dict | None) -> str | None:
    """Serialize a dict to a JSON string.

    Args:
        val: Dict to serialize or None.

    Returns:
        JSON string or None for None input.
    """
    if val is None:
        return None
    return json.dumps(val)


@dataclass
class Run:
    """A single execution run of a q-ai module.

    Attributes:
        id: Unique run identifier.
        module: Name of the module that owns this run.
        status: Current execution status.
        parent_run_id: Optional parent run for nested/chained runs.
        name: Optional human-readable name.
        target_id: Optional reference to the target being scanned.
        config: Optional configuration dict for the run.
        started_at: Optional timestamp when the run started.
        finished_at: Optional timestamp when the run finished.
    """

    id: str
    module: str
    status: RunStatus
    parent_run_id: str | None = None
    name: str | None = None
    target_id: str | None = None
    config: dict | None = None
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None

    def to_dict(self) -> dict:
        """Serialize the run to a dict suitable for database storage.

        Returns:
            Dict with status as int, config as JSON string, and datetimes as
            ISO strings.
        """
        return {
            "id": self.id,
            "module": self.module,
            "status": int(self.status),
            "parent_run_id": self.parent_run_id,
            "name": self.name,
            "target_id": self.target_id,
            "config": _dump_json(self.config),
            "started_at": _dump_dt(self.started_at),
            "finished_at": _dump_dt(self.finished_at),
        }

    @classmethod
    def from_row(cls, row: dict) -> Self:
        """Construct a Run from a database row dict.

        Args:
            row: Dict with raw database values.

        Returns:
            A Run instance with parsed fields.
        """
        return cls(
            id=row["id"],
            module=row["module"],
            status=RunStatus(row["status"]),
            parent_run_id=row.get("parent_run_id"),
            name=row.get("name"),
            target_id=row.get("target_id"),
            config=_parse_json(row.get("config")),
            started_at=_parse_dt(row.get("started_at")),
            finished_at=_parse_dt(row.get("finished_at")),
        )


@dataclass
class Target:
    """A scan target (e.g. an MCP server, endpoint, or file).

    Attributes:
        id: Unique target identifier.
        type: Target type (e.g. "server", "endpoint", "file").
        name: Human-readable name.
        uri: Optional URI or address.
        metadata: Optional metadata dict.
        created_at: Optional creation timestamp.
    """

    id: str
    type: str
    name: str
    uri: str | None = None
    metadata: dict | None = None
    created_at: datetime.datetime | None = None

    def to_dict(self) -> dict:
        """Serialize the target to a dict suitable for database storage.

        Returns:
            Dict with metadata as JSON string and datetime as ISO string.
        """
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "uri": self.uri,
            "metadata": _dump_json(self.metadata),
            "created_at": _dump_dt(self.created_at),
        }

    @classmethod
    def from_row(cls, row: dict) -> Self:
        """Construct a Target from a database row dict.

        Args:
            row: Dict with raw database values.

        Returns:
            A Target instance with parsed fields.
        """
        return cls(
            id=row["id"],
            type=row["type"],
            name=row["name"],
            uri=row.get("uri"),
            metadata=_parse_json(row.get("metadata")),
            created_at=_parse_dt(row.get("created_at")),
        )


@dataclass
class Finding:
    """A security finding produced by a module run.

    Attributes:
        id: Unique finding identifier.
        run_id: The run that produced this finding.
        module: Name of the module that produced the finding.
        category: Finding category (e.g. "command_injection").
        severity: Severity level.
        title: Short human-readable title.
        description: Optional detailed description.
        framework_ids: Optional mapping of framework names to identifiers.
        source_ref: Optional reference to the source (file, line, tool name).
        created_at: Optional creation timestamp.
    """

    id: str
    run_id: str
    module: str
    category: str
    severity: Severity
    title: str
    description: str | None = None
    framework_ids: dict | None = None
    source_ref: str | None = None
    created_at: datetime.datetime | None = None

    def to_dict(self) -> dict:
        """Serialize the finding to a dict suitable for database storage.

        Returns:
            Dict with severity as int, framework_ids as JSON string, and
            datetime as ISO string.
        """
        return {
            "id": self.id,
            "run_id": self.run_id,
            "module": self.module,
            "category": self.category,
            "severity": int(self.severity),
            "title": self.title,
            "description": self.description,
            "framework_ids": _dump_json(self.framework_ids),
            "source_ref": self.source_ref,
            "created_at": _dump_dt(self.created_at),
        }

    @classmethod
    def from_row(cls, row: dict) -> Self:
        """Construct a Finding from a database row dict.

        Args:
            row: Dict with raw database values.

        Returns:
            A Finding instance with parsed fields.
        """
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            module=row["module"],
            category=row["category"],
            severity=Severity(row["severity"]),
            title=row["title"],
            description=row.get("description"),
            framework_ids=_parse_json(row.get("framework_ids")),
            source_ref=row.get("source_ref"),
            created_at=_parse_dt(row.get("created_at")),
        )


@dataclass
class Evidence:
    """Evidence associated with a finding or run.

    Attributes:
        id: Unique evidence identifier.
        type: Evidence type (e.g. "request", "response", "file").
        storage: Storage mode — "inline" for content in DB, "file" for path reference.
        finding_id: Optional associated finding.
        run_id: Optional associated run.
        mime_type: Optional MIME type of the content.
        hash: Optional content hash for integrity verification.
        content: Optional inline content (used when storage is "inline").
        path: Optional file path (used when storage is "file").
        created_at: Optional creation timestamp.
    """

    id: str
    type: str
    storage: str = "inline"
    finding_id: str | None = None
    run_id: str | None = None
    mime_type: str | None = None
    hash: str | None = None
    content: str | None = None
    path: str | None = None
    created_at: datetime.datetime | None = None

    def to_dict(self) -> dict:
        """Serialize the evidence to a dict suitable for database storage.

        Returns:
            Dict with datetime as ISO string.
        """
        return {
            "id": self.id,
            "type": self.type,
            "storage": self.storage,
            "finding_id": self.finding_id,
            "run_id": self.run_id,
            "mime_type": self.mime_type,
            "hash": self.hash,
            "content": self.content,
            "path": self.path,
            "created_at": _dump_dt(self.created_at),
        }

    @classmethod
    def from_row(cls, row: dict) -> Self:
        """Construct an Evidence from a database row dict.

        Args:
            row: Dict with raw database values.

        Returns:
            An Evidence instance with parsed fields.
        """
        return cls(
            id=row["id"],
            type=row["type"],
            storage=row.get("storage", "inline"),
            finding_id=row.get("finding_id"),
            run_id=row.get("run_id"),
            mime_type=row.get("mime_type"),
            hash=row.get("hash"),
            content=row.get("content"),
            path=row.get("path"),
            created_at=_parse_dt(row.get("created_at")),
        )
