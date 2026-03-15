"""SQLite CRUD operations for IPI campaigns and hits.

All data is persisted to the unified q-ai database via q_ai.core.db.get_connection().
Table names are ipi_payloads (campaigns) and ipi_hits, both created in schema V6.

Typical usage:
    >>> from q_ai.ipi.db import save_campaign, get_campaign
    >>> save_campaign(campaign, db_path=db_path)
    >>> retrieved = get_campaign(campaign.uuid, db_path=db_path)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.ipi.models import Campaign, Hit, HitConfidence


def init_db(db_path: Path | None = None) -> None:
    """No-op: schema migration is handled by get_connection().

    Args:
        db_path: Unused. Kept for API symmetry with other modules.
    """


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_campaign(row: sqlite3.Row) -> Campaign:
    """Convert a sqlite3.Row from ipi_payloads to a Campaign instance.

    Args:
        row: A row from ipi_payloads with sqlite3.Row access.

    Returns:
        Campaign instance with proper Python types.
    """
    created_at_raw: str = row["created_at"]
    return Campaign(
        id=row["id"],
        uuid=row["uuid"],
        token=row["token"] or "",
        filename=row["filename"] or "",
        format=row["format"] or "pdf",
        technique=row["technique"],
        callback_url=row["callback_url"] or "",
        output_path=row["output_path"],
        payload_style=row["payload_style"] or "obvious",
        payload_type=row["payload_type"] or "callback",
        run_id=row["run_id"],
        created_at=datetime.fromisoformat(created_at_raw),
    )


def _row_to_hit(row: sqlite3.Row) -> Hit:
    """Convert a sqlite3.Row from ipi_hits to a Hit instance.

    Args:
        row: A row from ipi_hits with sqlite3.Row access.

    Returns:
        Hit instance with proper Python types.
    """
    timestamp_raw: str = row["timestamp"]
    return Hit(
        id=row["id"],
        uuid=row["uuid"],
        source_ip=row["source_ip"] or "",
        user_agent=row["user_agent"] or "",
        headers=row["headers"] or "{}",
        body=row["body"],
        token_valid=bool(row["token_valid"]),
        confidence=HitConfidence(row["confidence"]),
        timestamp=datetime.fromisoformat(timestamp_raw),
    )


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------


def save_campaign(campaign: Campaign, db_path: Path | None = None) -> None:
    """Insert a campaign into ipi_payloads.

    Args:
        campaign: Campaign instance to persist.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Raises:
        sqlite3.IntegrityError: If a campaign with the same id already exists.
        sqlite3.Error: On other database failures.
    """
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ipi_payloads (
                id, run_id, uuid, token, filename, output_path,
                format, technique, payload_style, payload_type,
                callback_url, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign.id,
                campaign.run_id,
                campaign.uuid,
                campaign.token,
                campaign.filename,
                campaign.output_path,
                campaign.format,
                campaign.technique,
                campaign.payload_style,
                campaign.payload_type,
                campaign.callback_url,
                campaign.created_at.isoformat(),
            ),
        )


def get_campaign(uuid: str, db_path: Path | None = None) -> Campaign | None:
    """Retrieve a campaign by its UUID.

    Args:
        uuid: The unique identifier of the campaign to retrieve.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        Campaign instance if found, None otherwise.

    Raises:
        sqlite3.Error: On database failures.
    """
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM ipi_payloads WHERE uuid = ?", (uuid,)).fetchone()
        if row:
            return _row_to_campaign(row)
        return None


def get_campaign_by_token(uuid: str, token: str, db_path: Path | None = None) -> Campaign | None:
    """Retrieve a campaign by UUID and validate its authentication token.

    Returns the campaign only if both UUID and token match.

    Args:
        uuid: The unique identifier of the campaign.
        token: The authentication token to validate.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        Campaign instance if UUID exists and token matches, None otherwise.

    Raises:
        sqlite3.Error: On database failures.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ipi_payloads WHERE uuid = ? AND token = ?", (uuid, token)
        ).fetchone()
        if row:
            return _row_to_campaign(row)
        return None


def get_all_campaigns(db_path: Path | None = None) -> list[Campaign]:
    """Retrieve all campaigns ordered by created_at descending (newest first).

    Args:
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        List of Campaign instances, newest first. Empty list if none exist.

    Raises:
        sqlite3.Error: On database failures.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM ipi_payloads ORDER BY created_at DESC").fetchall()
        return [_row_to_campaign(row) for row in rows]


# ---------------------------------------------------------------------------
# Hit CRUD
# ---------------------------------------------------------------------------


def save_hit(hit: Hit, db_path: Path | None = None) -> None:
    """Insert a callback hit into ipi_hits.

    Args:
        hit: Hit instance to persist. hit.headers must be a JSON string.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Raises:
        sqlite3.Error: On database failures.
    """
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ipi_hits (
                id, uuid, source_ip, user_agent, headers, body,
                token_valid, confidence, timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hit.id,
                hit.uuid,
                hit.source_ip,
                hit.user_agent,
                hit.headers,
                hit.body,
                1 if hit.token_valid else 0,
                hit.confidence.value,
                hit.timestamp.isoformat(),
            ),
        )


def get_hits(uuid: str | None = None, db_path: Path | None = None) -> list[Hit]:
    """Retrieve callback hits, optionally filtered by campaign UUID.

    Args:
        uuid: If provided, only return hits for this campaign UUID.
            If None, return all hits.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        List of Hit instances ordered by timestamp descending (newest first).
        Empty list if no hits found.

    Raises:
        sqlite3.Error: On database failures.
    """
    with get_connection(db_path) as conn:
        if uuid is not None:
            rows = conn.execute(
                "SELECT * FROM ipi_hits WHERE uuid = ? ORDER BY timestamp DESC", (uuid,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM ipi_hits ORDER BY timestamp DESC").fetchall()
        return [_row_to_hit(row) for row in rows]


# ---------------------------------------------------------------------------
# reset_db
# ---------------------------------------------------------------------------


def reset_db(db_path: Path | None = None) -> tuple[int, int, int]:
    """Delete all campaigns, hits, and generated payload files.

    Reads output_path from ipi_payloads, deletes matching files from disk,
    then clears both tables. Schema is preserved.

    Args:
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        Tuple of (campaigns_deleted, hits_deleted, files_deleted).

    Raises:
        sqlite3.Error: On database failures.
    """
    files_deleted = 0
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT output_path FROM ipi_payloads WHERE output_path IS NOT NULL"
        ).fetchall()
        for row in rows:
            file_path = Path(row["output_path"])
            if file_path.is_file():
                file_path.unlink()
                files_deleted += 1

        hits_deleted: int = conn.execute("DELETE FROM ipi_hits").rowcount
        campaigns_deleted: int = conn.execute("DELETE FROM ipi_payloads").rowcount

    return campaigns_deleted, hits_deleted, files_deleted


__all__ = [
    "get_all_campaigns",
    "get_campaign",
    "get_campaign_by_token",
    "get_hits",
    "init_db",
    "reset_db",
    "save_campaign",
    "save_hit",
]
