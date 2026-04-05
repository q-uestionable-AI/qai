# Bolt's Performance Journal

## Async Blocking DB Queries and File I/O
- Found that `async def` FastAPI routes run directly on the event loop.
- Blocking synchronous operations like `sqlite3` database queries (`get_connection().execute(...)`) and filesystem reads (`Path.read_text()` or `json.loads` on large files) will freeze the entire event loop, severely degrading concurrency.
- **Pattern:** Use `asyncio.to_thread()` to offload these blocking tasks to the thread pool.
- **Example Fix:** In `api_proxy_session_detail` (`src/q_ai/server/routes.py`), extracted the database fetch, file read, and JSON parsing into a `_sync_fetch_proxy_session_detail` helper, and awaited it via `asyncio.to_thread()`. This prevents the main loop from stalling while reading proxy traffic logs from disk.
