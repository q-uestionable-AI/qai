"""SQLite CRUD operations for CXP campaigns and test results.

All data is persisted to the unified q-ai database via q_ai.core.db.get_connection().
Table name is cxp_test_results, created in schema V7. Campaigns are stored in the
core runs table with module="cxp".
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from q_ai.cxp.models import Campaign, TestResult


def init_db(db_path: Path | None = None) -> None:
    """No-op: schema migration is handled by get_connection().

    Args:
        db_path: Unused. Kept for API symmetry with other modules.
    """


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_result(row: sqlite3.Row) -> TestResult:
    """Convert a sqlite3.Row from cxp_test_results to a TestResult instance.

    Args:
        row: A row from cxp_test_results with sqlite3.Row access.

    Returns:
        TestResult instance with proper Python types.
    """
    captured = row["captured_files"]
    return TestResult(
        id=row["id"],
        campaign_id=row["campaign_id"],
        technique_id=row["technique_id"],
        assistant=row["assistant"],
        model=row["model"] or "",
        timestamp=datetime.fromisoformat(row["created_at"]),
        trigger_prompt=row["trigger_prompt"],
        capture_mode=row["capture_mode"],
        captured_files=json.loads(captured) if captured else [],
        raw_output=row["raw_output"],
        validation_result=row["validation_result"],
        validation_details=row["validation_details"] or "",
        notes=row["notes"] or "",
        rules_inserted=row["rules_inserted"] or "",
        format_id=row["format_id"] or "",
    )


# ---------------------------------------------------------------------------
# Campaign CRUD (backed by core runs table)
# ---------------------------------------------------------------------------


def create_campaign(
    conn: sqlite3.Connection,
    name: str,
    description: str = "",
) -> Campaign:
    """Create a new CXP campaign as a runs record.

    Args:
        conn: An open SQLite connection.
        name: Campaign name.
        description: Optional description.

    Returns:
        The created Campaign.
    """
    campaign_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """INSERT INTO runs (id, module, name, config, status, started_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (campaign_id, "cxp", name, description, 0, now),
    )
    return Campaign(
        id=campaign_id,
        name=name,
        created=datetime.fromisoformat(now),
        description=description,
    )


def list_campaigns(conn: sqlite3.Connection) -> list[Campaign]:
    """Return all CXP campaigns (runs with module='cxp'), newest first.

    Args:
        conn: An open SQLite connection.

    Returns:
        List of campaigns ordered by start time descending.
    """
    cursor = conn.execute(
        "SELECT id, name, config, started_at FROM runs WHERE module = 'cxp' "
        "ORDER BY started_at DESC"
    )
    campaigns: list[Campaign] = [
        Campaign(
            id=row["id"],
            name=row["name"] or "",
            created=datetime.fromisoformat(row["started_at"]),
            description=row["config"] or "",
        )
        for row in cursor.fetchall()
    ]
    return campaigns


def get_campaign(conn: sqlite3.Connection, campaign_id: str) -> Campaign | None:
    """Get a single CXP campaign by ID.

    Args:
        conn: An open SQLite connection.
        campaign_id: The campaign/run UUID.

    Returns:
        The Campaign, or None if not found.
    """
    row = conn.execute(
        "SELECT id, name, config, started_at FROM runs WHERE id = ? AND module = 'cxp'",
        (campaign_id,),
    ).fetchone()
    if row is None:
        return None
    return Campaign(
        id=row["id"],
        name=row["name"] or "",
        created=datetime.fromisoformat(row["started_at"]),
        description=row["config"] or "",
    )


# ---------------------------------------------------------------------------
# Test result CRUD
# ---------------------------------------------------------------------------


def record_result(
    conn: sqlite3.Connection,
    campaign_id: str,
    technique_id: str,
    assistant: str,
    trigger_prompt: str,
    raw_output: str,
    capture_mode: str,
    model: str = "",
    captured_files: list[str] | None = None,
    validation_result: str = "pending",
    validation_details: str = "",
    notes: str = "",
    rules_inserted: str = "",
    format_id: str = "",
) -> TestResult:
    """Record a test result into the cxp_test_results table.

    Args:
        conn: An open SQLite connection.
        campaign_id: The campaign/run this result belongs to.
        technique_id: Which technique was tested.
        assistant: Which assistant was tested.
        trigger_prompt: The prompt used to trigger the assistant.
        raw_output: Captured output text.
        capture_mode: "file" or "output".
        model: Underlying model name if known.
        captured_files: Paths to captured files (file mode).
        validation_result: Validation status (default "pending").
        validation_details: What the validator found.
        notes: Researcher observations.
        rules_inserted: Comma-separated rule IDs.
        format_id: Which format was used.

    Returns:
        The created TestResult.
    """
    result_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    files = captured_files or []
    conn.execute(
        """INSERT INTO cxp_test_results
           (id, run_id, campaign_id, technique_id, assistant, model,
            trigger_prompt, capture_mode, captured_files, raw_output,
            validation_result, validation_details, notes,
            rules_inserted, format_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result_id,
            campaign_id,
            campaign_id,
            technique_id,
            assistant,
            model,
            trigger_prompt,
            capture_mode,
            json.dumps(files),
            raw_output,
            validation_result,
            validation_details,
            notes,
            rules_inserted or None,
            format_id or None,
            now,
        ),
    )
    return TestResult(
        id=result_id,
        campaign_id=campaign_id,
        technique_id=technique_id,
        assistant=assistant,
        model=model,
        timestamp=datetime.fromisoformat(now),
        trigger_prompt=trigger_prompt,
        capture_mode=capture_mode,
        captured_files=files,
        raw_output=raw_output,
        validation_result=validation_result,
        validation_details=validation_details,
        notes=notes,
        rules_inserted=rules_inserted,
        format_id=format_id,
    )


def list_results(
    conn: sqlite3.Connection,
    campaign_id: str | None = None,
) -> list[TestResult]:
    """List CXP test results, optionally filtered by campaign.

    Args:
        conn: An open SQLite connection.
        campaign_id: Optional campaign ID to filter by.

    Returns:
        List of test results ordered by creation time descending.
    """
    if campaign_id:
        cursor = conn.execute(
            "SELECT * FROM cxp_test_results WHERE campaign_id = ? ORDER BY created_at DESC",
            (campaign_id,),
        )
    else:
        cursor = conn.execute("SELECT * FROM cxp_test_results ORDER BY created_at DESC")
    return [_row_to_result(row) for row in cursor.fetchall()]


def get_result(conn: sqlite3.Connection, result_id: str) -> TestResult | None:
    """Get a single CXP test result by ID.

    Args:
        conn: An open SQLite connection.
        result_id: The result UUID.

    Returns:
        The TestResult, or None if not found.
    """
    row = conn.execute("SELECT * FROM cxp_test_results WHERE id = ?", (result_id,)).fetchone()
    if row is None:
        return None
    return _row_to_result(row)


def update_validation(
    conn: sqlite3.Connection,
    result_id: str,
    validation_result: str,
    validation_details: str,
) -> None:
    """Update the validation fields of a stored test result.

    Args:
        conn: An open SQLite connection.
        result_id: The result UUID to update.
        validation_result: New validation result ("hit", "miss", "partial").
        validation_details: What the validator found.
    """
    conn.execute(
        "UPDATE cxp_test_results SET validation_result = ?, validation_details = ? WHERE id = ?",
        (validation_result, validation_details, result_id),
    )
