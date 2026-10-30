from vkbottle.bot import BotLabeler, Message

from config import (
    SALON_ADDRESS,
    SALON_LATITUDE,
    SALON_LONGITUDE,
    WORK_HOURS,
)
from keyboards.navigation import get_navigation_keyboard

labeler = BotLabeler()


@labeler.message(
    text=[
        "📍 Как добраться",
        "Как добраться",
        "как добраться",
        "/location",
        "/address",
        "/map",
        "адрес",
        "Адрес",
        "где вы",
        "карта",
    ]
)
async def navigation_handler(message: Message):
    text = (
        f"📍 Адрес салона:\n{SALON_ADDRESS}\n\n"
        f"⏰ Часы работы:\n{WORK_HOURS}"
    )

    await message.answer(
        text,
        lat=SALON_LATITUDE,
        long=SALON_LONGITUDE,
        keyboard=get_navigation_keyboard(),
    )


@labeler.message(text=["🌐 Наш сайт", "Наш сайт", "Сайт", "сайт", "/site"])
async def site_handler(message: Message):
    from config import WEB_APP_URL
    text = (
        "🌐 Официальный сайт и онлайн-запись мастера Бориса Гафурова:\n\n"
        f"{WEB_APP_URL}\n\n"
        "На сайте вы можете выбрать услуги, посмотреть доступные окна и воспользоваться персональным бонусом 300 ₽!"
    )
    await message.answer(text)
