"""Client-facing "My appointments" section (VK)."""

import asyncio
import datetime
import logging
import secrets

from vkbottle import GroupEventType, Keyboard, OpenLink
from vkbottle.bot import BotLabeler, Message, MessageEvent

from config import ADMIN_IDS, SALON_ADDRESS
from handlers.booking import get_payload
from services.appointments import (
    SARATOV_TZ,
    get_user_upcoming_appointments,
    reschedule_appointment,
)
from services.booking import get_schedule
from handlers.ui import edit_or_send as _edit_or_send

logger = logging.getLogger(__name__)
labeler = BotLabeler()

# telegram-style state: vk user id -> appointment id pending reschedule
user_resched_appt: dict[int, int] = {}


def _fmt_dt(dt_val) -> str:
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.datetime.fromisoformat(dt_val)
        except Exception:
            return dt_val
    if dt_val.tzinfo is None:
        dt_val = dt_val.replace(tzinfo=SARATOV_TZ)
    return dt_val.astimezone(SARATOV_TZ).strftime("%d.%m.%Y в %H:%M")


def _my_appts_keyboard(appts: list[dict]) -> str:
    kb = Keyboard(inline=True)
    for a in appts:
        kb.add(Callback(f"✏️ Перенести ({_fmt_dt(a['appointment_time'])})"[:40], {"cmd": "my_resched", "id": a["id"]}))
        kb.row()
        kb.add(Callback("❌ Отменить запись", {"cmd": "appt_cancel", "id": a["id"]}))
        kb.row()
    if not appts:
        kb.add(Callback("✂️ Записаться", {"cmd": "book_services"}))
    return kb.get_json()


async def _show_my_appointments(message: Message, user_id: int):
    appts = await asyncio.to_thread(get_user_upcoming_appointments, "vk", user_id)
    if not appts:
        text = (
            "📋 У вас нет активных записей.\n\n"
            "Запишись на удобное время — и запись появится здесь."
        )
    else:
        blocks = []
        for a in appts:
            blocks.append(
                f"#{a['id']}. 📅 {_fmt_dt(a['appointment_time'])}\n"
                f"✂️ {a.get('services') or 'Стрижка'} — {a.get('total_price') or 0} ₽\n"
                f"📍 {SALON_ADDRESS}"
            )
        text = "📋 Твои записи:\n\n" + "\n\n".join(blocks)

    await message.answer(text, keyboard=_my_appts_keyboard(appts))


@labeler.message(
    text=["📋 Мои записи", "Мои записи", "мои записи", "/my", "/appointments"]
)
async def my_appointments(message: Message):
    await _show_my_appointments(message, message.from_id)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "my_list",
)
async def my_appointments_cb(event: MessageEvent):
    payload = get_payload(event)
    user_id = event.user_id
    appts = await asyncio.to_thread(get_user_upcoming_appointments, "vk", user_id)
    if not appts:
        text = "📋 У тебя нет активных записей.\n\nЗапишись на удобное время — и запись появится здесь."
    else:
        blocks = []
        for a in appts:
            blocks.append(
                f"#{a['id']}. 📅 {_fmt_dt(a['appointment_time'])}\n"
                f"✂️ {a.get('services') or 'Стрижка'} — {a.get('total_price') or 0} ₽\n"
                f"📍 {SALON_ADDRESS}"
            )
        text = "📋 Твои записи:\n\n" + "\n\n".join(blocks)

    try:
        await _edit_or_send(event, message=text, keyboard=_my_appts_keyboard(appts))
    except Exception:
        pass
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "my_resched",
)
async def my_resched_start(event: MessageEvent):
    payload = get_payload(event)
    user_id = event.user_id
    appt_id = int(payload.get("id"))
    user_resched_appt[user_id] = appt_id

    schedule = await asyncio.to_thread(get_schedule, {"hair"})
    kb = Keyboard(inline=True)
    day_items = list(schedule.values())[:8]
    for i in range(0, len(day_items), 2):
        for d in day_items[i : i + 2]:
            kb.add(Callback(f"📅 {d['title']}"[:40], {"cmd": "my_resched_day", "day": d["day_key"]}))
        kb.row()
    kb.add(Callback("◀️ Мои записи", {"cmd": "my_list"}))

    try:
        await _edit_or_send(event, 
            message="🗓 Перенос записи\n\nВыбери новый день:",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "my_resched_day",
)
async def my_resched_day(event: MessageEvent):
    payload = get_payload(event)
    day_key = payload.get("day")

    schedule = await asyncio.to_thread(get_schedule, {"hair"})
    day_data = schedule.get(day_key)
    slots = day_data["slots"] if day_data else []
    title = day_data["title"] if day_data else day_key

    kb = Keyboard(inline=True)
    for i in range(0, len(slots), 3):
        for s in slots[i : i + 3]:
            kb.add(Callback(f"🕒 {s['time']}", {"cmd": "my_resched_slot", "ts": s["timestamp"]}))
        kb.row()
    kb.add(Callback("◀️ Другой день", {"cmd": "my_resched", "id": user_resched_appt.get(event.user_id, 0)}))

    try:
        await _edit_or_send(event, 
            message=f"🕒 Свободные окна на {title}:\n\nВыбери новое время:",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "my_resched_slot",
)
async def my_resched_slot(event: MessageEvent):
    payload = get_payload(event)
    user_id = event.user_id
    appt_id = user_resched_appt.get(user_id)
    if not appt_id:
        await event.show_snackbar("Сессия переноса устарела, начни заново.")
        await event.send_empty_answer()
        return

    ts = int(payload.get("ts"))
    new_dt = datetime.datetime.fromtimestamp(ts, tz=SARATOV_TZ)

    ok, msg, appt = await asyncio.to_thread(
        lambda: reschedule_appointment(appt_id, new_dt, platform="vk", platform_user_id=user_id)
    )
    user_resched_appt.pop(user_id, None)

    if not ok:
        try:
            await _edit_or_send(event, message=f"⚠️ {msg}")
        except Exception:
            pass
        await event.send_empty_answer()
        return

    text = (
        "✅ Запись перенесена!\n\n"
        f"📅 {_fmt_dt(appt['appointment_time'])}\n"
        f"✂️ {appt.get('services') or 'Стрижка'} — {appt.get('total_price') or 0} ₽\n"
        f"📍 {SALON_ADDRESS}\n\n"
        "Напомним накануне вечером и за 2 часа до визита."
    )
    try:
        await _edit_or_send(event, message=text, keyboard=_my_appts_keyboard([appt]))
    except Exception:
        pass
    await event.send_empty_answer()

    for admin_id in ADMIN_IDS:
        try:
            await event.ctx_api.messages.send(
                user_id=admin_id,
                random_id=secrets.randbits(31),
                message=(
                    "🗓 Клиент перенёс запись (ВКонтакте)!\n\n"
                    f"👤 Клиент: {appt.get('client_name') or 'Клиент'}\n"
                    f"📱 Телефон: {appt.get('phone') or '—'}\n"
                    f"✂️ Услуги: {appt.get('services') or ''}\n"
                    f"⏰ Новое время: {_fmt_dt(appt['appointment_time'])}"
                ),
            )
        except Exception as e:
            logger.warning("Failed to notify VK admin %s about reschedule: %s", admin_id, e)
