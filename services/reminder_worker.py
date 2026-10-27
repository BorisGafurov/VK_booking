import asyncio
import datetime
import logging
from vkbottle import API, Callback, Keyboard, OpenLink

from config import ADMIN_IDS
from services.appointments import (
    SARATOV_TZ,
    get_due_2h_reminders,
    get_due_confirmation_followup_reminders,
    get_due_evening_reminders,
    get_due_retention_reminders,
    mark_reminder_sent,
)

logger = logging.getLogger(__name__)


def _get_confirmation_keyboard(appointment_id: int) -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("✅ Подтверждаю запись", {"cmd": "appt_confirm", "id": appointment_id}))
    kb.row()
    kb.add(Callback("❌ Отменить запись", {"cmd": "appt_cancel", "id": appointment_id}))
    kb.row()
    kb.add(OpenLink(link="https://yandex.ru/maps/-/CDu~v-7x", label="📍 Как добраться"))
    return kb.get_json()


def _get_confirmed_2h_keyboard(appointment_id: int) -> str:
    kb = Keyboard(inline=True)
    kb.add(OpenLink(link="https://yandex.ru/maps/-/CDu~v-7x", label="📍 Как добраться"))
    kb.row()
    kb.add(Callback("❌ Отменить запись", {"cmd": "appt_cancel", "id": appointment_id}))
    return kb.get_json()


def _get_retention_keyboard() -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("✂️ Записаться онлайн", {"cmd": "book_services"}))
    return kb.get_json()


MASTER_PHONES = {"+79173225155", "79173225155", "89173225155"}
MASTER_USER_IDS = {1010373026, 90599236, 503925574}


def _is_master_or_admin(user_id: int, platform: str) -> bool:
    if not user_id:
        return False
    if user_id in ADMIN_IDS or user_id in MASTER_USER_IDS:
        return True
    try:
        from services.referrals import is_master_user, is_admin_user
        if is_master_user(user_id, platform=platform) or is_admin_user(user_id, platform=platform):
            return True
    except Exception:
        pass
    return False


def _resolve_platform_user_id(appt: dict, platform: str) -> int | None:
    from services.referrals import normalize_phone

    raw_phone = appt.get("phone") or ""
    norm_phone = normalize_phone(raw_phone)
    if norm_phone in MASTER_PHONES or norm_phone == "+79173225155":
        return None

    user_id = appt.get("platform_user_id")
    if user_id and user_id != 0:
        if _is_master_or_admin(user_id, platform):
            return None
        return user_id

    from database import get_connection
    with get_connection() as conn:
        col = {"telegram": "telegram_id", "vk": "vk_id", "max": "max_user_id"}.get(platform)
        if not col:
            return None
        row = None
        if appt.get("user_id"):
            row = conn.execute(f"SELECT id, phone, {col} FROM users WHERE id = %s", (appt["user_id"],)).fetchone()
        if not row and norm_phone:
            row = conn.execute(f"SELECT id, phone, {col} FROM users WHERE phone = %s", (norm_phone,)).fetchone()

        if row:
            row_phone = normalize_phone(row["phone"] or "")
            if row_phone in MASTER_PHONES or row_phone == "+79173225155":
                return None
            if row[col]:
                found_id = row[col]
                if _is_master_or_admin(found_id, platform):
                    return None
                conn.execute(
                    "UPDATE appointments SET platform_user_id = %s, platform = %s WHERE id = %s",
                    (found_id, platform, appt["id"]),
                )
                conn.commit()
                return found_id
    return None


