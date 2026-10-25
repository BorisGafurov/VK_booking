import asyncio
import datetime
import logging
from vkbottle import API, Callback, Keyboard, OpenLink

from config import ADMIN_IDS
from services.appointments import (
    SARATOV_TZ,
    get_due_2h_reminders,
    get_due_evening_reminders,
    get_due_retention_reminders,
    mark_reminder_sent,
)

logger = logging.getLogger(__name__)


def _get_evening_reminder_keyboard(appointment_id: int) -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("❌ Отменить запись", {"cmd": "appt_cancel", "id": appointment_id}))
    kb.row()
    kb.add(OpenLink(link="https://yandex.ru/maps/-/CDu~v-7x", label="📍 Как добраться"))
    return kb.get_json()


def _get_retention_keyboard() -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("✂️ Записаться онлайн", {"cmd": "book_services"}))
    return kb.get_json()


def _resolve_platform_user_id(appt: dict, platform: str) -> int | None:
    user_id = appt.get("platform_user_id")
    if user_id and user_id != 0:
        return user_id

    from database import get_connection
    with get_connection() as conn:
        col = {"telegram": "telegram_id", "vk": "vk_id", "max": "max_user_id"}.get(platform)
        if not col:
            return None
        row = None
        if appt.get("user_id"):
            row = conn.execute(f"SELECT {col} FROM users WHERE id = %s", (appt["user_id"],)).fetchone()
        if not row and appt.get("phone"):
            row = conn.execute(f"SELECT {col} FROM users WHERE phone = %s", (appt["phone"],)).fetchone()

        if row and row[col]:
            found_id = row[col]
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
                    "Если твои планы изменились, пожалуйста, нажми кнопку ниже:"
                )
                try:
                    await api.messages.send(
                        user_id=target_user_id,
                        message=text,
                        keyboard=_get_evening_reminder_keyboard(appt["id"]),
                        random_id=0,
                    )
                    mark_reminder_sent(appt["id"], "evening")
                    logger.info("Отправлено вечернее напоминание клиенту #%s (appt #%s)", target_user_id, appt["id"])
                except Exception as send_err:
                    logger.warning("Ошибка отправки вечернего напоминания #%s: %s", appt["id"], send_err)

            # 2. 2h express reminder
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

                text = (
                    "⏰ До твоей записи осталось 2 часа!\n\n"
                    f"Ждём тебя сегодня в {time_str}.\n"
                    f"✂️ Услуги: {appt['services']}\n"
                    "📍 Адрес: Саратов, ул. имени Е.И. Пугачёва, 110\n\n"
                    "До скорой встречи!"
                )
                try:
                    await api.messages.send(
                        user_id=target_user_id,
                        message=text,
                        random_id=0,
                    )
                    mark_reminder_sent(appt["id"], "2h")
                    logger.info("Отправлено 2ч напоминание клиенту #%s (appt #%s)", target_user_id, appt["id"])
                except Exception as send_err:
                    logger.warning("Ошибка отправки 2ч напоминания #%s: %s", appt["id"], send_err)

            # 3. 2.5 weeks retention reminder
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
