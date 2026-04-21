"""WebSocket endpoints — /ws broadcast channel and /ws/assist streaming."""

from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from q_ai.server.routes._shared import logger, reject_unless_local_origin

router = APIRouter()

_MAX_HISTORY = 1000
_MAX_FRAME_BYTES = 64 * 1024


def _cap_history(history: list[dict[str, str]], cap_flag: dict[str, bool]) -> None:
    """Trim ``history`` in place to ``_MAX_HISTORY`` messages (FIFO).

    ``cap_flag`` is a single-entry dict used to track whether we've
    already logged the cap-hit for this connection — this keeps the log
    to one line per session rather than one per overflow.
    """
    if len(history) <= _MAX_HISTORY:
        return
    overflow = len(history) - _MAX_HISTORY
    del history[:overflow]
    if not cap_flag.get("logged"):
        logger.debug(
            "Assist WebSocket history reached cap (%d); dropping oldest messages",
            _MAX_HISTORY,
        )
        cap_flag["logged"] = True


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for live workflow event updates.

    Connects through the ConnectionManager for event broadcasting. The
    channel is receive-only from the client side — any inbound frame is
    discarded. Oversize frames (>64 KB) close the connection since the
    client should never send anything that large on this endpoint.
    """
    if not await reject_unless_local_origin(websocket):
        return
    manager = websocket.app.state.ws_manager
    await manager.connect(websocket)
    try:
        while True:
            frame = await websocket.receive_text()
            frame_bytes = len(frame.encode("utf-8"))
            if frame_bytes > _MAX_FRAME_BYTES:
                logger.debug(
                    "Dropping oversize frame on /ws (%d bytes); closing connection",
                    frame_bytes,
                )
                await websocket.close(code=1009)
                return
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


async def _handle_assist_query(
    websocket: WebSocket,
    data: dict[str, Any],
    history: list[dict[str, str]],
    cap_flag: dict[str, bool],
) -> None:
    """Process a single assist_query message over WebSocket.

    Args:
        websocket: Active WebSocket connection.
        data: Parsed message dict with type, message, and context fields.
        history: Mutable conversation history (updated in place).
        cap_flag: Per-connection state for one-shot cap-hit logging.
    """
    message = str(data.get("message", "")).strip()
    if not message:
        await websocket.send_json({"type": "assist_error", "message": "Empty message"})
        return

    context = data.get("context") or {}
    scan_context = str(context.get("findings", ""))
    source = str(context.get("source", ""))

    history.append({"role": "user", "content": message})
    _cap_history(history, cap_flag)

    try:
        from q_ai.assist.service import (
            AssistantNotConfiguredError,
            chat_stream,
        )

        full_response = ""
        async for token in chat_stream(
            query=message,
            scan_context=scan_context,
            history=history[:-1],  # exclude current query (already in messages)
            source=source,
        ):
            full_response += token
            await websocket.send_json({"type": "assist_token", "token": token})

        history.append({"role": "assistant", "content": full_response})
        _cap_history(history, cap_flag)
        await websocket.send_json({"type": "assist_done"})

    except AssistantNotConfiguredError as exc:
        history.pop()
        await websocket.send_json({"type": "assist_error", "message": str(exc)})
    except Exception:
        history.pop()
        logger.exception("Assistant WebSocket error")
        await websocket.send_json(
            {"type": "assist_error", "message": "An unexpected error occurred."}
        )


@router.websocket("/ws/assist")
async def assist_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for conversational assistant streaming.

    Receives assist_query messages and streams back token-by-token responses.
    Maintains per-connection conversation history (ephemeral, capped at
    ``_MAX_HISTORY`` entries).
    """
    if not await reject_unless_local_origin(websocket):
        return
    await websocket.accept()
    history: list[dict[str, str]] = []
    cap_flag: dict[str, bool] = {"logged": False}

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:
                await websocket.send_json({"type": "assist_error", "message": "Message too large"})
                continue
            try:
                data = _json.loads(raw)
            except (ValueError, TypeError):
                await websocket.send_json({"type": "assist_error", "message": "Invalid JSON"})
                continue

            if not isinstance(data, dict):
                continue

            msg_type = data.get("type")

            if msg_type == "assist_reset":
                history.clear()
                cap_flag["logged"] = False
                await websocket.send_json({"type": "assist_reset_done"})
                continue

            if msg_type == "assist_query":
                await _handle_assist_query(websocket, data, history, cap_flag)

    except WebSocketDisconnect:
        pass