async def run_reminder_worker(api: API, platform: str = "vk"):
    logger.info("Запущен фоновый воркер напоминаний (%s)", platform)

    while True:
        try:
            # 1. Evening reminder (day before visit, >= 18:00)
            evening_due = get_due_evening_reminders(platform=platform)
            for appt in evening_due:
                target_user_id = _resolve_platform_user_id(appt, platform)
                if not target_user_id:
                    mark_reminder_sent(appt["id"], "evening")
                    continue

                dt = appt["appointment_time"]
                if isinstance(dt, str):
                    dt = datetime.datetime.fromisoformat(dt)
                dt_local = dt.astimezone(SARATOV_TZ) if dt.tzinfo else dt
                date_str = dt_local.strftime("%d.%m.%Y")
                time_str = dt_local.strftime("%H:%M")
                weekdays = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
                weekday_str = weekdays[dt_local.weekday()]

                text = (
                    "🔔 Напоминание о записи к мастеру Борису Гафурову\n\n"
                    f"Завтра в {weekday_str} ({date_str}) в {time_str} ждём тебя на стрижку!\n"
                    f"✂️ Услуги: {appt['services']}\n"
                    f"💰 Стоимость: {appt['total_price']} ₽\n"
                    "📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110\n\n"
                    "👉 Пожалуйста, подтверди свой визит нажатием кнопки ниже или отмени запись, если твои планы изменились:"
                )
                try:
                    await api.messages.send(
                        user_id=target_user_id,
                        message=text,
                        keyboard=_get_confirmation_keyboard(appt["id"]),
                        random_id=0,
                    )
                    mark_reminder_sent(appt["id"], "evening")
                    logger.info("Отправлено вечернее напоминание клиенту #%s (appt #%s)", target_user_id, appt["id"])
                except Exception as send_err:
                    logger.warning("Ошибка отправки вечернего напоминания #%s: %s", appt["id"], send_err)

            # 2. Morning follow-up reminder (on visit day, >= 09:00, if not confirmed)
            morning_due = get_due_confirmation_followup_reminders(platform=platform)
            for appt in morning_due:
                target_user_id = _resolve_platform_user_id(appt, platform)
                if not target_user_id:
                    mark_reminder_sent(appt["id"], "morning")
                    continue

                dt = appt["appointment_time"]
                if isinstance(dt, str):
                    dt = datetime.datetime.fromisoformat(dt)
                dt_local = dt.astimezone(SARATOV_TZ) if dt.tzinfo else dt
                time_str = dt_local.strftime("%H:%M")

                text = (
                    "⚠️ Подтверждение записи на сегодня\n\n"
                    f"Ты записан сегодня в {time_str}, но ещё не подтвердил свой визит.\n\n"
                    f"✂️ Услуги: {appt['services']}\n"
                    f"💰 Стоимость: {appt['total_price']} ₽\n"
                    "📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110\n\n"
                    "👉 Пожалуйста, подтверди запись, чтобы мастер держал за тобой время, или отмени её, если не сможешь прийти:"
                )
                try:
                    await api.messages.send(
                        user_id=target_user_id,
                        message=text,
                        keyboard=_get_confirmation_keyboard(appt["id"]),
                        random_id=0,
                    )
                    mark_reminder_sent(appt["id"], "morning")
                    logger.info("Отправлено утреннее напоминание о подтверждении клиенту #%s (appt #%s)", target_user_id, appt["id"])
                except Exception as send_err:
                    logger.warning("Ошибка отправки утреннего напоминания #%s: %s", appt["id"], send_err)

            # 3. 2h express reminder
            two_h_due = get_due_2h_reminders(platform=platform)
            for appt in two_h_due:
                target_user_id = _resolve_platform_user_id(appt, platform)
                if not target_user_id:
                    mark_reminder_sent(appt["id"], "2h")
                    continue

                dt = appt["appointment_time"]
                if isinstance(dt, str):
                    dt = datetime.datetime.fromisoformat(dt)
                dt_local = dt.astimezone(SARATOV_TZ) if dt.tzinfo else dt
                time_str = dt_local.strftime("%H:%M")

                is_confirmed = bool(appt.get("client_confirmed"))
                if is_confirmed:
                    text = (
                        "⏰ До твоей записи осталось 2 часа!\n\n"
                        f"Ждём тебя сегодня в {time_str}.\n"
                        f"✂️ Услуги: {appt['services']}\n"
                        "📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110\n\n"
                        "Твоя запись подтверждена, мастер готов и ждёт тебя! ✂️"
                    )
                    keyboard = _get_confirmed_2h_keyboard(appt["id"])
                else:
                    text = (
                        "⏰ До твоей записи осталось 2 часа!\n\n"
                        f"Ждём тебя сегодня в {time_str}.\n"
                        f"✂️ Услуги: {appt['services']}\n"
                        "📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110\n\n"
                        "⚠️ Внимание: запись всё ещё не подтверждена!\n"
                        "Пожалуйста, подтверди визит или отмени запись, чтобы мастер не ждал впустую:"
                    )
                    keyboard = _get_confirmation_keyboard(appt["id"])

                try:
                    await api.messages.send(
                        user_id=target_user_id,
                        message=text,
                        keyboard=keyboard,
                        random_id=0,
                    )
                    mark_reminder_sent(appt["id"], "2h")
                    logger.info("Отправлено 2ч напоминание клиенту #%s (appt #%s, confirmed=%s)", target_user_id, appt["id"], is_confirmed)
                except Exception as send_err:
                    logger.warning("Ошибка отправки 2ч напоминания #%s: %s", appt["id"], send_err)

            # 4. 2.5 weeks retention reminder
            retention_due = get_due_retention_reminders(platform=platform)
            for appt in retention_due:
                target_user_id = _resolve_platform_user_id(appt, platform)
                if not target_user_id:
                    mark_reminder_sent(appt["id"], "retention")
                    continue

                text = (
                    "✂️ Привет! Прошло 2,5 недели с твоей стрижки\n\n"
                    "Волосы уже отросли, и форма начинает теряться. Самое время освежить стрижку и бороду! 💈\n\n"
                    "Нажми кнопку ниже, чтобы занять удобное время на этой неделе:"
                )
                try:
                    await api.messages.send(
                        user_id=target_user_id,
                        message=text,
                        keyboard=_get_retention_keyboard(),
                        random_id=0,
                    )
                    mark_reminder_sent(appt["id"], "retention")
                    logger.info("Отправлено retention напоминание клиенту #%s (appt #%s)", target_user_id, appt["id"])
                except Exception as send_err:
                    logger.warning("Ошибка отправки retention напоминания #%s: %s", appt["id"], send_err)

        except Exception as e:
            logger.error("Ошибка в цикле reminder worker VK: %s", e)

        await asyncio.sleep(60)
