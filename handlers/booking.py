import asyncio
import datetime
import json
import logging
import secrets
from typing import Any

from vkbottle import Callback, GroupEventType, Keyboard, OpenLink
from vkbottle.bot import BotLabeler, Message, MessageEvent

from config import ADMIN_IDS, BOOKING_URL
from handlers.referrals import safe_event_answer


async def _resolve_vk_client_name(api, user_id: int) -> str:
    """Resolve real first and last name for VK user instead of using id123456."""
    try:
        from services.referrals import get_user_by_platform_id
        u = get_user_by_platform_id(user_id, platform="vk")
        if u and u.get("first_name"):
            name = f"{u['first_name']} {u.get('last_name') or ''}".strip()
            if name:
                return name
    except Exception:
        pass
    try:
        users = await api.users.get(user_ids=[user_id])
        if users:
            name = f"{users[0].first_name} {users[0].last_name}".strip()
            if name:
                return name
    except Exception:
        pass
    return "Клиент"
from services.appointments import (
    SARATOV_TZ,
    SlotUnavailableError,
    cancel_appointment,
    confirm_appointment_by_client,
    create_or_update_appointment,
)
from keyboards.booking import (
    get_booking_keyboard,
    get_days_keyboard,
    get_services_keyboard,
    get_slots_keyboard,
)
from services.booking import (
    calculate_services,
    get_schedule,
    user_selected_day,
    user_selected_services,
)
from services.referrals import get_user_phone, is_phone_number, link_user_phone, normalize_phone
from handlers.ui import edit_or_send as _edit_or_send

logger = logging.getLogger(__name__)
labeler = BotLabeler()
user_selected_slot: dict[int, int] = {}


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


def _format_services_text(selected_keys: list[str] | set[str]) -> str:
    calc = calculate_services(selected_keys)
    names_str = " + ".join(calc["names"])
    return (
        "✂️ Онлайн-запись к мастеру Борису Гафурову\n\n"
        "💡 Как выбрать услуги:\n"
        "• Нажми на услугу один раз — появится галочка ✅ (услуга выбрана)\n"
        "• Нажми ещё раз — галочка снимется ⚪️ (услуга удалена)\n"
        "Можно выбрать одну услугу или сразу обе.\n\n"
        f"📋 Выбрано: {names_str}\n"
        f"💰 Итого: {calc['total_price']} ₽ ({calc['duration_text']})\n\n"
        "После выбора нажми кнопку «📅 Выбрать время»:"
    )


