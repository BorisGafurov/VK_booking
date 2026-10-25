from vkbottle.bot import BotLabeler, Message

from keyboards.booking import get_booking_keyboard
from services.referrals import get_user_phone, is_phone_number, link_user_phone

labeler = BotLabeler()


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
    phone = get_user_phone(message.from_id, platform="vk")
    if phone:
        await message.answer(
            "Нажми кнопку ниже, выбери услугу и свободное время:",
            keyboard=get_booking_keyboard(),
        )
    else:
        await message.answer(
            "✂️ Онлайн-запись\n\n"
            "Чтобы закрепить за тобой бонусы и объединить профиль, отправь свой номер телефона сообщением (например, +79991234567):\n\n"
            "Или переходи сразу к выбору времени:",
            keyboard=get_booking_keyboard(),
        )


@labeler.message(func=lambda msg: bool(msg.text and is_phone_number(msg.text)))
async def phone_message_handler(message: Message):
    success, _ = link_user_phone(message.from_id, message.text, platform="vk")
    if success:
        await message.answer(
            "✅ Номер телефона успешно сохранен!\n\n"
            "Теперь твои бонусы и приглашённые друзья объединены в едином профиле.\n\n"
            "Нажми кнопку ниже, выбери услугу и свободное время:",
            keyboard=get_booking_keyboard(),
        )
    else:
        await message.answer("Пожалуйста, укажи корректный номер телефона (например, +79991234567).")
