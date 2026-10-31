"""Client review handlers for VK bot."""

import json
import logging
from typing import Any

from vkbottle import Callback, GroupEventType, Keyboard
from vkbottle.bot import BotLabeler, Message, MessageEvent
from handlers.ui import edit_or_send as _edit_or_send

from services.reviews import (
    can_review_platform_appointment,
    create_review_from_platform,
    get_reviewable_for_platform,
    get_reviews_for_platform,
)

logger = logging.getLogger(__name__)
labeler = BotLabeler()

# State for client awaiting review text input: vk_user_id -> {"appointment_id": int, "rating": int}
user_pending_review: dict[int, dict[str, Any]] = {}


def get_payload(event: MessageEvent) -> dict[str, Any]:
    p = event.payload
    if isinstance(p, dict):
        return p
    if isinstance(p, str):
        try:
            return json.loads(p)
        except Exception:
            return {}
    return {}


def get_rating_keyboard(appointment_id: int) -> str:
    kb = Keyboard(inline=True)
    for n in range(1, 6):
        kb.add(Callback(f"⭐ {n}", {"cmd": "review_rate", "id": appointment_id, "rating": n}))
    kb.row()
    kb.add(Callback("❌ Отмена", {"cmd": "review_cancel"}))
    return kb.get_json()


def get_cancel_keyboard() -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("❌ Отмена", {"cmd": "review_cancel"}))
    return kb.get_json()


@labeler.message(text=["⭐ Мои отзывы", "Мои отзывы", "мои отзывы", "/reviews"])
async def show_my_vk_reviews(message: Message):
    user_id = message.from_id
    reviews = get_reviews_for_platform("vk", user_id)
    reviewable = get_reviewable_for_platform("vk", user_id)

    lines = ["⭐ Мои отзывы\n"]

    if not reviews and not reviewable:
        lines.append(
            "У тебя пока нет оставленных отзывов.\n\n"
            "После того как визит состоится и мастер подтвердит оплату, "
            "здесь появится возможность оценить стрижку и сервис! 💈"
        )
        await message.answer("\n".join(lines))
        return

    kb = None
    if reviewable:
        lines.append("✂️ Визиты, ожидающие вашего отзыва:")
        vk_kb = Keyboard(inline=True)
        for v in reviewable[:3]:
            svc = v.get("services") or "Визит"
            time_str = str(v.get("appointment_time") or "")[:10]
            lines.append(f"• {svc} ({time_str})")
            vk_kb.add(Callback(f"✍️ Отзыв ({svc[:15]})", {"cmd": "review_start", "id": v["id"]}))
            vk_kb.row()
        kb = vk_kb.get_json()
        lines.append("")

    if reviews:
        lines.append("📋 Ваша история отзывов:\n")
        for r in reviews[:5]:
            stars = "⭐" * int(r.get("rating") or 5)
            created = str(r.get("created_at") or "")[:10]
            comment = str(r.get("comment") or "")
            svc = str(r.get("services") or "Стрижка")
            lines.append(f"{stars} ({r.get('rating')}/5) · {created}\n✂️ {svc}\n«{comment}»\n")

    await message.answer("\n".join(lines), keyboard=kb)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "review_start",
)
async def callback_review_start(event: MessageEvent):
    payload = get_payload(event)
    appt_id = payload.get("id")
    if not appt_id:
        return

    can, msg, appt = can_review_platform_appointment("vk", event.user_id, int(appt_id))
    if not can:
        await event.show_snackbar(msg or "Отзыв за этот визит недоступен.")
        return

    services = (appt.get("services") or "Стрижка") if appt else "Визит"
    text = (
        "✂️ Оставить отзыв о визите\n\n"
        f"Услуга: {services}\n\n"
        "Пожалуйста, выбери оценку от 1 до 5 звёзд:"
    )
    await _edit_or_send(event, message=text, keyboard=get_rating_keyboard(int(appt_id)))


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "review_rate",
)
async def callback_review_rate(event: MessageEvent):
    payload = get_payload(event)
    appt_id = payload.get("id")
    rating = payload.get("rating")
    if not appt_id or not rating:
        return

    appt_id = int(appt_id)
    rating = int(rating)

    can, msg, _ = can_review_platform_appointment("vk", event.user_id, appt_id)
    if not can:
        await event.show_snackbar(msg or "Отзыв за этот визит недоступен.")
        return

    user_pending_review[event.user_id] = {"appointment_id": appt_id, "rating": rating}

    stars_str = "⭐" * rating
    text = (
        f"Ты выбрал оценку: {rating} из 5 ({stars_str})\n\n"
        "✍️ Напиши, пожалуйста, текстовый отзыв:\n"
        "(от 10 до 500 символов — что понравилось, качество стрижки, сервис и атмосфера)\n\n"
        "Для отмены напиши «отмена» или нажми кнопку ниже 👇"
    )
    await _edit_or_send(event, message=text, keyboard=get_cancel_keyboard())


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "review_cancel",
)
async def callback_review_cancel(event: MessageEvent):
    user_pending_review.pop(event.user_id, None)
    await _edit_or_send(
        event,
        message="Отзыв отменён. Ты сможешь оставить его в любое время в разделе «📋 Мои записи».",
    )


@labeler.message()
async def handle_vk_review_comment(message: Message):
    user_id = message.from_id
    pending = user_pending_review.get(user_id)
    if not pending:
        # Not in review mode, let next handler handle it
        return None

    text = (message.text or "").strip()
    if text.lower() in {"/cancel", "отмена", "отменить"}:
        user_pending_review.pop(user_id, None)
        await message.answer("Отзыв отменён. Ты сможешь оставить его в любое время в разделе «📋 Мои записи».")
        return

    appt_id = pending["appointment_id"]
    rating = pending["rating"]

    ok, err_or_msg, created = create_review_from_platform(
        platform="vk",
        platform_user_id=user_id,
        appointment_id=appt_id,
        rating=rating,
        comment=text,
    )

    if not ok:
        await message.answer(
            f"❌ {err_or_msg}\n\nПожалуйста, напиши отзыв ещё раз или отправь «отмена» для отмены:",
            keyboard=get_cancel_keyboard(),
        )
        return

    user_pending_review.pop(user_id, None)
    await message.answer(
        "🎉 Спасибо за твой отзыв!\n\n"
        "Он успешно сохранён и опубликован на нашем сайте srezforma.ru в разделе «Отзывы».\n\n"
        "Будем рады видеть тебя снова! 💈"
    )
