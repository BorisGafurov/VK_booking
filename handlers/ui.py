"""Shared helpers for rendering screens on messages (VK)."""

import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


def _event_peer_id(event: Any) -> int | None:
    for getter in (
        lambda: event.peer_id,
        lambda: event.object.peer_id,
        lambda: event.object.user_id,
        lambda: event.object.message.peer_id,
    ):
        try:
            val = getter()
            if val:
                return int(val)
        except Exception:
            continue
    return None


async def edit_or_send(event: Any, message: str, keyboard: Any = None) -> None:
    """
    Shows a screen on the message a button was pressed on.

    If editing fails (the message was deleted, is not editable by the community,
    or the API rejects the edit), the screen is sent as a fresh message instead
    so the button never appears dead.
    """
    try:
        await event.edit_message(message=message, keyboard=keyboard)
        return
    except Exception as e:
        logger.debug("VK edit_message failed, sending a new message instead: %s", e)

    peer_id = _event_peer_id(event)
    if not peer_id:
        logger.warning("VK fallback screen dropped: cannot resolve peer_id")
        return
    try:
        await event.ctx_api.messages.send(
            peer_id=peer_id,
            random_id=secrets.randbits(31),
            message=message,
            keyboard=keyboard,
        )
    except Exception as e:
        logger.warning("Failed to send fallback screen in VK: %s", e)
