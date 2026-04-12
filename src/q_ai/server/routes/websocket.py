"""WebSocket endpoints — /ws broadcast channel and /ws/assist streaming."""

from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from q_ai.server.routes._shared import logger

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for live workflow event updates.

    Connects through the ConnectionManager for event broadcasting.
    """
    manager = websocket.app.state.ws_manager
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


async def _handle_assist_query(
    websocket: WebSocket,
    data: dict[str, Any],
    history: list[dict[str, str]],
) -> None:
    """Process a single assist_query message over WebSocket.

    Args:
        websocket: Active WebSocket connection.
        data: Parsed message dict with type, message, and context fields.
        history: Mutable conversation history (updated in place).
    """
    message = str(data.get("message", "")).strip()
    if not message:
        await websocket.send_json({"type": "assist_error", "message": "Empty message"})
        return

    context = data.get("context") or {}
    scan_context = str(context.get("findings", ""))
    source = str(context.get("source", ""))

    history.append({"role": "user", "content": message})

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
    Maintains per-connection conversation history (ephemeral).
    """
    await websocket.accept()
    history: list[dict[str, str]] = []

    try:
        while True:
            raw = await websocket.receive_text()
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
                await websocket.send_json({"type": "assist_reset_done"})
                continue

            if msg_type == "assist_query":
                await _handle_assist_query(websocket, data, history)

    except WebSocketDisconnect:
        pass
