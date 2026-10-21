import asyncio
import logging
import os
import sys

import certifi

# Ensure SSL certificates are properly loaded on macOS/Linux
if "SSL_CERT_FILE" not in os.environ:
    os.environ["SSL_CERT_FILE"] = certifi.where()

from vkbottle import Bot
from vkbottle.bot import BotLabeler, Message

import config
from config import ADMIN_IDS, VK_TOKEN
from database import init_db
from handlers.advice import labeler as advice_labeler
from handlers.booking import labeler as booking_labeler
from handlers.navigation import labeler as navigation_labeler
from handlers.referrals import labeler as referrals_labeler
from handlers.start import labeler as start_labeler
from keyboards.menu import get_main_menu

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

fallback_labeler = BotLabeler()


@fallback_labeler.message()
async def fallback_handler(message: Message):
    is_admin = message.from_id in ADMIN_IDS
    await message.answer(
        "Пожалуйста, выбери нужный раздел с помощью меню ниже 👇",
        keyboard=get_main_menu(is_admin=is_admin),
    )


async def main():
    if not VK_TOKEN:
        print("\n[ОШИБКА] Не указан VK_TOKEN в файле .env!")
        print("Создайте файл .env (на основе .env.example) и укажите токен сообщества ВКонтакте.\n")
        sys.exit(1)

    init_db()

    bot = Bot(token=VK_TOKEN)

    # Automatically resolve community ID and screen_name if not provided in .env
    try:
        group_resp = await bot.api.groups.get_by_id()
        if group_resp.groups:
            group = group_resp.groups[0]
            if not config.GROUP_ID:
                config.GROUP_ID = group.id
            if not config.GROUP_DOMAIN:
                config.GROUP_DOMAIN = group.screen_name
            logger.info(
                "Подключено к сообществу: %s (id: %s, domain: %s)",
                group.name,
                group.id,
                group.screen_name,
            )
    except Exception as e:
        logger.warning("Не удалось автоматически определить данные сообщества: %s", e)

    # Load handlers in order
    bot.labeler.load(start_labeler)
    bot.labeler.load(booking_labeler)
    bot.labeler.load(navigation_labeler)
    bot.labeler.load(advice_labeler)
    bot.labeler.load(referrals_labeler)
    bot.labeler.load(fallback_labeler)

    logger.info("VK-бот успешно запущен. Нажми Ctrl+C для остановки.")
    await bot.run_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
