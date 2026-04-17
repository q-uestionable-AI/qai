"""Service layer for q-ai — shared data-access functions for UI, CLI, and API."""

from q_ai.services.managed_listener import (
    MANAGER_CLI,
    MANAGER_WEB_UI,
    ForeignListenerRecord,
    ListenerState,
    ManagedListenerConflictError,
    ManagedListenerHandle,
    ManagedListenerStartupError,
    ManagedListenerStuckStopError,
    detect_existing_listener,
    start_adopted_poller,
    start_managed_listener,
    stop_managed_listener,
)

__all__ = [
    "MANAGER_CLI",
    "MANAGER_WEB_UI",
    "ForeignListenerRecord",
    "ListenerState",
    "ManagedListenerConflictError",
    "ManagedListenerHandle",
    "ManagedListenerStartupError",
    "ManagedListenerStuckStopError",
    "detect_existing_listener",
    "start_adopted_poller",
    "start_managed_listener",
    "stop_managed_listener",
]
