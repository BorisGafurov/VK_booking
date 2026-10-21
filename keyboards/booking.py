from vkbottle import Keyboard, OpenLink

from config import BOOKING_URL


def get_booking_keyboard() -> str:
    """Return inline keyboard with direct booking URL."""
    kb = Keyboard(inline=True)
    kb.add(OpenLink(link=BOOKING_URL, label="✂️ Записаться онлайн"))
    return kb.get_json()
