import re
from vkbottle import Callback, Keyboard, OpenLink

from services.recommendations import get_articles, get_products

PAGE_SIZE = 4


def _clean_btn_title(title: str) -> str:
    """Ensure button label is concise and does not exceed VK 40 chars limit."""
    if len(title) > 35 and "(" in title:
        title = re.sub(r"\s*\([A-Za-z /]+\)", "", title).strip()
    return title[:40]


def get_recommendations_hub_keyboard() -> str:
    """Hub keyboard showing articles and entrance to products."""
    articles = get_articles()
    kb = Keyboard(inline=True)

    for slug, item in articles.items():
        title = _clean_btn_title(item["title"])
        kb.add(Callback(title, {"cmd": "art", "slug": slug}))
        kb.row()

    kb.add(Callback("🛍 Проверенные товары на Ozon", {"cmd": "rec_products", "page": 0}))
    return kb.get_json()


def get_articles_keyboard() -> str:
    """Keyboard listing all articles."""
    articles = get_articles()
    kb = Keyboard(inline=True)

    for slug, item in articles.items():
        title = _clean_btn_title(item["title"])
        kb.add(Callback(title, {"cmd": "art", "slug": slug}))
        kb.row()

    kb.add(Callback("⬅️ Назад к советам", {"cmd": "rec_hub"}))
    return kb.get_json()


def get_products_keyboard(page: int = 0) -> str:
    """Paginated keyboard listing products (max 6 rows strictly respected)."""
    products = list(get_products().items())
    total = len(products)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = products[start:end]

    kb = Keyboard(inline=True)

    for slug, item in page_items:
        title = _clean_btn_title(item["title"])
        kb.add(Callback(title, {"cmd": "prod", "slug": slug, "page": page}))
        kb.row()

    # Pagination controls row
    nav_row_needed = False
    if page > 0 or end < total:
        if page > 0:
            kb.add(Callback("◀️ Назад", {"cmd": "rec_products", "page": page - 1}))
            nav_row_needed = True
        if end < total:
            kb.add(Callback("Вперёд ▶️", {"cmd": "rec_products", "page": page + 1}))
            nav_row_needed = True
        if nav_row_needed:
            kb.row()

    kb.add(Callback("⬅️ Назад к советам", {"cmd": "rec_hub"}))
    return kb.get_json()


def get_article_detail_keyboard(slug: str) -> str:
    """Keyboard for article details."""
    kb = Keyboard(inline=True)
    kb.add(Callback("🛍 Товары на Ozon", {"cmd": "rec_products", "page": 0}))
    kb.row()
    kb.add(Callback("⬅️ Назад к советам", {"cmd": "rec_hub"}))
    return kb.get_json()


def get_product_detail_keyboard(links: list[tuple[str, str]], page: int = 0) -> str:
    """Keyboard for product details with Ozon OpenLink buttons."""
    kb = Keyboard(inline=True)

    # Add at most 4 direct product links to stay well within VK inline keyboard limits (max 6 rows total)
    for label, url in links[:4]:
        btn_text = f"🛍 {label[:28]} ↗"[:40]
        kb.add(OpenLink(link=url, label=btn_text))
        kb.row()

    kb.add(Callback("⬅️ Назад к товарам", {"cmd": "rec_products", "page": page}))
    return kb.get_json()
