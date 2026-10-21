import json
import logging
import random
from typing import Any

from vkbottle import Callback, GroupEventType, Keyboard
from vkbottle.bot import BotLabeler, Message, MessageEvent

from config import ADMIN_IDS, REFERRAL_REWARD_TEXT
from services.referrals import (
    build_referral_link,
    get_active_rewards,
    get_pending_referrals,
    get_referral_stats,
    mark_referral_visited,
    mark_reward_used,
    save_user,
)

logger = logging.getLogger(__name__)
labeler = BotLabeler()

PAGE_SIZE = 5


def get_payload(event: MessageEvent) -> dict[str, Any]:
    """Safely extract payload dictionary from MessageEvent."""
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
    return user_id in ADMIN_IDS


def admin_menu_keyboard() -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("⏳ Ожидают подтверждения", {"cmd": "admin_pending", "offset": 0}))
    kb.row()
    kb.add(Callback("💰 Активные бонусы", {"cmd": "admin_rewards", "offset": 0}))
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

    # Pagination row
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

    # Pagination row
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

    await message.answer(
        "⚙️ Админ-панель:",
        keyboard=admin_menu_keyboard(),
    )


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_menu",
)
async def admin_menu(event: MessageEvent):
    if not is_admin(event.user_id):
        await event.show_snackbar("Нет доступа.")
        return

    try:
        await event.edit_message(
            message="⚙️ Админ-панель:",
            keyboard=admin_menu_keyboard(),
        )
    except Exception as e:
        logger.warning("Ошибка при открытии админ-меню: %s", e)

    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_pending",
)
async def admin_pending(event: MessageEvent):
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
    except Exception as e:
        logger.warning("Ошибка при открытии списка ожидающих визита: %s", e)

    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "admin_rewards",
)
async def admin_rewards(event: MessageEvent):
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
    except Exception as e:
        logger.warning("Ошибка при открытии активных бонусов: %s", e)

    await event.send_empty_answer()


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

    if not referral_id:
        await event.show_snackbar("Некорректный ID")
        return

    success, text, notify_data = mark_referral_visited(referral_id)

    if success and notify_data:
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
        except Exception as e:
            logger.warning("Не удалось отправить уведомление рефереру: %s", e)

    referrals_list, total = get_pending_referrals(limit=PAGE_SIZE, offset=offset)
    msg_text = f"ℹ️ {text}\n\n⏳ Ожидают подтверждения:"

    try:
        await event.edit_message(
            message=msg_text,
            keyboard=pending_referrals_keyboard(referrals_list, offset, total),
        )
    except Exception as e:
        logger.warning("Ошибка при обновлении списка рефералов: %s", e)

    await event.show_snackbar(text[:60])


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

    if not reward_id:
        await event.show_snackbar("Некорректный ID")
        return

    success, text, notify_data = mark_reward_used(reward_id)

    if success and notify_data:
        try:
            await event.ctx_api.messages.send(
                peer_id=notify_data["user_vk_id"],
                random_id=random.randint(1, 2**31 - 1),
                message=(
                    f"✂️ Бонус {notify_data['amount']} ₽ успешно списан в счет оплаты визита!\n"
                    f"💰 Оставшийся баланс бонусов: {notify_data['remaining_balance']} ₽.\n\n"
                    "Спасибо за визит, ждем снова!"
                ),
            )
        except Exception as e:
            logger.warning("Не удалось отправить клиенту уведомление о списании: %s", e)

    rewards_list, total = get_active_rewards(limit=PAGE_SIZE, offset=offset)
    msg_text = f"ℹ️ {text}\n\n💰 Активные бонусы клиентов:"

    try:
        await event.edit_message(
            message=msg_text,
            keyboard=active_rewards_keyboard(rewards_list, offset, total),
        )
    except Exception as e:
        logger.warning("Ошибка при обновлении списка бонусов: %s", e)

    await event.show_snackbar(text[:60])


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "noop",
)
async def noop_handler(event: MessageEvent):
    await event.send_empty_answer()