@labeler.message(
    text=[
        "✂️ Записаться",
        "Записаться",
        "записаться",
        "/book",
        "/booking",
        "запись",
        "Запись",
    ]
)
async def booking_handler(message: Message):
    user_id = message.from_id
    user_selected_services[user_id] = {"hair"}

    await message.answer(
        _format_services_text(user_selected_services[user_id]),
        keyboard=get_services_keyboard(user_selected_services[user_id]),
    )


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_toggle",
)
async def callback_book_toggle(event: MessageEvent):
    user_id = event.user_id
    payload = get_payload(event)
    key = payload.get("key")

    current = user_selected_services.get(user_id, {"hair"})
    alert_msg = None
    if key in current:
        if len(current) > 1:
            current.remove(key)
        else:
            alert_msg = "Нужно выбрать хотя бы одну услугу"
    else:
        current.add(key)
    user_selected_services[user_id] = current

    try:
        await _edit_or_send(event, 
            message=_format_services_text(current),
            keyboard=get_services_keyboard(current),
        )
    except Exception as e:
        logger.warning("Error in callback_book_toggle: %s", e)

    if alert_msg:
        await event.show_snackbar(alert_msg)
    else:
        await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_services",
)
async def callback_book_services(event: MessageEvent):
    user_id = event.user_id
    current = user_selected_services.get(user_id, {"hair"})
    try:
        await _edit_or_send(event, 
            message=_format_services_text(current),
            keyboard=get_services_keyboard(current),
        )
    except Exception as e:
        logger.warning("Error in callback_book_services: %s", e)
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_days",
)
async def callback_book_days(event: MessageEvent):
    user_id = event.user_id
    current = user_selected_services.get(user_id, {"hair"})
    schedule = await asyncio.to_thread(get_schedule, current)

    if not schedule:
        try:
            await _edit_or_send(event, 
                message="⚠️ Не удалось загрузить расписание онлайн. Выберите удобное время на сайте:",
                keyboard=get_booking_keyboard(),
            )
        except Exception:
            pass
        await event.send_empty_answer()
        return

    calc = calculate_services(current)
    try:
        await _edit_or_send(event, 
            message=(
                f"📅 Выберите удобный день записи:\n\n"
                f"Услуги: {', '.join(calc['names'])}\n"
                f"Стоимость: {calc['total_price']} ₽ ({calc['duration_text']})"
            ),
            keyboard=get_days_keyboard(schedule),
        )
    except Exception as e:
        logger.warning("Error in callback_book_days: %s", e)
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_day",
)
async def callback_book_day(event: MessageEvent):
    user_id = event.user_id
    payload = get_payload(event)
    day_key = payload.get("day")
    user_selected_day[user_id] = day_key

    current = user_selected_services.get(user_id, {"hair"})
    schedule = await asyncio.to_thread(get_schedule, current)
    day_data = schedule.get(day_key)
    slots = day_data["slots"] if day_data else []

    title = day_data["title"] if day_data else day_key
    try:
        await _edit_or_send(event, 
            message=f"🕒 Свободные окна на {title}:\n\nВыберите время начала:",
            keyboard=get_slots_keyboard(slots),
        )
    except Exception as e:
        logger.warning("Error in callback_book_day: %s", e)
    await event.send_empty_answer()


