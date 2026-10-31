import asyncio
import datetime
import logging
import os
import secrets
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
from services.reviews import (
    get_pending_notifications,
    mark_notification_failed,
    mark_notification_sent,
    mark_notification_skipped,
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


def _get_review_invite_keyboard(appointment_id: int) -> str:
    kb = Keyboard(inline=True)
    for n in range(1, 6):
        kb.add(Callback(f"⭐ {n}", {"cmd": "review_rate", "id": appointment_id, "rating": n}))
    return kb.get_json()


MASTER_TELEGRAM_IDS = {
    int(x.strip()) for x in (os.getenv("MASTER_TELEGRAM_IDS") or "").split(",") if x.strip().isdigit()
}
MASTER_VK_IDS = {
    int(x.strip()) for x in (os.getenv("MASTER_VK_IDS") or "").split(",") if x.strip().isdigit()
}
MASTER_MAX_IDS = {
    int(x.strip()) for x in (os.getenv("MASTER_MAX_IDS") or "").split(",") if x.strip().isdigit()
}
MASTER_USER_IDS = {
    int(x.strip()) for x in (os.getenv("MASTER_USER_IDS") or "").split(",") if x.strip().isdigit()
}


def _is_master_or_admin(user_id: int, platform: str) -> bool:
    if not user_id:
        return False
    if user_id in ADMIN_IDS:
        return True
    if platform == "telegram" and user_id in MASTER_TELEGRAM_IDS:
        return True
    if platform == "vk" and user_id in MASTER_VK_IDS:
        return True
    if platform == "max" and user_id in MASTER_MAX_IDS:
        return True
    if user_id in MASTER_USER_IDS:
        return True
    try:
        from services.referrals import is_master_user, is_admin_user
        if is_master_user(user_id, platform=platform) or is_admin_user(user_id, platform=platform):
            return True
    except Exception:
        pass
    return False


def _resolve_platform_user_id(appt: dict, platform: str) -> int | None:
    from services.referrals import is_master_phone, normalize_phone

    raw_phone = appt.get("phone") or ""
    norm_phone = normalize_phone(raw_phone)
    if is_master_phone(norm_phone):
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
            if is_master_phone(row_phone):
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
                        random_id=secrets.randbits(31),
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
                        random_id=secrets.randbits(31),
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
                        random_id=secrets.randbits(31),
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
                        random_id=secrets.randbits(31),
                    )
                    mark_reminder_sent(appt["id"], "retention")
                    logger.info("Отправлено retention напоминание клиенту #%s (appt #%s)", target_user_id, appt["id"])
                except Exception as send_err:
                    logger.warning("Ошибка отправки retention напоминания #%s: %s", appt["id"], send_err)

            # 5. Review invitations (sent when master confirms payment in CRM)
            invites = get_pending_notifications(platform, "review_invite")
            for invite in invites:
                target_user_id = invite.get("recipient_id")
                if not target_user_id:
                    mark_notification_skipped(invite["id"], "missing_recipient")
                    continue

                if invite.get("existing_review_id"):
                    mark_notification_skipped(invite["id"], "already_reviewed")
                    continue

                services_name = invite.get("services") or "Стрижка"
                text = (
                    "✂️ Спасибо, что посетил салон Срез!\n\n"
                    f"Визит: {services_name}\n\n"
                    "Надеемся, стрижка принесла удовольствие и отличный настрой. "
                    "Пожалуйста, оцени качество визита от 1 до 5 звёзд:"
                )
                try:
                    await api.messages.send(
                        user_id=target_user_id,
                        message=text,
                        keyboard=_get_review_invite_keyboard(invite["appointment_id"]),
                        random_id=secrets.randbits(31),
                    )
                    mark_notification_sent(invite["id"])
                    logger.info("Отправлено приглашение оставить отзыв клиенту VK #%s (appt #%s)", target_user_id, invite["appointment_id"])
                except Exception as send_err:
                    logger.warning("Ошибка отправки приглашения к отзыву VK #%s: %s", invite["id"], send_err)
                    mark_notification_failed(invite["id"], str(send_err))

            # 6. Master new review notifications (sent to admin/master VK)
            master_alerts = get_pending_notifications(platform, "master_new_review")
            for alert in master_alerts:
                rating = alert.get("rating") or 5
                stars = "⭐" * int(rating)
                client_name = alert.get("first_name") or "Клиент"
                phone = alert.get("phone") or "—"
                services_str = alert.get("services") or "Визит"
                comment = alert.get("comment") or ""
                alert_text = (
                    f"⭐ Новый отзыв: {rating}/5 ({stars})\n\n"
                    f"👤 Клиент: {client_name}\n"
                    f"📱 Телефон: {phone}\n"
                    f"✂️ Услуга: {services_str}\n\n"
                    f"«{comment}»\n\n"
                    "🌐 Отзыв опубликован на сайте srezforma.ru"
                )
                admin_recipients = set(ADMIN_IDS) | set(MASTER_VK_IDS)
                for admin_id in admin_recipients:
                    try:
                        await api.messages.send(
                            user_id=admin_id,
                            message=alert_text,
                            random_id=secrets.randbits(31),
                        )
                    except Exception as a_err:
                        logger.warning("Не удалось уведомить админа VK %s о новом отзыве: %s", admin_id, a_err)
                mark_notification_sent(alert["id"])

        except Exception as e:
            logger.error("Ошибка в цикле reminder worker VK: %s", e)

        await asyncio.sleep(60)
