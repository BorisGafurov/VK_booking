from vkbottle import Keyboard, OpenLink

from config import SALON_LATITUDE, SALON_LONGITUDE


def get_navigation_keyboard() -> str:
    """Return inline keyboard with Yandex Maps link."""
    yandex_maps_url = (
        f"https://yandex.ru/maps/?pt={SALON_LONGITUDE},{SALON_LATITUDE}&z=17&l=map"
    )
    kb = Keyboard(inline=True)
    kb.add(OpenLink(link=yandex_maps_url, label="🗺 В Яндекс Картах"))
    return kb.get_json()
