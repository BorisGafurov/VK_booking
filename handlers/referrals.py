import asyncio
import datetime
import json
import logging
import random
import re
from typing import Any
from zoneinfo import ZoneInfo

from vkbottle import Callback, GroupEventType, Keyboard
from vkbottle.bot import BotLabeler, Message, MessageEvent

from config import ADMIN_IDS, REFERRAL_REWARD_TEXT
from services.appointments import (
    SARATOV_TZ,
    adjust_master_day_hours,
    cancel_appointment,
    complete_appointment,
    confirm_appointment_by_client,
    confirm_manual_appointment_masters_sms,
    create_manual_appointment,
    get_appointment,
    get_appointments_counts,
    get_current_saratov_time,
    get_day_time_slots_status,
    get_master_appointments,
    get_master_day_schedule,
    get_upcoming_days_schedule,
    pending_manual_bookings,
    reschedule_appointment,
    set_master_day_schedule,
    update_appointment_price_and_service,
)
from services.client_import import (
    get_clients_page,
    get_clients_stats,
    import_clients_from_file,
    import_clients_from_text,
    update_appointment_client_details,
)
from services.masters import get_service_options_for_creation
from services.masters_sync import (
    confirm_master_login,
    disconnect_masters_session,
    get_active_masters_session,
    get_suggested_master_phone,
    pending_master_logins,
    request_master_login,
    sync_masters_appointments,
)
from services.referrals import (
    build_referral_link,
    get_active_rewards,
    get_broadcast_recipients,
    get_pending_referrals,
    get_referral_stats,
    get_user_phone,
    is_admin_user,
    is_master_user,
    is_phone_number,
    mark_referral_visited,
    mark_reward_used,
    normalize_phone,
    save_user,
)

logger = logging.getLogger(__name__)
labeler = BotLabeler()

PAGE_SIZE = 3
admin_broadcast_drafts: dict[int, dict] = {}
admin_create_drafts: dict[int, dict] = {}
admin_resched_drafts: dict[int, dict] = {}
admin_edit_client_drafts: dict[int, dict] = {}
admin_edit_price_drafts: dict[int, dict] = {}
admin_clients_state: dict[int, dict] = {}

RU_MONTHS = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
RU_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


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


def is_admin(user_id: int) -> bool:
    return is_master_user(user_id, platform="vk")


async def safe_event_answer(event: MessageEvent, snackbar: str | None = None) -> None:
    try:
        if snackbar:
            await event.show_snackbar(snackbar[:60])
        else:
            await event.send_empty_answer()
    except Exception:
        pass