async def _create_vk_booking_and_notify(
    api,
    user_id: int,
    phone: str,
    timestamp: int,
    selected_services: list[str] | set[str],
    client_name: str,
    target_event_or_msg,
) -> int | None:
    """Creates the booking in the shared schedule and shows the confirmation."""
    calc = calculate_services(selected_services)
    dt = datetime.datetime.fromtimestamp(timestamp, tz=SARATOV_TZ)

    try:
        appt_id = await asyncio.to_thread(
            create_or_update_appointment,
            phone=phone,
            appointment_time=dt,
            services=", ".join(calc["names"]),
            total_price=calc["total_price"],
            client_name=client_name,
            platform="vk",
            platform_user_id=user_id,
            duration_min=calc["duration_minutes"],
        )
    except SlotUnavailableError:
        text = (
            "⚠️ Это время уже занято.\n\n"
            "Пожалуйста, выберите другой слот или запишитесь через сайт «Срез»:"
        )
        if isinstance(target_event_or_msg, MessageEvent):
            try:
                await _edit_or_send(
                    target_event_or_msg,
                    message=text,
                    keyboard=get_booking_keyboard(),
                )
            except Exception:
                pass
            await target_event_or_msg.send_empty_answer()
        else:
            await target_event_or_msg.answer(text, keyboard=get_booking_keyboard())
        return None

    user_selected_slot.pop(user_id, None)

    cancel_kb = Keyboard(inline=True)
    cancel_kb.add(Callback("📋 Мои записи", {"cmd": "my_list"}))
    cancel_kb.row()
    cancel_kb.add(Callback("❌ Отменить запись", {"cmd": "appt_cancel", "id": appt_id}))

    text = (
        f"🎉 Запись успешно подтверждена!\n\n"
        f"✂️ Услуги: {', '.join(calc['names'])}\n"
        f"💰 Стоимость: {calc['total_price']} ₽ ({calc['duration_text']})\n"
        f"📅 Дата и время: {dt.strftime('%d.%m.%Y в %H:%M')}\n"
        f"📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110\n\n"
        "Ждём вас! Мы пришлём напоминание накануне вечером и за 2 часа до визита.\n\n"
        "💡 Все свои записи ты всегда найдёшь в разделе «📋 Мои записи» в меню — "
        "там можно посмотреть детали, перенести или отменить визит."
    )

    if isinstance(target_event_or_msg, MessageEvent):
        await _edit_or_send(
            target_event_or_msg,
            message=text,
            keyboard=cancel_kb.get_json(),
        )
        await target_event_or_msg.send_empty_answer()
    else:
        await target_event_or_msg.answer(
            message=text,
            keyboard=cancel_kb.get_json(),
        )

    for admin_id in ADMIN_IDS:
        try:
            await api.messages.send(
                user_id=admin_id,
                random_id=secrets.randbits(31),
                message=(
                    "🔥 Новая онлайн-запись через бота (ВКонтакте)!\n\n"
                    f"👤 Клиент: {client_name}\n"
                    f"📱 Телефон: {phone}\n"
                    f"✂️ Услуги: {', '.join(calc['names'])}\n"
                    f"📅 Время: {dt.strftime('%d.%m.%Y в %H:%M')}\n"
                    f"💰 Сумма: {calc['total_price']} ₽"
                ),
            )
        except Exception as e:
            logger.error("Failed to notify VK admin %s: %s", admin_id, e)

    return appt_id


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_slot",
)
async def callback_book_slot(event: MessageEvent):
    user_id = event.user_id
    payload = get_payload(event)
    timestamp = int(payload.get("ts", 0))
    user_selected_slot[user_id] = timestamp

    phone = get_user_phone(user_id, platform="vk")
    if not phone:
        try:
            await _edit_or_send(event, 
                message=(
                    "📱 Для завершения записи отправьте ваш номер телефона ответным сообщением (например, +79991234567):\n\n"
                    "Или вы можете записаться напрямую через сайт «Срез»:"
                ),
                keyboard=get_booking_keyboard(),
            )
        except Exception:
            pass
        await event.send_empty_answer()
        return

    current = user_selected_services.get(user_id, {"hair"})
    client_name = await _resolve_vk_client_name(event.ctx_api, user_id)
    await _create_vk_booking_and_notify(
        event.ctx_api, user_id, phone, timestamp, current, client_name, event
    )
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_cancel",
)
async def callback_book_cancel(event: MessageEvent):
    user_id = event.user_id
    user_selected_slot.pop(user_id, None)
    try:
        await _edit_or_send(event, message="Запись отменена.")
    except Exception:
        pass
    await event.show_snackbar("Запись отменена.")


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "appt_confirm",
)
async def callback_confirm_appointment(event: MessageEvent):
    payload = get_payload(event)
    appt_id = payload.get("id")
    if not appt_id:
        await safe_event_answer(event, "Некорректная запись")
        return

    success, msg, appt = confirm_appointment_by_client(
        int(appt_id), platform="vk", platform_user_id=event.user_id
    )
    if not success:
        await safe_event_answer(event, msg)
        return

    dt_str = ""
    if appt and appt.get("appointment_time"):
        dt = appt["appointment_time"]
        if isinstance(dt, str):
            dt = datetime.datetime.fromisoformat(dt)
        dt_local = dt.astimezone(SARATOV_TZ) if dt.tzinfo else dt
        dt_str = dt_local.strftime("%d.%m.%Y в %H:%M")

    kb = Keyboard(inline=True)
    kb.add(OpenLink(link="https://yandex.ru/maps/-/CDu~v-7x", label="📍 Как добраться"))
    kb.row()
    kb.add(Callback("❌ Отменить запись", {"cmd": "appt_cancel", "id": int(appt_id)}))
    kb.row()
    kb.add(Callback("📋 Мои записи", {"cmd": "my_list"}))

    try:
        await _edit_or_send(event, 
            message=(
                "✅ Запись подтверждена!\n\n"
                f"Спасибо за подтверждение! Ждём вас {dt_str} ✂️\n"
                "📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110"
            ),
            keyboard=kb.get_json(),
        )
    except Exception as e:
        logger.warning("Error editing confirmed appt msg: %s", e)

    await safe_event_answer(event, "Запись подтверждена! Ждём вас.")

    if appt:
        for admin_id in ADMIN_IDS:
            try:
                await event.ctx_api.messages.send(
                    user_id=admin_id,
                    random_id=secrets.randbits(31),
                    message=(
                        "✅ Клиент подтвердил запись (ВКонтакте)!\n\n"
                        f"👤 Клиент: {appt.get('client_name') or 'Клиент'}\n"
                        f"📱 Телефон: {appt.get('phone')}\n"
                        f"✂️ Услуги: {appt.get('services')}\n"
                        f"📅 Время: {dt_str}\n"
                        "Клиент точно придёт к назначенному времени."
                    ),
                )
            except Exception as e:
                logger.error("Failed to notify VK admin %s: %s", admin_id, e)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "appt_cancel",
)
async def callback_cancel_appointment(event: MessageEvent):
    payload = get_payload(event)
    appt_id = payload.get("id")
    if not appt_id:
        await safe_event_answer(event, "Некорректная запись")
        return

    success, msg, appt = cancel_appointment(
        int(appt_id), platform="vk", platform_user_id=event.user_id
    )
    if not success:
        await safe_event_answer(event, msg)
        return

    nav_kb = Keyboard(inline=True)
    nav_kb.add(Callback("📋 Мои записи", {"cmd": "my_list"}))
    nav_kb.row()
    nav_kb.add(Callback("✂️ Записаться", {"cmd": "book_services"}))

    try:
        await _edit_or_send(event, 
            message=(
                "❌ Запись успешно отменена\n\n"
                "Будем рады видеть в другой раз! Чтобы выбрать новую дату, нажми «✂️ Записаться».\n\n"
                "💡 Остальные твои записи — в разделе «📋 Мои записи»."
            ),
            keyboard=nav_kb.get_json(),
        )
    except Exception as e:
        logger.warning("Error editing cancelled appt msg: %s", e)

    await safe_event_answer(event, "Запись отменена.")

    if appt:
        dt_str = str(appt.get("appointment_time", ""))
        for admin_id in ADMIN_IDS:
            try:
                await event.ctx_api.messages.send(
                    user_id=admin_id,
                    random_id=secrets.randbits(31),
                    message=(
                        "⚠️ Клиент отменил запись (ВКонтакте)!\n\n"
                        f"👤 Клиент: {appt.get('client_name') or 'Клиент'}\n"
                        f"📱 Телефон: {appt.get('phone')}\n"
                        f"✂️ Услуги: {appt.get('services')}\n"
                        f"📅 Время: {dt_str}\n"
                        "Слот снова свободен для других клиентов."
                    ),
                )
            except Exception as e:
                logger.error("Failed to notify VK admin %s: %s", admin_id, e)


