import json
import logging
from typing import Any

from vkbottle import GroupEventType
from vkbottle.bot import BotLabeler, Message, MessageEvent

from keyboards.advice import (
    get_article_detail_keyboard,
    get_product_detail_keyboard,
    get_products_keyboard,
    get_recommendations_hub_keyboard,
)
from services.recommendations import get_article, get_product

logger = logging.getLogger(__name__)
labeler = BotLabeler()

HUB_TEXT = (
    "🧴 Рекомендации мастера и проверенный стайлинг\n\n"
    "Здесь собраны подробные инструкции по укладке и ссылки на проверенные средства с Ozon, "
    "которыми мастер пользуется лично.\n\n"
    "Выбери нужный раздел:"
)

PRODUCTS_TEXT = (
    "🛍 Проверенные средства на Ozon:\n\n"
    "Здесь собраны проверенные стайлинги и уход, которые мастер лично использует в работе. "
    "Выбери категорию, чтобы посмотреть рекомендации и ссылки 👇"
)


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


@labeler.message(
    text=[
        "🧴 Советы и товары",
        "Советы и товары",
        "советы и товары",
        "🧴 Советы по уходу",
        "Советы по уходу",
        "советы по уходу",
        "советы",
        "Советы",
        "товары",
        "Товары",
        "/advice",
        "/tips",
        "/care",
        "/products",
    ]
)
async def advice_menu(message: Message):
    await message.answer(
        HUB_TEXT,
        keyboard=get_recommendations_hub_keyboard(),
    )


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "rec_hub",
)
async def callback_rec_hub(event: MessageEvent):
    try:
        await event.edit_message(
            message=HUB_TEXT,
            keyboard=get_recommendations_hub_keyboard(),
        )
    except Exception as e:
        logger.warning("Ошибка при возврате в меню советов: %s", e)
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "rec_products",
)
async def callback_rec_products(event: MessageEvent):
    payload = get_payload(event)
    page = int(payload.get("page", 0))

    try:
        await event.edit_message(
            message=PRODUCTS_TEXT,
            keyboard=get_products_keyboard(page=page),
        )
    except Exception as e:
        logger.warning("Ошибка при отображении товаров: %s", e)
    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "art",
)
async def callback_article_detail(event: MessageEvent):
    payload = get_payload(event)
    slug = str(payload.get("slug", ""))
    article = get_article(slug)

    if not article:
        await event.show_snackbar("Инструкция не найдена")
        return

    text = f"📖 {article['title']}\n\n{article['text']}"

    try:
        await event.edit_message(
            message=text,
            keyboard=get_article_detail_keyboard(slug),
        )
    except Exception as e:
        logger.warning("Ошибка при отображении статьи %s: %s", slug, e)

    await event.send_empty_answer()


@labeler.raw_event(
    GroupEventType.MESSAGE_EVENT,
    dataclass=MessageEvent,
    func=lambda event: get_payload(event).get("cmd") == "prod",
)
async def callback_product_detail(event: MessageEvent):
    payload = get_payload(event)
    slug = str(payload.get("slug", ""))
    page = int(payload.get("page", 0))
    product = get_product(slug)

    if not product:
        await event.show_snackbar("Товар не найден")
        return

    text = f"🏺 {product['title']}\n\n{product['text']}"

    try:
        await event.edit_message(
            message=text,
            keyboard=get_product_detail_keyboard(product["links"], page=page),
        )
    except Exception as e:
        logger.warning("Ошибка при отображении товара %s: %s", slug, e)

    await event.send_empty_answer()
