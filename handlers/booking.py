from vkbottle.bot import BotLabeler, Message

from keyboards.booking import get_booking_keyboard

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
    await message.answer(
        "Нажми кнопку ниже, выбери услугу и свободное время:",
        keyboard=get_booking_keyboard(),
    )