def parse_to_saratov(val) -> datetime.datetime:
    if isinstance(val, datetime.datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=SARATOV_TZ)
        return val.astimezone(SARATOV_TZ)
    if isinstance(val, str):
        val_clean = val.replace("T", " ")
        if "." in val_clean:
            val_clean = val_clean.split(".")[0]
        try:
            dt = datetime.datetime.strptime(val_clean[:19], "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=SARATOV_TZ)
        except Exception:
            pass
    return get_current_saratov_time()


def format_dt(val) -> str:
    dt = parse_to_saratov(val)
    return f"{dt.day} {RU_MONTHS[dt.month]} в {dt.strftime('%H:%M')}"


def admin_menu_keyboard() -> str:
    """
    VK Inline Keyboard: strictly <= 6 rows and <= 10 buttons.
    Total: 5 rows, 8 buttons.
    """
    kb = Keyboard(inline=True)
    kb.add(Callback("📋 Календарь записей", {"cmd": "admin_appts", "filter": "all", "offset": 0}))
    kb.row()
    kb.add(Callback("➕ Новая запись", {"cmd": "admin_create_start"}))
    kb.add(Callback("👥 Клиенты", {"cmd": "admin_clients", "offset": 0}))
    kb.row()
    kb.add(Callback("🕒 Рабочий график", {"cmd": "admin_sched", "offset": 0}))
    kb.add(Callback("⏳ Ожидающие", {"cmd": "admin_pending", "offset": 0}))
    kb.row()
    kb.add(Callback("💰 Бонусы", {"cmd": "admin_rewards", "offset": 0}))
    kb.add(Callback("🔄 Masters", {"cmd": "admin_masters"}))
    kb.row()
    kb.add(Callback("📢 Рассылка", {"cmd": "admin_broadcast"}))
    return kb.get_json()


def admin_appts_keyboard(
    appts: list[dict],
    current_filter: str,
    offset: int,
    total: int,
    counts: dict[str, int] | None = None,
) -> str:
    """
    VK Inline Keyboard: strictly <= 6 rows and <= 10 buttons.
    Row 1: Filters (3 buttons)
    Rows 2..4: Up to 3 appointments (1 button per row)
    Row 5: Navigation (1-2 buttons, if needed)
    Row 6: Actions (2 buttons)
    Max rows: 6, max buttons: 10.
    """
    kb = Keyboard(inline=True)

    if counts is None:
        try:
            counts = get_appointments_counts()
        except Exception:
            counts = {"today": 0, "tomorrow": 0, "pending": 0, "all": total}

    filters = [
        ("today", f"Сегодня ({counts.get('today', 0)})"),
        ("tomorrow", f"Завтра ({counts.get('tomorrow', 0)})"),
        ("all", f"Все ({counts.get('all', 0)})"),
    ]
    for f_key, f_title in filters:
        title = f"• {f_title} •" if f_key == current_filter else f_title
        kb.add(Callback(title, {"cmd": "admin_appts", "filter": f_key, "offset": 0}))
    kb.row()

    for item in appts[:3]:
        dt = parse_to_saratov(item["appointment_time"])
        time_str = dt.strftime("%H:%M")
        if current_filter == "all":
            time_str = f"{dt.day} {RU_MONTHS[dt.month]} {time_str}"

        st = item.get("status", "confirmed")
        if st == "completed":
            badge = "✅"
        elif item.get("client_confirmed"):
            badge = "🟢"
        else:
            badge = "⏳"

        name = (item.get("client_name") or "Клиент")[:10]
        price = item.get("total_price") or 0
        btn_text = f"{badge} {time_str} · {name} ({price} ₽)"[:40]
        kb.add(Callback(btn_text, {"cmd": "admin_appt", "id": item["id"], "filter": current_filter, "offset": offset}))
        kb.row()

    if not appts:
        kb.add(Callback("Нет записей в этой категории", {"cmd": "noop"}))
        kb.row()

    nav_needed = False
    if offset > 0:
        prev_offset = max(0, offset - PAGE_SIZE)
        kb.add(Callback("◀️ Назад", {"cmd": "admin_appts", "filter": current_filter, "offset": prev_offset}))
        nav_needed = True
    if offset + PAGE_SIZE < total:
        next_offset = offset + PAGE_SIZE
        kb.add(Callback("Вперёд ▶️", {"cmd": "admin_appts", "filter": current_filter, "offset": next_offset}))
        nav_needed = True
    if nav_needed:
        kb.row()

    kb.add(Callback("➕ Новая", {"cmd": "admin_create_start"}))
    kb.add(Callback("⬅️ Меню", {"cmd": "admin_menu"}))
    return kb.get_json()


def admin_appt_card_keyboard(appt: dict, current_filter: str, offset: int) -> str:
    kb = Keyboard(inline=True)
    appt_id = appt["id"]
    status = appt.get("status", "confirmed")

    if status != "completed" and status != "cancelled":
        kb.add(Callback("✂️ Пострижен (Завершить)", {"cmd": "admin_complete_appt", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.row()
        if not appt.get("client_confirmed"):
            kb.add(Callback("✅ Подтвердить запись", {"cmd": "admin_confirm_appt", "id": appt_id, "filter": current_filter, "offset": offset}))
            kb.row()
        kb.add(Callback("✏️ Имя / тел.", {"cmd": "admin_edit_client", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.add(Callback("💰 Цена/услуга", {"cmd": "admin_edit_price", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.row()
        kb.add(Callback("🗓 Перенести", {"cmd": "admin_resched_start", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.add(Callback("❌ Отменить", {"cmd": "admin_cancel_appt", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.row()
    else:
        kb.add(Callback("✏️ Имя / тел.", {"cmd": "admin_edit_client", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.add(Callback("💰 Цена/услуга", {"cmd": "admin_edit_price", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.row()

    kb.add(Callback("⬅️ К списку записей", {"cmd": "admin_appts", "filter": current_filter, "offset": offset}))
    return kb.get_json()


def admin_schedule_keyboard(days: list[dict], offset: int, total: int) -> str:
    """
    VK Inline Keyboard for schedule: 4 days per page.
    Rows 1-4: 4 days (1 button per row)
    Row 5: Navigation (1-2 buttons)
    Row 6: Main menu (1 button)
    Total: 6 rows, 7 buttons.
    """
    kb = Keyboard(inline=True)
    for d in days[:4]:
        status_txt = f"🟢 {d['start_time']}-{d['end_time']}" if d["is_working"] else "🔴 Выходной"
        text = f"{d['title']}: {status_txt}"[:40]
        kb.add(Callback(text, {"cmd": "admin_sched_day", "day_key": d["day_key"]}))
        kb.row()

    nav_needed = False
    if offset > 0:
        kb.add(Callback("◀️ Назад", {"cmd": "admin_sched", "offset": max(0, offset - 4)}))
        nav_needed = True
    if offset + 4 < total:
        kb.add(Callback("Вперёд ▶️", {"cmd": "admin_sched", "offset": offset + 4}))
        nav_needed = True
    if nav_needed:
        kb.row()

    kb.add(Callback("⬅️ Главное меню", {"cmd": "admin_menu"}))
    return kb.get_json()


def admin_sched_day_keyboard(sched: dict) -> str:
    day_key = sched["day_key"]
    kb = Keyboard(inline=True)
    if sched["is_working"]:
        kb.add(Callback("🔴 Сделать выходным днем", {"cmd": "admin_sched_toggle", "day_key": day_key}))
        kb.row()
        kb.add(Callback("➖ Начало -1ч", {"cmd": "admin_sched_adj", "day_key": day_key, "which": "start", "delta": -1}))
        kb.add(Callback("➕ Начало +1ч", {"cmd": "admin_sched_adj", "day_key": day_key, "which": "start", "delta": 1}))
        kb.row()
        kb.add(Callback("➖ Конец -1ч", {"cmd": "admin_sched_adj", "day_key": day_key, "which": "end", "delta": -1}))
        kb.add(Callback("➕ Конец +1ч", {"cmd": "admin_sched_adj", "day_key": day_key, "which": "end", "delta": 1}))
        kb.row()
    else:
        kb.add(Callback("🟢 Сделать рабочим (10:00 - 19:00)", {"cmd": "admin_sched_toggle", "day_key": day_key}))
        kb.row()

    kb.add(Callback("⬅️ К календарю графика", {"cmd": "admin_sched", "offset": 0}))
    return kb.get_json()


def pending_referrals_keyboard(referrals: list[dict], offset: int, total: int) -> str:
    kb = Keyboard(inline=True)
    for item in referrals:
        invited_name = (item["invited_first_name"] or "Без имени")[:10]
        referrer_name = (item["referrer_first_name"] or "Без имени")[:10]
        btn_text = f"✅ #{item['id']} · {invited_name} ← {referrer_name}"[:40]
        kb.add(Callback(btn_text, {"cmd": "ref_visit", "id": item["id"], "offset": offset}))
        kb.row()

    if not referrals:
        kb.add(Callback("Нет ожидающих рефералов", {"cmd": "noop"}))
        kb.row()

    nav_row_needed = False
    if offset > 0:
        prev_offset = max(0, offset - PAGE_SIZE)
        kb.add(Callback("◀️ Назад", {"cmd": "admin_pending", "offset": prev_offset}))
        nav_row_needed = True
    if offset + PAGE_SIZE < total:
        next_offset = offset + PAGE_SIZE
        kb.add(Callback("Вперёд ▶️", {"cmd": "admin_pending", "offset": next_offset}))
        nav_row_needed = True
    if nav_row_needed:
        kb.row()

    kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
    return kb.get_json()


def active_rewards_keyboard(rewards: list[dict], offset: int, total: int) -> str:
    kb = Keyboard(inline=True)
    for item in rewards:
        first_name = (item["first_name"] or "Без имени")[:12]
        btn_text = f"✂️ Списать #{item['id']} · {first_name} · {item['amount']} ₽"[:40]
        kb.add(Callback(btn_text, {"cmd": "reward_use", "id": item["id"], "offset": offset}))
        kb.row()

    if not rewards:
        kb.add(Callback("Нет активных бонусов", {"cmd": "noop"}))
        kb.row()

    nav_row_needed = False
    if offset > 0:
        prev_offset = max(0, offset - PAGE_SIZE)
        kb.add(Callback("◀️ Назад", {"cmd": "admin_rewards", "offset": prev_offset}))
        nav_row_needed = True
    if offset + PAGE_SIZE < total:
        next_offset = offset + PAGE_SIZE
        kb.add(Callback("Вперёд ▶️", {"cmd": "admin_rewards", "offset": next_offset}))
        nav_row_needed = True
    if nav_row_needed:
        kb.row()

    kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
    return kb.get_json()


@labeler.message(text=["/myid", "мой id", "Мой id", "myid", "мой ид", "Мой ид", "id", "ID"])
async def my_id(message: Message):
    await message.answer(f"Твой VK ID: {message.from_id}")


@labeler.message(
    text=[
        "🎁 Пригласить друга",
        "Пригласить друга",
        "пригласить друга",
        "/referral",
        "/ref",
        "бонусы",
        "Бонусы",
    ]
)
async def referrals(message: Message):
    user_info = None
    try:
        user_info = await message.get_user(fields=["domain"])
    except Exception as e:
        logger.warning("Не удалось получить профиль пользователя %s: %s", message.from_id, e)

    first_name = user_info.first_name if user_info else ""
    last_name = user_info.last_name if user_info else ""
    domain = user_info.domain if user_info else None

    save_user(message.from_id, domain, first_name, last_name)

    referral_link = build_referral_link(message.from_id)
    stats = get_referral_stats(message.from_id)

    text = (
        "🎁 Приглашай друзей и получай бонусы!\n\n"
        "Отправь свою персональную ссылку другу. "
        f"Когда он впервые придет на стрижку или уход, тебе начислится {REFERRAL_REWARD_TEXT}!\n\n"
        f"🔗 Твоя реферальная ссылка:\n{referral_link}\n"
        f"(Или друг может просто написать в чат код: ref_{message.from_id})\n\n"
        "📊 Твоя статистика:\n"
        f"• Переходов по ссылке: {stats['started']}\n"
        f"• Подтвержденных визитов: {stats['confirmed']}\n"
        f"• Доступных бонусов: {stats['active_rewards_count']} (на сумму {stats['active_rewards_sum']} ₽)\n\n"
        "Бонусы суммируются: 300 ₽ за каждого друга. Их можно списать при оплате услуг мастера."
    )
    await message.answer(text)


@labeler.message(
    text=[
        "⚙️ Админ-панель",
        "Админ-панель",
        "админ-панель",
        "/admin",
        "админ",
        "Админ",
        "админка",
        "Админка",
    ]
)
async def admin_panel(message: Message):
    if not is_admin(message.from_id):
        await message.answer("Нет доступа.")
        return

    try:
        await message.answer(
            "⚙️ Панель мастера и администратора:",
            keyboard=admin_menu_keyboard(),
        )
    except Exception as e:
        logger.error("Ошибка при открытии админ-панели в VK: %s", e)
        await message.answer(f"Ошибка при открытии панели мастера: {e}")


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_menu",
)
async def admin_menu_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    try:
        await event.edit_message(
            message="⚙️ Панель мастера и администратора:",
            keyboard=admin_menu_keyboard(),
        )
    except Exception as e:
        logger.warning("Ошибка при открытии админ-меню: %s", e)

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_appts",
)
async def admin_appts_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    current_filter = payload.get("filter", "all")
    offset = int(payload.get("offset", 0))

    counts = get_appointments_counts()
    appts, total = get_master_appointments(date_filter=current_filter, limit=PAGE_SIZE, offset=offset)

    filter_names = {
        "today": "Сегодня",
        "tomorrow": "Завтра",
        "pending": "Ожидающие подтверждения",
        "all": "Все будущие",
    }
    title = filter_names.get(current_filter, current_filter)

    text = f"📋 Календарь записей · {title} ({total} всего):\n\n"
    if not appts:
        text += "Записей не найдено."
    else:
        text += "Нажмите на запись для просмотра деталей, подтверждения, завершения визита или переноса:"

    try:
        await event.edit_message(
            message=text,
            keyboard=admin_appts_keyboard(appts, current_filter, offset, total, counts),
        )
    except Exception as e:
        logger.warning("Ошибка при открытии календаря записей: %s", e)

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") in ("admin_appt", "admin_appt_detail"),
)
async def admin_appt_detail_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    appt = get_appointment(appt_id)
    if not appt:
        await event.show_snackbar("Запись не найдена")
        return

    st = appt.get("status", "confirmed")
    if st == "completed":
        st_text = "✂️ Завершена (клиент пострижен)"
    elif st == "cancelled":
        st_text = "❌ Отменена"
    elif appt.get("client_confirmed"):
        st_text = "🟢 Подтверждена клиентом"
    else:
        st_text = "⏳ Ожидает подтверждения"

    phone = appt.get("phone") or "—"
    name = appt.get("client_name") or "Клиент"
    services = appt.get("services") or "—"
    price = appt.get("total_price") or 0
    time_fmt = format_dt(appt["appointment_time"])
    platform = (appt.get("platform") or "vk").upper()
    masters_id = appt.get("masters_id") or "—"

    card_text = (
        f"📋 Карточка записи #{appt['id']}\n\n"
        f"👤 Клиент: {name}\n"
        f"📱 Телефон: {phone}\n"
        f"✂️ Услуга: {services}\n"
        f"💰 Стоимость: {price} ₽\n"
        f"⏰ Дата и время: {time_fmt}\n"
        f"📌 Статус: {st_text}\n"
        f"🌐 Источник: {platform}\n"
        f"🔄 Masters ID: {masters_id}"
    )

    try:
        await event.edit_message(
            message=card_text,
            keyboard=admin_appt_card_keyboard(appt, current_filter, offset),
        )
    except Exception as e:
        logger.warning("Ошибка при открытии карточки записи: %s", e)

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_complete_appt",
)
async def admin_complete_appt_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    ok, msg, appt, ref_notify = complete_appointment(appt_id)
    if not ok:
        await event.show_snackbar(msg[:60])
        return

    if ref_notify and ref_notify.get("referrer_vk_id"):
        try:
            invited_name = ref_notify["invited_first_name"]
            await event.ctx_api.messages.send(
                peer_id=ref_notify["referrer_vk_id"],
                random_id=random.randint(1, 2**31 - 1),
                message=(
                    "🎉 Отличные новости!\n"
                    f"Твой друг {invited_name} посетил салон!\n"
                    f"Тебе начислен бонус: {ref_notify['amount']} ₽.\n\n"
                    f"💰 Доступный баланс бонусов: {ref_notify['new_balance']} ₽.\n"
                    "Ты можешь списать их при следующем визите к мастеру!"
                ),
            )
        except Exception as e:
            logger.warning("Не удалось отправить уведомление рефереру: %s", e)

    await safe_event_answer(event, snackbar=msg)
    # Refresh card
    updated_appt = get_appointment(appt_id)
    if updated_appt:
        try:
            await event.edit_message(
                message=f"✅ {msg}\n\n" + f"📋 Карточка записи #{appt_id}\nСтатус: ✂️ Завершена (клиент пострижен)",
                keyboard=admin_appt_card_keyboard(updated_appt, current_filter, offset),
            )
        except Exception:
            pass


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_confirm_appt",
)
async def admin_confirm_appt_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    ok, msg, appt = confirm_appointment_by_client(appt_id)
    await safe_event_answer(event, snackbar=msg)
    await admin_appt_detail_handler(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_cancel_appt",
)
async def admin_cancel_appt_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    ok, msg, appt = cancel_appointment(appt_id)
    if ok and appt and appt.get("platform_user_id") and appt.get("platform") == "vk":
        try:
            await event.ctx_api.messages.send(
                peer_id=appt["platform_user_id"],
                random_id=random.randint(1, 2**31 - 1),
                message="❌ Ваша запись была отменена мастером.\n\nЕсли у вас есть вопросы, напишите мастеру в сообщения группы.",
            )
        except Exception as e:
            logger.warning("Failed to notify client about cancel: %s", e)

    await safe_event_answer(event, snackbar="Запись отменена")
    # Redirect to list
    appts, total = get_master_appointments(date_filter=current_filter, limit=PAGE_SIZE, offset=offset)
    try:
        await event.edit_message(
            message=f"❌ Запись #{appt_id} отменена.\n\n📋 Календарь записей ({total} всего):",
            keyboard=admin_appts_keyboard(appts, current_filter, offset, total),
        )
    except Exception:
        pass


# Reschedule flow
@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_resched_start",
)
async def admin_resched_start_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    now = get_current_saratov_time()
    kb = Keyboard(inline=True)
    for i in range(5):
        d = now + datetime.timedelta(days=i)
        day_key = d.strftime("%Y-%m-%d")
        title = f"{RU_WEEKDAYS[d.weekday()]}, {d.day} {RU_MONTHS[d.month]}"
        if i == 0:
            title += " (Сегодня)"
        elif i == 1:
            title += " (Завтра)"
        kb.add(Callback(title, {"cmd": "admin_resched_day", "id": appt_id, "day": day_key, "filter": current_filter, "offset": offset}))
        kb.row()

    kb.add(Callback("⬅️ Назад к карточке", {"cmd": "admin_appt", "id": appt_id, "filter": current_filter, "offset": offset}))

    try:
        await event.edit_message(
            message=f"🗓 Перенос записи #{appt_id}\n\nВыберите новую дату:",
            keyboard=kb.get_json(),
        )
    except Exception as e:
        logger.warning("Ошибка при переносе записи: %s", e)

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_resched_day",
)
async def admin_resched_day_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    day_key = payload.get("day", "")
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    hours = ["10:00", "12:00", "14:00", "16:00", "18:00", "19:00"]
    kb = Keyboard(inline=True)
    count = 0
    for h in hours:
        kb.add(Callback(h, {"cmd": "admin_resched_time", "id": appt_id, "day": day_key, "time": h, "filter": current_filter, "offset": offset}))
        count += 1
        if count % 3 == 0:
            kb.row()

    kb.add(Callback("✏️ Своё время (08:30...)", {"cmd": "admin_resched_custom", "id": appt_id, "day": day_key, "filter": current_filter, "offset": offset}))
    kb.row()
    kb.add(Callback("⬅️ Назад к датам", {"cmd": "admin_resched_start", "id": appt_id, "filter": current_filter, "offset": offset}))

    try:
        await event.edit_message(
            message=f"🗓 Перенос записи #{appt_id} на {day_key}\n\nВыберите время или введите своё:",
            keyboard=kb.get_json(),
        )
    except Exception as e:
        logger.warning("Ошибка при выборе времени переноса: %s", e)

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_resched_time",
)
async def admin_resched_time_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    day_key = payload.get("day", "")
    time_str = payload.get("time", "")

    try:
        new_dt = datetime.datetime.strptime(f"{day_key} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=SARATOV_TZ)
    except Exception:
        await event.show_snackbar("Ошибка формата времени")
        return

    ok, msg, appt = reschedule_appointment(appt_id, new_dt)
    if ok and appt and appt.get("platform_user_id") and appt.get("platform") == "vk":
        try:
            await event.ctx_api.messages.send(
                peer_id=appt["platform_user_id"],
                random_id=random.randint(1, 2**31 - 1),
                message=(
                    "🗓 Ваша запись перенесена мастером!\n\n"
                    f"⏰ Новое время: {format_dt(new_dt)}\n"
                    f"✂️ Услуга: {appt.get('services')}\n"
                    f"💰 Стоимость: {appt.get('total_price')} ₽\n\n"
                    "Ждем вас!"
                ),
            )
        except Exception as e:
            logger.warning("Failed to notify client: %s", e)

    await safe_event_answer(event, snackbar="Запись успешно перенесена!")
    await admin_appt_detail_handler(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_resched_custom",
)
async def admin_resched_custom_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    day_key = payload.get("day", "")

    admin_resched_drafts[event.user_id] = {
        "appt_id": appt_id,
        "day_key": day_key,
        "step": "awaiting_custom_time",
    }

    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_appt", "id": appt_id, "filter": "today", "offset": 0}))

    try:
        await event.edit_message(
            message=f"✏️ Введите желаемое время на {day_key}\n\nНапишите время в ответном сообщении (например 08:30 или 21:15):",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


# Manual appointment creation
@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_create_start",
)
async def admin_create_start_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    admin_create_drafts[event.user_id] = {"step": "service"}

    opts = await asyncio.to_thread(get_service_options_for_creation)
    kb = Keyboard(inline=True)
    for o in opts[:3]:
        kb.add(Callback(f"{o['name']} ({o['price']} ₽)"[:40], {"cmd": "admin_create_svc", "svc": o["name"], "price": o["price"]}))
        kb.row()
    kb.add(Callback("✏️ Другая услуга", {"cmd": "admin_create_svc_custom"}))
    kb.row()
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_menu"}))

    try:
        await event.edit_message(
            message="➕ Создание новой записи мастером\n\nШаг 1 из 4: Выберите услугу из Masters или введите другое название:",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_create_svc",
)
async def admin_create_svc_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    draft = admin_create_drafts.setdefault(event.user_id, {})
    draft["service"] = payload.get("svc", "Стрижка (МУЖСКАЯ)")
    draft["price"] = int(payload.get("price", 2300))
    draft["step"] = "day"

    now = get_current_saratov_time()
    kb = Keyboard(inline=True)
    for i in range(5):
        d = now + datetime.timedelta(days=i)
        day_key = d.strftime("%Y-%m-%d")
        title = f"{RU_WEEKDAYS[d.weekday()]}, {d.day} {RU_MONTHS[d.month]}"
        if i == 0:
            title += " (Сегодня)"
        elif i == 1:
            title += " (Завтра)"
        kb.add(Callback(title, {"cmd": "admin_create_day", "day": day_key}))
        kb.row()

    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_menu"}))

    try:
        await event.edit_message(
            message="➕ Шаг 2 из 4: Выберите дату записи:",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_create_svc_custom",
)
async def admin_create_svc_custom_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    draft = admin_create_drafts.setdefault(event.user_id, {})
    draft["step"] = "awaiting_custom_service"

    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_create_start"}))

    try:
        await event.edit_message(
            message="✏️ Введите название услуги в чат (например: Моделирование бороды VIP):",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_create_day",
)
async def admin_create_day_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    day_key = payload.get("day", "")
    draft = admin_create_drafts.setdefault(event.user_id, {})
    draft["day_key"] = day_key
    draft["step"] = "time"

    slots_info = await asyncio.to_thread(get_day_time_slots_status, day_key)
    free_slots = slots_info.get("free_slots", [])
    busy_appts = slots_info.get("busy_appts", [])

    lines = [f"➕ Шаг 3 из 4: Выберите время на {day_key}:\n"]
    if free_slots:
        lines.append(f"🟢 Свободно: {', '.join(free_slots)}")
    else:
        lines.append("⚠️ На этот день нет свободных окон в Masters")

    if busy_appts:
        lines.append("\n🔴 Уже занято:")
        for b in busy_appts:
            c_name = b["client_name"] or "Клиент"
            lines.append(f"• {b['time']} — {c_name} ({b['services']})")

    lines.append("\nВыберите свободное окно 🟢 или занятое 🔴 для овербукинга:")
    msg_text = "\n".join(lines)

    kb = Keyboard(inline=True)
    count = 0
    for s in free_slots[:6]:
        kb.add(Callback(f"🟢 {s}", {"cmd": "admin_create_time", "time": s}))
        count += 1
        if count % 3 == 0:
            kb.row()
    if count % 3 != 0:
        kb.row()

    for b in busy_appts[:2]:
        kb.add(Callback(f"🔴 {b['time']} (занято)"[:40], {"cmd": "admin_create_busy", "time": b["time"]}))
    if busy_appts:
        kb.row()

    kb.add(Callback("✏️ Своё время (08:30...)", {"cmd": "admin_create_time_custom"}))
    kb.row()
    kb.add(Callback("⬅️ Назад к датам", {"cmd": "admin_create_start"}))

    try:
        await event.edit_message(
            message=msg_text,
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_create_busy",
)
async def admin_create_busy_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    time_val = payload.get("time", "")
    draft = admin_create_drafts.setdefault(event.user_id, {})
    day_key = draft.get("day_key", "")

    slots_info = await asyncio.to_thread(get_day_time_slots_status, day_key)
    b_appt = next((b for b in slots_info.get("busy_appts", []) if b["time"] == time_val), None)
    details = f"{b_appt['client_name']} ({b_appt['services']})" if b_appt else "запись из Masters"

    text = (
        f"⚠️ Внимание: на {day_key} в {time_val} уже есть запись!\n\n"
        f"• {time_val} — {details}\n\n"
        "Вы действительно хотите записать ещё одного клиента на это же время (овербукинг)?"
    )
    kb = Keyboard(inline=True)
    kb.add(Callback(f"⚠️ Да, записать на {time_val}"[:40], {"cmd": "admin_create_time", "time": time_val}))
    kb.row()
    kb.add(Callback("⬅️ Выбрать другое время", {"cmd": "admin_create_day", "day": day_key}))

    try:
        await event.edit_message(message=text, keyboard=kb.get_json())
    except Exception:
        pass
    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_create_time",
)
async def admin_create_time_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    draft = admin_create_drafts.setdefault(event.user_id, {})
    draft["time_str"] = payload.get("time", "12:00")
    draft["step"] = "awaiting_client_info"

    curr_price = draft.get("price", 1000)
    curr_svc = draft.get("service", "Стрижка")
    day_key = draft.get("day_key", "")

    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_menu"}))

    try:
        await event.edit_message(
            message=(
                f"➕ Шаг 4 из 4: Данные клиента и цена\n\n"
                f"✂️ Услуга: {curr_svc}\n"
                f"⏰ Время: {day_key} {draft['time_str']}\n"
                f"💰 Цена: {curr_price} ₽\n\n"
                "👉 Отправьте в чат телефон и имя клиента:\n"
                "Например: +79991234567 Иван или просто номер 89991234567.\n\n"
                "Если хотите изменить цену, укажите её в конце сообщения: 89991234567 Иван 1200"
            ),
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_create_time_custom",
)
async def admin_create_time_custom_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    draft = admin_create_drafts.setdefault(event.user_id, {})
    draft["step"] = "awaiting_custom_time"

    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_create_start"}))

    try:
        await event.edit_message(
            message=f"✏️ Введите любое время записи на {draft.get('day_key')}\n\nНапишите в ответном сообщении (например 08:30 или 22:00):",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


# Schedule management
@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_sched",
)
async def admin_sched_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    offset = int(payload.get("offset", 0))
    all_days = get_upcoming_days_schedule(days_ahead=14)
    slice_days = all_days[offset:offset+4]

    text = (
        "🕒 Управление рабочим графиком мастера\n\n"
        "Выберите день, чтобы сделать его выходным/рабочим или изменить часы работы (прибавить/убавить начало и конец дня):"
    )
    try:
        await event.edit_message(
            message=text,
            keyboard=admin_schedule_keyboard(slice_days, offset, len(all_days)),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_sched_day",
)
async def admin_sched_day_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    day_key = payload.get("day_key", "")
    sched = get_master_day_schedule(day_key)

    st_txt = "🟢 Рабочий день" if sched["is_working"] else "🔴 Выходной день"
    card_text = (
        f"🗓 График на {day_key}:\n\n"
        f"• Статус: {st_txt}\n"
        f"• Рабочие часы: {sched['start_time']} — {sched['end_time']}\n\n"
        "Вы можете изменить статус дня или скорректировать рабочие часы:"
    )

    try:
        await event.edit_message(
            message=card_text,
            keyboard=admin_sched_day_keyboard(sched),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_sched_toggle",
)
async def admin_sched_toggle_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    day_key = payload.get("day_key", "")
    sched = get_master_day_schedule(day_key)
    new_work = not sched["is_working"]
    set_master_day_schedule(day_key, new_work, sched["start_time"], sched["end_time"])

    msg = "День отмечен как РАБОЧИЙ!" if new_work else "День отмечен как ВЫХОДНОЙ!"
    await safe_event_answer(event, snackbar=msg)
    await admin_sched_day_handler(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_sched_adj",
)
async def admin_sched_adj_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    day_key = payload.get("day_key", "")
    which = payload.get("which", "start")
    delta = int(payload.get("delta", 0))

    if which == "start":
        adjust_master_day_hours(day_key, start_delta_hours=delta, end_delta_hours=0)
    else:
        adjust_master_day_hours(day_key, start_delta_hours=0, end_delta_hours=delta)

    await safe_event_answer(event, snackbar="Часы обновлены")
    await admin_sched_day_handler(event)


# Existing referrals & rewards callbacks
@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_pending",
)
async def admin_pending_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    offset = int(payload.get("offset", 0))

    referrals_list, total = get_pending_referrals(limit=PAGE_SIZE, offset=offset)
    count_info = f" ({total} всего)" if total > 0 else ""

    text = (
        f"⏳ Ожидают подтверждения визита{count_info}:\n"
        "(нажми на нужного клиента, чтобы подтвердить его визит и начислить бонус другу)"
    )

    try:
        await event.edit_message(
            message=text,
            keyboard=pending_referrals_keyboard(referrals_list, offset, total),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_rewards",
)
async def admin_rewards_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    offset = int(payload.get("offset", 0))

    rewards_list, total = get_active_rewards(limit=PAGE_SIZE, offset=offset)
    count_info = f" ({total} всего)" if total > 0 else ""

    text = (
        f"💰 Активные бонусы клиентов{count_info}:\n"
        "(нажми на бонус, чтобы списать его при оплате клиентом)"
    )

    try:
        await event.edit_message(
            message=text,
            keyboard=active_rewards_keyboard(rewards_list, offset, total),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "ref_visit",
)
async def confirm_visit(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    referral_id = int(payload.get("id", 0))
    offset = int(payload.get("offset", 0))

    success, text, notify_data = mark_referral_visited(referral_id)

    if success and notify_data and notify_data.get("referrer_vk_id"):
        try:
            invited_name = notify_data["invited_first_name"]
            await event.ctx_api.messages.send(
                peer_id=notify_data["referrer_vk_id"],
                random_id=random.randint(1, 2**31 - 1),
                message=(
                    "🎉 Отличные новости!\n"
                    f"Твой друг {invited_name} посетил салон!\n"
                    f"Тебе начислен бонус: {notify_data['amount']} ₽.\n\n"
                    f"💰 Доступный баланс бонусов: {notify_data['new_balance']} ₽.\n"
                    "Ты можешь списать их при следующем визите к мастеру!"
                ),
            )
        except Exception:
            pass

    referrals_list, total = get_pending_referrals(limit=PAGE_SIZE, offset=offset)
    msg_text = f"ℹ️ {text}\n\n⏳ Ожидают подтверждения:"

    try:
        await event.edit_message(
            message=msg_text,
            keyboard=pending_referrals_keyboard(referrals_list, offset, total),
        )
    except Exception:
        pass

    await safe_event_answer(event, snackbar=text)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "reward_use",
)
async def use_reward(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    reward_id = int(payload.get("id", 0))
    offset = int(payload.get("offset", 0))

    success, text, notify_data = mark_reward_used(reward_id)

    if success and notify_data and notify_data.get("vk_id"):
        try:
            await event.ctx_api.messages.send(
                peer_id=notify_data["vk_id"],
                random_id=random.randint(1, 2**31 - 1),
                message=(
                    f"✂️ Бонус {notify_data['amount']} ₽ успешно списан в счет оплаты визита!\n"
                    f"💰 Оставшийся баланс бонусов: {notify_data['remaining_balance']} ₽.\n\n"
                    "Спасибо за визит, ждем снова!"
                ),
            )
        except Exception:
            pass

    rewards_list, total = get_active_rewards(limit=PAGE_SIZE, offset=offset)
    msg_text = f"ℹ️ {text}\n\n💰 Активные бонусы клиентов:"

    try:
        await event.edit_message(
            message=msg_text,
            keyboard=active_rewards_keyboard(rewards_list, offset, total),
        )
    except Exception:
        pass

    await safe_event_answer(event, snackbar=text)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "noop",
)
async def noop(event: MessageEvent):
    await safe_event_answer(event)


# Masters integration
@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_masters",
)
async def admin_masters_status_vk(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    session = get_active_masters_session()
    kb = Keyboard(inline=True)
    if session:
        text = (
            "✅ Синхронизация Masters подключена\n\n"
            f"📱 Номер мастера: {session['master_phone']}\n"
            "Записи из Masters автоматически синхронизируются каждые 10 минут, "
            "а клиенты получают напоминания о стрижке накануне вечером и за 2 часа.\n\n"
            "Вы можете запустить принудительную синхронизацию прямо сейчас:"
        )
        kb.add(Callback("🔄 Синхронизировать сейчас", {"cmd": "admin_masters_sync"}))
        kb.row()
        kb.add(Callback("🚪 Отключить аккаунт Masters", {"cmd": "admin_masters_logout"}))
        kb.row()
        kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
    else:
        suggested_phone = get_suggested_master_phone()
        text = (
            "🔗 Подключение аккаунта Masters\n\n"
            "Бот может автоматически забирать все записи из твоего личного кабинета Masters "
            "и бесплатно рассылать клиентам напоминания о визите накануне вечером и за 2 часа.\n\n"
            f"📱 Номер мастера для входа: {suggested_phone}\n\n"
            "Нажми кнопку ниже, чтобы отправить проверочный код по СМС, или укажи другой номер:"
        )
        kb.add(Callback(f"📲 СМС на {suggested_phone}", {"cmd": "admin_masters_request", "phone": suggested_phone}))
        kb.row()
        kb.add(Callback("✏️ Ввести другой номер", {"cmd": "admin_masters_enter_phone"}))
        kb.row()
        kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))

    try:
        await event.edit_message(message=text, keyboard=kb.get_json())
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_masters_enter_phone",
)
async def admin_masters_enter_phone_vk(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    user_id = event.user_id
    pending_master_logins[user_id] = {"awaiting_phone": True}
    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Назад", {"cmd": "admin_masters"}))
    try:
        await event.edit_message(
            message=(
                "📱 Введите номер телефона мастера\n\n"
                "Напишите в ответном сообщении номер, зарегистрированный в сервисе Masters "
                "(например, +79991234567):"
            ),
            keyboard=kb.get_json(),
        )
    except Exception:
        pass
    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_masters_request",
)
async def admin_masters_request_code_vk(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    user_id = event.user_id
    payload = get_payload(event)
    phone = payload.get("phone") or get_suggested_master_phone()

    if not phone:
        pending_master_logins[user_id] = {"awaiting_phone": True}
        kb = Keyboard(inline=True)
        kb.add(Callback("⬅️ Назад", {"cmd": "admin_masters"}))
        try:
            await event.edit_message(
                message="📱 Введите номер телефона мастера\n\nНапишите номер, зарегистрированный в сервисе Masters (например, +79991234567):",
                keyboard=kb.get_json(),
            )
        except Exception:
            pass
        await safe_event_answer(event)
        return

    success, msg, session_data = request_master_login(phone)
    if not success:
        pending_master_logins[user_id] = {"awaiting_phone": True}
        kb = Keyboard(inline=True)
        kb.add(Callback("✏️ Ввести номер вручную", {"cmd": "admin_masters_enter_phone"}))
        kb.row()
        kb.add(Callback("⬅️ Назад", {"cmd": "admin_masters"}))
        try:
            await event.edit_message(
                message=(
                    f"❌ {msg}\n\n"
                    "Проверьте номер или напишите другой номер телефона мастера в ответном сообщении:\n"
                    "(например, +79991234567)"
                ),
                keyboard=kb.get_json(),
            )
        except Exception:
            pass
        await safe_event_answer(event)
        return

    pending_master_logins[user_id] = session_data
    kb = Keyboard(inline=True)
    kb.add(Callback("✏️ Ввести другой номер", {"cmd": "admin_masters_enter_phone"}))
    kb.row()
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_masters"}))
    try:
        await event.edit_message(
            message=f"📩 Код подтверждения отправлен\n\nНа номер {session_data['phone_formatted']} отправлено СМС от Masters.\n\n👉 Напишите 4 цифры из СМС в ответном сообщении:",
            keyboard=kb.get_json(),
        )
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_masters_sync",
)
async def admin_masters_sync_now_vk(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    ok, count, msg = sync_masters_appointments()
    await safe_event_answer(event, snackbar=msg)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_masters_logout",
)
async def admin_masters_disconnect_vk(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    disconnect_masters_session()
    await safe_event_answer(event, snackbar="Синхронизация отключена")
    await admin_masters_status_vk(event)


# Broadcast flow
@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_broadcast",
)
async def admin_broadcast_start(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    admin_broadcast_drafts[event.user_id] = {"step": "awaiting_text"}

    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_broadcast_cancel"}))

    text = (
        "📢 Рассылка сообщений клиентам (VK)\n\n"
        "Напишите и отправьте в чат текст, который хотите разослать всем клиентам сообщества.\n\n"
        "После отправки бот покажет предпросмотр и попросит подтвердить рассылку перед отправкой."
    )
    try:
        await event.edit_message(message=text, keyboard=kb.get_json())
    except Exception:
        pass

    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_broadcast_cancel",
)
async def admin_broadcast_cancel(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return
    admin_broadcast_drafts.pop(event.user_id, None)
    await safe_event_answer(event, snackbar="Рассылка отменена")
    await admin_menu_handler(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_broadcast_send",
)
async def admin_broadcast_send(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    draft = admin_broadcast_drafts.pop(event.user_id, None)
    if not draft or not draft.get("text"):
        await event.show_snackbar("Черновик не найден")
        return

    text = draft["text"]
    recipients = get_broadcast_recipients("vk")
    total = len(recipients)

    await safe_event_answer(event)
    try:
        await event.edit_message(message=f"⏳ Отправка рассылки...\n0 из {total}")
    except Exception:
        pass

    success_count = 0
    fail_count = 0

    for uid in recipients:
        try:
            await event.ctx_api.messages.send(
                user_id=uid,
                random_id=random.randint(1, 2**31 - 1),
                message=text,
            )
            success_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.05)

    report = (
        "✅ Рассылка успешно завершена!\n\n"
        f"📬 Доставлено: {success_count}\n"
        f"⚠️ Не доставлено: {fail_count}\n"
        f"👥 Всего получателей: {total}"
    )
    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))

    try:
        await event.edit_message(message=report, keyboard=kb.get_json())
    except Exception:
        pass


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_clients",
)
async def admin_clients_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    action = payload.get("action")
    if action == "import":
        admin_clients_state[event.user_id] = {"action": "awaiting_import"}
        kb = Keyboard(inline=True)
        kb.add(Callback("⬅️ Назад", {"cmd": "admin_clients", "offset": 0}))
        msg = (
            "📥 Импорт базы клиентов из Masters\n\n"
            "Отправьте список клиентов сообщением в чат:\n"
            "Иван Иванов +79271234567\n"
            "Алексей 89053214455"
        )
        try:
            await event.edit_message(message=msg, keyboard=kb.get_json())
        except Exception:
            pass
        await safe_event_answer(event)
        return

    if action == "search":
        admin_clients_state[event.user_id] = {"action": "awaiting_search"}
        kb = Keyboard(inline=True)
        kb.add(Callback("⬅️ Назад", {"cmd": "admin_clients", "offset": 0}))
        try:
            await event.edit_message(message="🔍 Поиск клиента в базе\n\nОтправьте в чат имя или телефон клиента:", keyboard=kb.get_json())
        except Exception:
            pass
        await safe_event_answer(event)
        return

    offset = int(payload.get("offset", 0))
    stats = get_clients_stats()
    clients, total = get_clients_page(offset=offset, limit=4)

    text = f"👥 База клиентов ({stats['total']} всего, с телефонами: {stats['with_phone']}):\n\n"
    if not clients:
        text += "В базе пока нет клиентов. Нажмите «📥 Импорт», чтобы добавить контакты."
    else:
        for c in clients:
            c_name = f"{c['first_name'] or ''} {c['last_name'] or ''}".strip() or "Без имени"
            c_phone = c.get("phone") or "—"
            text += f"• {c_name} — {c_phone}\n"

    kb = Keyboard(inline=True)
    if offset > 0:
        kb.add(Callback("◀️ Назад", {"cmd": "admin_clients", "offset": max(0, offset - 4)}))
    if offset + 4 < total:
        kb.add(Callback("Вперёд ▶️", {"cmd": "admin_clients", "offset": offset + 4}))
    if offset > 0 or offset + 4 < total:
        kb.row()

    kb.add(Callback("📥 Импорт из Masters", {"cmd": "admin_clients", "action": "import"}))
    kb.add(Callback("🔍 Поиск", {"cmd": "admin_clients", "action": "search"}))
    kb.row()
    kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))

    try:
        await event.edit_message(message=text, keyboard=kb.get_json())
    except Exception:
        pass
    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_edit_client",
)
async def admin_edit_client_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    appt = get_appointment(appt_id)
    if not appt:
        await event.show_snackbar("Запись не найдена")
        return

    admin_edit_client_drafts[event.user_id] = {
        "appt_id": appt_id,
        "current_filter": current_filter,
        "offset": offset,
    }

    cur_name = appt.get("client_name") or "Клиент"
    cur_phone = appt.get("phone") or "—"

    text = (
        f"✏️ Изменение данных клиента для записи #{appt_id}\n\n"
        f"Текущее имя: {cur_name}\n"
        f"Текущий телефон: {cur_phone}\n\n"
        "👉 Отправьте в чат имя и/или номер телефона клиента:\n"
        "Например: Иван +79271234567 или просто номер +79271234567, или имя Иван\n\n"
        "Запись мгновенно обновится в базе данных."
    )
    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_appt_detail", "id": appt_id, "filter": current_filter, "offset": offset}))

    try:
        await event.edit_message(message=text, keyboard=kb.get_json())
    except Exception:
        pass
    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_edit_price",
)
async def admin_edit_price_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    appt = get_appointment(appt_id)
    if not appt:
        await event.show_snackbar("Запись не найдена")
        return

    cur_svc = appt.get("services") or "—"
    cur_price = appt.get("total_price") or 0
    cur_client = appt.get("client_name") or "Клиент"

    text = (
        f"💰 Редактирование услуги и стоимости для записи #{appt_id}\n\n"
        f"👤 Клиент: {cur_client}\n"
        f"✂️ Текущая услуга: {cur_svc}\n"
        f"💵 Текущая стоимость: {cur_price} ₽\n\n"
        "👉 Выберите услугу с ценой в 1 клик или введите свою цену:"
    )

    kb = Keyboard(inline=True)
    kb.add(Callback("💈 Стрижка — 2 300 ₽", {"cmd": "admin_set_price", "id": appt_id, "price": 2300, "svc": "hair", "filter": current_filter, "offset": offset}))
    kb.row()
    kb.add(Callback("🧔 Оформление бороды — 700 ₽", {"cmd": "admin_set_price", "id": appt_id, "price": 700, "svc": "beard", "filter": current_filter, "offset": offset}))
    kb.row()
    kb.add(Callback("💈+🧔 Стрижка+Борода — 3 000 ₽", {"cmd": "admin_set_price", "id": appt_id, "price": 3000, "svc": "combo", "filter": current_filter, "offset": offset}))
    kb.row()
    kb.add(Callback("✏️ Ввести свою цену", {"cmd": "admin_set_custom_price", "id": appt_id, "filter": current_filter, "offset": offset}))
    kb.row()
    kb.add(Callback("⬅️ Назад к карточке", {"cmd": "admin_appt_detail", "id": appt_id, "filter": current_filter, "offset": offset}))

    try:
        await event.edit_message(message=text, keyboard=kb.get_json())
    except Exception:
        pass
    await safe_event_answer(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_set_price",
)
async def admin_set_price_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    price = int(payload.get("price", 2300))
    svc_code = payload.get("svc", "hair")

    svc_map = {
        "hair": "Стрижка (МУЖСКАЯ)",
        "beard": "Оформление бороды",
        "combo": "Стрижка + Борода",
    }
    svc_name = svc_map.get(svc_code, "Стрижка")

    ok, msg, appt = update_appointment_price_and_service(appt_id, price, svc_name)
    await safe_event_answer(event, snackbar=f"Стоимость обновлена: {price} ₽")
    await admin_appt_detail_handler(event)


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_set_custom_price",
)
async def admin_set_custom_price_handler(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    payload = get_payload(event)
    appt_id = int(payload.get("id", 0))
    current_filter = payload.get("filter", "today")
    offset = int(payload.get("offset", 0))

    admin_edit_price_drafts[event.user_id] = {
        "appt_id": appt_id,
        "current_filter": current_filter,
        "offset": offset,
    }

    text = (
        f"✏️ Ввод своей стоимости для записи #{appt_id}\n\n"
        "Отправьте в чат новую стоимость в рублях (например: 700, 1500 или 2000):"
    )
    kb = Keyboard(inline=True)
    kb.add(Callback("⬅️ Отмена", {"cmd": "admin_edit_price", "id": appt_id, "filter": current_filter, "offset": offset}))

    try:
        await event.edit_message(message=text, keyboard=kb.get_json())
    except Exception:
        pass
    await safe_event_answer(event)


def _has_active_vk_draft(message: Message) -> bool:
    uid = message.from_id
    return (
        uid in pending_master_logins
        or uid in admin_broadcast_drafts
        or uid in admin_resched_drafts
        or uid in admin_create_drafts
        or uid in admin_edit_client_drafts
        or uid in admin_edit_price_drafts
        or uid in admin_clients_state
    )


# Text messages handler (SMS, Phone, Broadcast, Reschedule custom time, Create appointment)
@labeler.message(func=_has_active_vk_draft)
async def handle_vk_text_messages(message: Message):
    user_id = message.from_id
    raw_text = (message.text or "").strip()

    # 1. Master SMS code
    if user_id in pending_master_logins and raw_text.isdigit() and len(raw_text) == 4:
        session_data = pending_master_logins.get(user_id)
        if session_data and not session_data.get("awaiting_phone"):
            success, msg = confirm_master_login(session_data, raw_text)
            if not success:
                kb = Keyboard(inline=True)
                kb.add(Callback("✏️ Ввести другой номер", {"cmd": "admin_masters_enter_phone"}))
                kb.row()
                kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
                await message.answer(f"❌ {msg}\n\nПопробуйте ввести код ещё раз:", keyboard=kb.get_json())
                return
            pending_master_logins.pop(user_id, None)
            ok, count, sync_msg = sync_masters_appointments()
            await message.answer(
                "🎉 Аккаунт Masters успешно подключен!\n\n"
                f"✅ {sync_msg}\n"
                "Теперь бот будет автоматически отправлять клиентам напоминания накануне вечером и за 2 часа.",
                keyboard=admin_menu_keyboard(),
            )
            return

    # 2. Master phone input
    if user_id in pending_master_logins and pending_master_logins.get(user_id, {}).get("awaiting_phone"):
        if is_phone_number(raw_text):
            await message.answer("⏳ Запрашиваю проверочный код в Masters...")
            success, msg, session_data = request_master_login(raw_text)
            if not success:
                pending_master_logins[user_id] = {"awaiting_phone": True}
                kb = Keyboard(inline=True)
                kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
                await message.answer(
                    f"❌ {msg}\n\nПопробуйте ввести номер телефона мастера ещё раз (например, +79991234567):",
                    keyboard=kb.get_json(),
                )
                return
            pending_master_logins[user_id] = session_data
            kb = Keyboard(inline=True)
            kb.add(Callback("✏️ Ввести другой номер", {"cmd": "admin_masters_enter_phone"}))
            kb.row()
            kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
            await message.answer(
                f"📩 Код подтверждения отправлен\n\n"
                f"На номер {session_data['phone_formatted']} отправлено СМС от Masters.\n\n"
                "👉 Напишите 4 цифры из СМС в ответном сообщении:",
                keyboard=kb.get_json(),
            )
            return
        else:
            kb = Keyboard(inline=True)
            kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
            await message.answer(
                "⚠️ Пожалуйста, введите корректный номер телефона мастера (например, +79991234567):",
                keyboard=kb.get_json(),
            )
            return

    if not is_admin(user_id):
        return

    # 3. Client name edit on appointment
    if user_id in admin_edit_client_drafts:
        edit_draft = admin_edit_client_drafts.pop(user_id)
        appt_id = edit_draft["appt_id"]
        current_filter = edit_draft["current_filter"]
        offset = edit_draft["offset"]

        phone_match = re.search(r"(\+?[78][\s\-\(]*\d{3}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}|\b9\d{9}\b|\b\d{10,11}\b)", raw_text)
        client_phone = None
        if phone_match:
            client_phone = phone_match.group(1)
            client_name = (raw_text[:phone_match.start()] + " " + raw_text[phone_match.end():]).strip(" :;,-")
            client_name = re.sub(r"\s+", " ", client_name).strip()
        else:
            client_name = raw_text.strip()

        if not client_name:
            client_name = "Клиент"

        ok, msg = update_appointment_client_details(appt_id, client_name, client_phone)
        appt = get_appointment(appt_id) or {}
        fresh_name = appt.get("client_name") or client_name
        fresh_phone = appt.get("phone") or "—"

        kb = Keyboard(inline=True)
        kb.add(Callback("📋 К карточке", {"cmd": "admin_appt_detail", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.row()
        kb.add(Callback("⬅️ К записям", {"cmd": "admin_appts", "filter": current_filter, "offset": offset}))

        await message.answer(
            f"✅ Данные клиента обновлены!\n\n👤 Клиент: {fresh_name}\n📱 Телефон: {fresh_phone}",
            keyboard=kb.get_json(),
        )
        return

    # 3b. Price edit on appointment
    if user_id in admin_edit_price_drafts:
        p_draft = admin_edit_price_drafts.pop(user_id)
        appt_id = p_draft["appt_id"]
        current_filter = p_draft["current_filter"]
        offset = p_draft["offset"]

        price_digits = re.sub(r"\D", "", raw_text)
        if not price_digits or int(price_digits) <= 0:
            await message.answer("⚠️ Пожалуйста, введите корректную сумму в рублях (например: 700 или 1500):")
            admin_edit_price_drafts[user_id] = p_draft
            return

        new_price = int(price_digits)
        ok, res_msg, updated_appt = update_appointment_price_and_service(appt_id, new_price)

        kb = Keyboard(inline=True)
        kb.add(Callback("📋 К карточке", {"cmd": "admin_appt_detail", "id": appt_id, "filter": current_filter, "offset": offset}))
        kb.row()
        kb.add(Callback("⬅️ К записям", {"cmd": "admin_appts", "filter": current_filter, "offset": offset}))

        await message.answer(
            f"✅ Стоимость записи #{appt_id} успешно изменена на {new_price} ₽!",
            keyboard=kb.get_json(),
        )
        return

    # 4. Clients search / import text
    c_state = admin_clients_state.get(user_id)
    if c_state:
        action = c_state.get("action")
        if action == "awaiting_search":
            admin_clients_state.pop(user_id, None)
            clients, total = get_clients_page(offset=0, limit=4, search=raw_text)
            text = f"🔍 Результаты поиска «{raw_text}» (найдено: {total}):\n\n"
            if not clients:
                text += "Клиенты не найдены."
            else:
                for c in clients:
                    c_name = f"{c['first_name'] or ''} {c['last_name'] or ''}".strip() or "Без имени"
                    text += f"• {c_name} — {c.get('phone', '—')}\n"
            kb = Keyboard(inline=True)
            kb.add(Callback("🔍 Новый поиск", {"cmd": "admin_clients", "action": "search"}))
            kb.add(Callback("👥 Вся база", {"cmd": "admin_clients", "offset": 0}))
            await message.answer(text, keyboard=kb.get_json())
            return

        if action == "awaiting_import":
            admin_clients_state.pop(user_id, None)
            added, updated, msg = import_clients_from_text(raw_text)
            kb = Keyboard(inline=True)
            kb.add(Callback("👥 База клиентов", {"cmd": "admin_clients", "offset": 0}))
            kb.add(Callback("⬅️ Меню админки", {"cmd": "admin_menu"}))
            await message.answer(
                f"✅ Импорт клиентов завершён!\n\n• Добавлено новых клиентов: {added}\n• Обновлено: {updated}\n• {msg}",
                keyboard=kb.get_json(),
            )
            return

    # 5. Broadcast draft text
    if admin_broadcast_drafts.get(user_id, {}).get("step") == "awaiting_text":
        recipients = get_broadcast_recipients("vk")
        count = len(recipients)
        admin_broadcast_drafts[user_id] = {
            "step": "confirm",
            "text": raw_text,
        }
        kb = Keyboard(inline=True)
        kb.add(Callback(f"✅ Отправить ({count})", {"cmd": "admin_broadcast_send"}))
        kb.row()
        kb.add(Callback("✏️ Изменить текст", {"cmd": "admin_broadcast"}))
        kb.add(Callback("❌ Отмена", {"cmd": "admin_broadcast_cancel"}))

        preview = (
            "📢 Предпросмотр рассылки:\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{raw_text}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Получателей в VK: {count} чел.\n\n"
            "Отправить это сообщение всем клиентам?"
        )
        await message.answer(preview, keyboard=kb.get_json())
        return

    # 6. Reschedule custom time
    if admin_resched_drafts.get(user_id, {}).get("step") == "awaiting_custom_time":
        draft = admin_resched_drafts.pop(user_id)
        m = re.match(r"^(\d{1,2})[:.-](\d{2})$", raw_text)
        if not m:
            await message.answer("⚠️ Неверный формат. Введите время в формате 08:30 или 21:00:")
            admin_resched_drafts[user_id] = draft
            return

        h, mm = int(m.group(1)), int(m.group(2))
        time_str = f"{h:02d}:{mm:02d}"
        day_key = draft["day_key"]
        appt_id = draft["appt_id"]

        try:
            new_dt = datetime.datetime.strptime(f"{day_key} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=SARATOV_TZ)
        except Exception:
            await message.answer("Некорректная дата/время")
            return

        ok, msg, appt = reschedule_appointment(appt_id, new_dt)
        if ok and appt and appt.get("platform_user_id") and appt.get("platform") == "vk":
            try:
                await message.ctx_api.messages.send(
                    peer_id=appt["platform_user_id"],
                    random_id=random.randint(1, 2**31 - 1),
                    message=(
                        "🗓 Ваша запись перенесена мастером!\n\n"
                        f"⏰ Новое время: {format_dt(new_dt)}\n"
                        f"✂️ Услуга: {appt.get('services')}\n"
                        f"💰 Стоимость: {appt.get('total_price')} ₽\n\n"
                        "Ждем вас!"
                    ),
                )
            except Exception as e:
                logger.warning("Failed to notify client: %s", e)

        kb = Keyboard(inline=True)
        kb.add(Callback("📋 К календарю", {"cmd": "admin_appts", "filter": "today", "offset": 0}))
        await message.answer(f"✅ Запись #{appt_id} успешно перенесена на {format_dt(new_dt)}!", keyboard=kb.get_json())
        return

    # 7. Create appointment steps
    create_draft = admin_create_drafts.get(user_id)
    if create_draft:
        step = create_draft.get("step")

        if step == "awaiting_custom_service":
            create_draft["service"] = raw_text
            create_draft["price"] = 2300
            create_draft["step"] = "day"

            now = get_current_saratov_time()
            kb = Keyboard(inline=True)
            for i in range(5):
                d = now + datetime.timedelta(days=i)
                day_key = d.strftime("%Y-%m-%d")
                title = f"{RU_WEEKDAYS[d.weekday()]}, {d.day} {RU_MONTHS[d.month]}"
                kb.add(Callback(title, {"cmd": "admin_create_day", "day": day_key}))
                kb.row()
            kb.add(Callback("⬅️ Отмена", {"cmd": "admin_menu"}))
            await message.answer(f"✂️ Услуга: {raw_text}\n\nШаг 2 из 4: Выберите дату записи:", keyboard=kb.get_json())
            return

        if step == "awaiting_custom_time":
            m = re.match(r"^(\d{1,2})[:.-](\d{2})$", raw_text)
            if not m:
                await message.answer("⚠️ Неверный формат. Введите время в формате 08:30 или 22:00:")
                return
            h, mm = int(m.group(1)), int(m.group(2))
            create_draft["time_str"] = f"{h:02d}:{mm:02d}"
            create_draft["step"] = "awaiting_client_info"
            kb = Keyboard(inline=True)
            kb.add(Callback("⬅️ Отмена", {"cmd": "admin_menu"}))
            await message.answer(
                f"⏰ Время: {create_draft['time_str']}\n\n👉 Теперь отправьте телефон и имя клиента (например: +79991234567 Иван или с ценой: 89991234567 Иван 1200):",
                keyboard=kb.get_json(),
            )
            return

        if step == "awaiting_client_info":
            phone = ""
            custom_price = create_draft.get("price", 2300)

            for token in tokens:
                digits = re.sub(r"\D", "", token)
                if len(digits) >= 10:
                    phone = normalize_phone(token)
                elif token.isdigit() and int(token) >= 100:
                    custom_price = int(token)

            if not phone:
                await message.answer("⚠️ Пожалуйста, укажите номер телефона клиента (например: +79991234567 Иван):")
                return

            name_parts = []
            for t in tokens:
                digits = re.sub(r"\D", "", t)
                if len(digits) >= 10:
                    continue
                if t.isdigit() and (int(t) >= 100 or int(t) == custom_price):
                    continue
                name_parts.append(t)

            name = " ".join(name_parts) if name_parts else "Клиент"

            create_draft["phone"] = phone
            create_draft["name"] = name
            create_draft["price"] = custom_price

            day_key = create_draft["day_key"]
            time_str = create_draft["time_str"]
            dt_obj = datetime.datetime.strptime(f"{day_key} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=SARATOV_TZ)

            ok, res_msg, appt_id = await asyncio.to_thread(
                create_manual_appointment,
                phone=phone,
                appointment_time=dt_obj,
                services=create_draft["service"],
                total_price=custom_price,
                client_name=name,
                platform="manual",
            )

            if ok and appt_id in pending_manual_bookings:
                create_draft["step"] = "awaiting_masters_sms"
                create_draft["appt_id"] = appt_id
            else:
                admin_create_drafts.pop(user_id, None)

            if ok:
                kb = Keyboard(inline=True)
                kb.add(Callback("📋 К календарю", {"cmd": "admin_appts", "filter": "today", "offset": 0}))
                kb.add(Callback("➕ Создать ещё", {"cmd": "admin_create_start"}))
                kb.row()
                kb.add(Callback("⬅️ Главное меню", {"cmd": "admin_menu"}))
                await message.answer(
                    f"🎉 {res_msg}\n\n"
                    f"👤 Клиент: {name} ({phone})\n"
                    f"⏰ Дата и время: {format_dt(dt_obj)}\n"
                    f"✂️ Услуга: {create_draft['service']}\n"
                    f"💰 Стоимость: {custom_price} ₽",
                    keyboard=kb.get_json(),
                )
            else:
                await message.answer(f"❌ Ошибка создания записи: {res_msg}")
            return

        if step == "awaiting_masters_sms":
            code = raw_text.strip()
            if not code.isdigit() or len(code) != 4:
                await message.answer("⚠️ Код из СМС должен содержать 4 цифры. Перешли код, который получил клиент:")
                return

            appt_id = create_draft.get("appt_id")
            ok_sms, msg_sms = await asyncio.to_thread(
                confirm_manual_appointment_masters_sms, appt_id, code
            )
            admin_create_drafts.pop(user_id, None)
            if ok_sms:
                await message.answer(
                    f"✅ {msg_sms}",
                )
            else:
                await message.answer(
                    f"⚠️ {msg_sms}\n\nЗапись остаётся в боте. Код можно запросить у клиента и отправить ещё раз."
                )
            return
