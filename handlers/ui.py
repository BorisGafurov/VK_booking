"""Shared helpers for rendering screens on messages (VK)."""

import json
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


def _sanitize_vk_keyboard(keyboard: Any) -> Any:
    """
    Ensures inline keyboards never violate VK API constraints:
    1. Maximum 6 rows.
    2. Maximum 10 buttons total across all rows.
    3. Maximum 5 buttons per row.
    If an inline keyboard exceeds any limit, sanitizes it safely while preserving navigation buttons.
    """
    if not keyboard:
        return keyboard
    try:
        is_str = isinstance(keyboard, str)
        kb_data = json.loads(keyboard) if is_str else keyboard
        if isinstance(kb_data, dict) and kb_data.get("inline"):
            rows = kb_data.get("buttons", [])
            if not rows:
                return keyboard

            total_buttons = sum(len(r) for r in rows)
            needs_sanitization = (
                len(rows) > 6 
                or total_buttons > 10 
                or any(len(r) > 5 for r in rows)
            )

            if needs_sanitization:
                logger.warning(
                    "Sanitizing VK inline keyboard (had %d rows, %d buttons total to satisfy VK API limits)",
                    len(rows),
                    total_buttons,
                )
                nav_row = rows[-1] if len(rows) > 1 else None
                content_rows = rows[:-1] if nav_row else rows

                max_content_rows = 5 if nav_row else 6
                content_rows = content_rows[:max_content_rows]

                clean_nav = nav_row[:5] if nav_row else []
                max_content_buttons = max(0, 10 - len(clean_nav))

                final_rows = []
                curr_buttons = 0
                for r in content_rows:
                    allowed = max_content_buttons - curr_buttons
                    if allowed <= 0:
                        break
                    take = r[:5][:allowed]
                    if take:
                        final_rows.append(take)
                        curr_buttons += len(take)

                if clean_nav:
                    final_rows.append(clean_nav)

                kb_data["buttons"] = final_rows[:6]
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
