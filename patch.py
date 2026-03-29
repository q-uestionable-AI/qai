import re
from pathlib import Path

content = Path("src/q_ai/server/routes.py").read_text()

search = """        history_runs: list[_HistoryRow] = []
        for run in parent_runs:
            child_ids = run_service.get_child_run_ids(conn, run.id)
            all_ids = [run.id, *child_ids]
            finding_count = run_service.get_finding_count_for_runs(conn, all_ids)"""

replace = """        # Batch finding counts and report runs to fix N+1 queries (O(1) instead of O(N))
        run_ids = [r.id for r in parent_runs] + [r.id for r in import_runs]
        finding_counts = {}
        if run_ids:
            ph = ", ".join("?" for _ in run_ids)
            rows = conn.execute(
                f"SELECT COALESCE(r.parent_run_id, r.id) as pid, COUNT(f.id) as cnt "
                f"FROM findings f JOIN runs r ON f.run_id = r.id "
                f"WHERE COALESCE(r.parent_run_id, r.id) IN ({ph}) GROUP BY pid",
                run_ids,
            ).fetchall()
            finding_counts = {r["pid"]: r["cnt"] for r in rows}

        target_ids = list({
            r.target_id or (r.config or {}).get("target_id")
            for r in parent_runs
            if r.target_id or (r.config or {}).get("target_id")
        })
        report_runs = {}
        if target_ids:
            ph = ", ".join("?" for _ in target_ids)
            rows = conn.execute(
                f"SELECT target_id, id FROM runs "
                f"WHERE name = 'generate_report' AND target_id IN ({ph}) "
                f"AND status IN (?, ?) ORDER BY finished_at DESC",
                (*target_ids, int(RunStatus.COMPLETED), int(RunStatus.PARTIAL)),
            ).fetchall()
            for r in rows:
                if r["target_id"] not in report_runs:
                    report_runs[r["target_id"]] = r["id"]

        history_runs: list[_HistoryRow] = []
        for run in parent_runs:
            finding_count = finding_counts.get(run.id, 0)"""

if search in content:
    content = content.replace(search, replace)
    Path("src/q_ai/server/routes.py").write_text(content)
    print("Replaced 1")
else:
    print("Failed 1")

search2 = """            # Check for existing report run for this target
            report_run_id = None
            if eff_target_id:
                report_row = conn.execute(
                    \"\"\"SELECT id FROM runs
                       WHERE name = 'generate_report' AND target_id = ?
                       AND status IN (?, ?)
                       ORDER BY finished_at DESC LIMIT 1\"\"\",
                    (eff_target_id, int(RunStatus.COMPLETED), int(RunStatus.PARTIAL)),
                ).fetchone()
                if report_row:
                    report_run_id = report_row["id"]"""

replace2 = """            # Get pre-computed latest report run
            report_run_id = report_runs.get(eff_target_id)"""

if search2 in content:
    content = content.replace(search2, replace2)
    Path("src/q_ai/server/routes.py").write_text(content)
    print("Replaced 2")
else:
    print("Failed 2")

search3 = """        for run in import_runs:
            if workflow_filter:
                continue  # Import runs don't match workflow filters
            source_name = run.source or "Unknown"
            display_name = f"Import ({source_name.title()})"
            finding_count = run_service.get_finding_count_for_runs(conn, [run.id])"""

replace3 = """        for run in import_runs:
            if workflow_filter:
                continue  # Import runs don't match workflow filters
            source_name = run.source or "Unknown"
            display_name = f"Import ({source_name.title()})"
            finding_count = finding_counts.get(run.id, 0)"""

if search3 in content:
    content = content.replace(search3, replace3)
    Path("src/q_ai/server/routes.py").write_text(content)
    print("Replaced 3")
else:
    print("Failed 3")
