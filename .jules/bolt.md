# Bolt's Journal

## Observations
- `list_findings` runs `ORDER BY severity DESC, created_at DESC`. A composite index on `(severity DESC, created_at DESC)` in `findings` could help, but might not be under 50 lines if requiring schema migration.
- In `routes.py`, `operations()` fetches `workflow_run`, `child_runs`, and then does a nested loop `list_findings(conn, run_id=child.id)` to extend findings. This is an N+1 query pattern!

- N+1 query in `operations()`:
```python
        with get_connection(db_path) as conn:
            workflow_run = get_run(conn, run_id)
            if workflow_run:
                child_runs = list_runs(conn, parent_run_id=run_id)
                findings = list_findings(conn, run_id=run_id)
                # Also collect findings from child runs
                for child in child_runs:
                    findings.extend(list_findings(conn, run_id=child.id))
```
This is a classic N+1 query pattern. We can just use `run_ids=[run_id] + [c.id for c in child_runs]` and call `list_findings` once. This exactly aligns with "N+1 query patterns in route handlers or CLI commands" and "Repeated database connections". Wait, `list_findings` already supports `run_ids`!
