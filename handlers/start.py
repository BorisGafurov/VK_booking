import json
import logging
import random
import re

from vkbottle.bot import BotLabeler, Message

from config import ADMIN_IDS
from keyboards.menu import get_main_menu
from services.referrals import register_referral, save_user

logger = logging.getLogger(__name__)
labeler = BotLabeler()


def extract_ref_id(message: Message) -> int | None:
    """Extract referrer VK ID from message.ref or message text."""
    if message.ref:
        ref_clean = str(message.ref).strip()
        if ref_clean.startswith("ref_"):
            val = ref_clean[4:]
            if val.isdigit():
                return int(val)
        elif ref_clean.isdigit():
            return int(ref_clean)

    if message.text:
        match = re.search(r"ref_(\d+)", message.text)
        if match:
            return int(match.group(1))

    return None


def is_start_message(message: Message) -> bool:
    """Check if the incoming message is a start/greeting or referral link entry."""
    if message.ref:
        return True

    if message.payload:
        try:
            payload = json.loads(message.payload)
            if isinstance(payload, dict) and payload.get("command") == "start":
                return True
        except Exception:
            if "start" in message.payload.lower():
                return True

    if message.text:
        text = message.text.strip().lower()
        if text in {
            "/start",
            "start",
            "начать",
            "старт",
            "меню",
            "главное меню",
            "привет",
            "здравствуйте",
            "хай",
        }:
            return True
        if re.search(r"ref_\d+", text):
            return True

    return False


@labeler.message(func=is_start_message)
async def start_handler(message: Message):
    user_info = None
    try:
        user_info = await message.get_user(fields=["domain"])
    except Exception as e:
        logger.warning("Не удалось получить профиль пользователя %s: %s", message.from_id, e)

    first_name = user_info.first_name if user_info else ""
    last_name = user_info.last_name if user_info else ""
    domain = user_info.domain if user_info else None

    user_db_id, is_new = save_user(
        vk_id=message.from_id,
        domain=domain,
        first_name=first_name,
        last_name=last_name,
    )

    greeting_name = f", {first_name}" if first_name else ""
    text = f"Привет{greeting_name}! Выбери нужный раздел:"
    extra_text = ""

    referrer_vk_id = extract_ref_id(message)
    if referrer_vk_id:
        if not is_new:
            extra_text = (
                "\n\nℹ️ Вы уже были зарегистрированы в боте ранее. "
                "Реферальный бонус начисляется только за приглашение новых пользователей."
            )
        else:
            success, result_text, notify_data = register_referral(
                new_user_vk_id=message.from_id,
                referrer_vk_id=referrer_vk_id,
            )

            if success:
                extra_text = "\n\n🎁 Вы пришли по приглашению. Реферальная связь сохранена!"
                if notify_data:
                    try:
                        invited_name = notify_data["invited_first_name"]
                        await message.ctx_api.messages.send(
                            peer_id=notify_data["referrer_vk_id"],
                            random_id=random.randint(1, 2**31 - 1),
                            message=(
                                f"👋 По вашей ссылке зарегистрировался друг: {invited_name}!\n"
                                f"После его первого визита в салон вам начислится бонус."
                            ),
                        )
                    except Exception as e:
                        logger.warning("Не удалось уведомить пригласившего: %s", e)
            else:
                extra_text = f"\n\n{result_text}"

    is_admin = message.from_id in ADMIN_IDS
    await message.answer(
        text + extra_text,
        keyboard=get_main_menu(is_admin=is_admin),
    )