@labeler.message(func=lambda msg: bool(msg.text and is_phone_number(msg.text)))
async def phone_message_handler(message: Message):
    from handlers.referrals import _has_active_vk_draft
    if _has_active_vk_draft(message):
        return

    user_id = message.from_id
    success, link_msg = link_user_phone(user_id, message.text, platform="vk")
    if not success:
        await message.answer(link_msg or "Пожалуйста, укажи корректный номер телефона (например, +79991234567).")
        return

    pending_ts = user_selected_slot.pop(user_id, None)
    if pending_ts:
        phone = get_user_phone(user_id, platform="vk") or normalize_phone(message.text)
        current = user_selected_services.get(user_id, {"hair"})
        dt = datetime.datetime.fromtimestamp(pending_ts, tz=SARATOV_TZ)

        await message.answer(f"⏳ Номер {phone} сохранён. Оформляю запись на {dt.strftime('%d.%m.%Y в %H:%M')}...")
        client_name = await _resolve_vk_client_name(message.ctx_api, user_id)
        await _create_vk_booking_and_notify(
            message.ctx_api, user_id, phone, pending_ts, current, client_name, message
        )
        return

    user_selected_services[user_id] = {"hair"}
    await message.answer(
        f"✅ {link_msg}\n\n"
        "Выберите нужные услуги для записи:",
        keyboard=get_services_keyboard(user_selected_services[user_id]),
    )
