import datetime
import json
import logging
from typing import Any

from vkbottle import Callback, GroupEventType, Keyboard
from vkbottle.bot import BotLabeler, Message, MessageEvent

from config import ADMIN_IDS, BOOKING_URL
from services.appointments import (
    SARATOV_TZ,
    cancel_appointment,
    create_or_update_appointment,
)
from keyboards.booking import (
    get_booking_keyboard,
    get_days_keyboard,
    get_services_keyboard,
    get_slots_keyboard,
    get_sms_keyboard,
)
from services.masters import (
    SERVICES,
    calculate_services,
    confirm_sms_code,
    get_schedule,
    initiate_booking,
    user_pending_booking,
    user_selected_day,
    user_selected_services,
)
from services.referrals import get_user_phone, is_phone_number, link_user_phone

logger = logging.getLogger(__name__)
labeler = BotLabeler()


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
        await event.edit_message(
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
        await event.edit_message(
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
    phone = get_user_phone(user_id, platform="vk")

    if not phone:
        try:
            await event.edit_message(
                message=(
                    "📱 Для онлайн-записи и сохранения бонусов отправьте ваш номер телефона ответным сообщением (например, +79991234567):\n\n"
                    "Или вы можете записаться напрямую через сайт Masters:"
                ),
                keyboard=get_booking_keyboard(),
            )
        except Exception as e:
            logger.warning("Error in callback_book_days: %s", e)
        await event.send_empty_answer()
        return

    current = user_selected_services.get(user_id, {"hair"})
    schedule = get_schedule(current)

    if not schedule:
        try:
            await event.edit_message(
                message="⚠️ Не удалось загрузить расписание онлайн. Выберите удобное время на сайте:",
                keyboard=get_booking_keyboard(),
            )
        except Exception:
            pass
        await event.send_empty_answer()
        return

    calc = calculate_services(current)
    try:
        await event.edit_message(
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
    schedule = get_schedule(current)
    day_data = schedule.get(day_key)
    slots = day_data["slots"] if day_data else []

    title = day_data["title"] if day_data else day_key
    try:
        await event.edit_message(
            message=f"🕒 Свободные окна на {title}:\n\nВыберите время начала:",
            keyboard=get_slots_keyboard(slots),
        )
    except Exception as e:
        logger.warning("Error in callback_book_day: %s", e)
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_slot",
)
async def callback_book_slot(event: MessageEvent):
    user_id = event.user_id
    payload = get_payload(event)
    timestamp = int(payload.get("ts", 0))

    phone = get_user_phone(user_id, platform="vk")
    if not phone:
        try:
            await event.edit_message(
                message="📱 Отправьте ваш номер телефона сообщением (например, +79991234567) для подтверждения записи:",
                keyboard=get_booking_keyboard(),
            )
        except Exception:
            pass
        await event.send_empty_answer()
        return

    try:
        await event.edit_message(
            message="⏳ Связываюсь с Masters и отправляю проверочный код...",
            keyboard=get_sms_keyboard(),
        )
    except Exception:
        pass

    current = user_selected_services.get(user_id, {"hair"})
    success, msg, session_data = initiate_booking(phone, current, timestamp)

    if not success or not session_data:
        try:
            await event.edit_message(
                message=f"⚠️ {msg}\n\nВы можете завершить бронирование напрямую через сайт:",
                keyboard=get_booking_keyboard(),
            )
        except Exception:
            pass
        await event.send_empty_answer()
        return

    user_pending_booking[user_id] = session_data
    dt = datetime.datetime.fromtimestamp(timestamp)

    try:
        await event.edit_message(
            message=(
                f"📩 Подтверждение записи\n\n"
                f"Вы выбрали: {dt.strftime('%d.%m.%Y в %H:%M')}\n"
                f"На номер {phone} отправлено СМС-сообщение с кодом от Masters.\n\n"
                "👉 Напишите 4 цифры из СМС в ответном сообщении:"
            ),
            keyboard=get_sms_keyboard(),
        )
    except Exception as e:
        logger.warning("Error in callback_book_slot: %s", e)
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "book_cancel",
)
async def callback_book_cancel(event: MessageEvent):
    user_id = event.user_id
    user_pending_booking.pop(user_id, None)
    try:
        await event.edit_message(message="Запись отменена.")
    except Exception:
        pass
    await event.show_snackbar("Запись отменена.")


@labeler.message(func=lambda msg: bool(msg.text and msg.text.strip().isdigit() and len(msg.text.strip()) == 4))
async def sms_code_handler(message: Message):
    user_id = message.from_id
    session_data = user_pending_booking.get(user_id)

    if not session_data:
        return

    code = message.text.strip()
    success, msg = confirm_sms_code(session_data, code)

    if not success:
        await message.answer(
            f"❌ {msg}\n\nПопробуйте ввести 4 цифры ещё раз или завершите запись на сайте:",
            keyboard=get_sms_keyboard(),
        )
        return

    user_pending_booking.pop(user_id, None)
    calc = session_data["calc"]
    dt = datetime.datetime.fromtimestamp(session_data["timestamp"], tz=SARATOV_TZ)
    phone = session_data["phone"]

    appt_id = create_or_update_appointment(
        phone=phone,
        appointment_time=dt,
        services=", ".join(calc["names"]),
        total_price=calc["total_price"],
        client_name=f"id{message.from_id}",
        platform="vk",
        platform_user_id=user_id,
        masters_id=session_data.get("appointment_id"),
    )

    cancel_kb = Keyboard(inline=True)
    cancel_kb.add(Callback("❌ Отменить запись", {"cmd": "appt_cancel", "id": appt_id}))

    await message.answer(
        f"🎉 Запись успешно подтверждена!\n\n"
        f"✂️ Услуги: {', '.join(calc['names'])}\n"
        f"💰 Стоимость: {calc['total_price']} ₽ ({calc['duration_text']})\n"
        f"📅 Дата и время: {dt.strftime('%d.%m.%Y в %H:%M')}\n"
        f"📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110\n\n"
        "Ждём вас! Мы пришлём напоминание накануне вечером и за 2 часа до визита.",
        keyboard=cancel_kb.get_json(),
    )

    for admin_id in ADMIN_IDS:
        try:
            await message.ctx_api.messages.send(
                user_id=admin_id,
                random_id=0,
                message=(
                    "🔥 Новая онлайн-запись через бота (ВКонтакте)!\n\n"
                    f"👤 Клиент: id{message.from_id}\n"
                    f"📱 Телефон: {phone}\n"
                    f"✂️ Услуги: {', '.join(calc['names'])}\n"
                    f"📅 Время: {dt.strftime('%d.%m.%Y в %H:%M')}\n"
                    f"💰 Сумма: {calc['total_price']} ₽"
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
        await event.show_snackbar("Некорректная запись")
        return

    success, msg, appt = cancel_appointment(int(appt_id))
    if not success:
        await event.show_snackbar(msg)
        return

    try:
        await event.edit_message(
            message=(
                "❌ Запись успешно отменена\n\n"
                "Будем рады видеть в другой раз! Чтобы выбрать новую дату, нажми «✂️ Записаться»."
            )
        )
    except Exception as e:
        logger.warning("Error editing cancelled appt msg: %s", e)

    await event.show_snackbar("Запись отменена.")

    if appt:
        dt_str = str(appt.get("appointment_time", ""))
        for admin_id in ADMIN_IDS:
            try:
                await event.ctx_api.messages.send(
                    user_id=admin_id,
                    random_id=0,
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
    success, _ = link_user_phone(message.from_id, message.text, platform="vk")
    if success:
        user_id = message.from_id
        user_selected_services[user_id] = {"hair"}
        await message.answer(
            "✅ Номер телефона успешно сохранен!\n\n"
            "Выберите нужные услуги для записи:",
            keyboard=get_services_keyboard(user_selected_services[user_id]),
        )
    else:
        await message.answer("Пожалуйста, укажи корректный номер телефона (например, +79991234567).")
