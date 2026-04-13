"""Client integration helpers for Claude Code and Codex."""

from .manager import (
    SUPPORTED_CLIENTS,
    ClientStatus,
    inspect_clients,
    install_clients,
    remove_clients,
    resolve_requested_clients,
)

__all__ = [
    "SUPPORTED_CLIENTS",
    "ClientStatus",
    "install_clients",
    "inspect_clients",
    "remove_clients",
    "resolve_requested_clients",
]
