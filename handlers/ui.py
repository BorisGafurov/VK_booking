"""Shared helpers for rendering screens on messages (VK)."""

import json
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


def _sanitize_vk_keyboard(keyboard: Any) -> Any:
    """
    Ensures inline keyboards never exceed the VK API limit of 6 rows.
    If an inline keyboard has > 6 rows, trims it to 6 rows (retaining the last navigation row).
    """
    if not keyboard:
        return keyboard
    try:
        is_str = isinstance(keyboard, str)
        kb_data = json.loads(keyboard) if is_str else keyboard
        if isinstance(kb_data, dict) and kb_data.get("inline"):
            buttons = kb_data.get("buttons", [])
            if len(buttons) > 6:
                logger.warning(
                    "VK inline keyboard exceeded 6 rows (%d rows), trimming to 6 to satisfy VK API limits",
                    len(buttons),
                )
                trimmed_buttons = buttons[:5] + [buttons[-1]]
                kb_data["buttons"] = trimmed_buttons
                if is_str:
                    return json.dumps(kb_data, ensure_ascii=False)
                return kb_data
    except Exception as e:
        logger.debug("Keyboard sanitization skipped: %s", e)
    return keyboard


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
    safe_keyboard = _sanitize_vk_keyboard(keyboard)

    try:
        await event.edit_message(message=message, keyboard=safe_keyboard)
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
            keyboard=safe_keyboard,
        )
    except Exception as e:
        logger.warning("Failed to send fallback screen in VK: %s", e)
