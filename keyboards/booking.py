from typing import Any
from vkbottle import Callback, Keyboard, OpenLink

from config import BOOKING_URL
from services.booking import SERVICES, calculate_services


def get_booking_keyboard() -> str:
    """Return inline keyboard with direct booking URL."""
    kb = Keyboard(inline=True)
    kb.add(OpenLink(link=BOOKING_URL, label="🌐 Перейти на сайт"))
    return kb.get_json()


def get_services_keyboard(selected_keys: list[str] | set[str]) -> str:
    calc = calculate_services(selected_keys)
    active_keys = set(calc["keys"])

    kb = Keyboard(inline=True)
    for key, info in SERVICES.items():
        is_selected = key in active_keys
        icon = "✅" if is_selected else "⚪️"
        btn_text = f"{icon} {info['name']} · {info['price']}₽"
        kb.add(Callback(btn_text[:40], {"cmd": "book_toggle", "key": key}))
        kb.row()

    next_text = f"📅 Выбрать время ({calc['total_price']} ₽)"
    kb.add(Callback(next_text[:40], {"cmd": "book_days"}))
    kb.row()
    kb.add(OpenLink(link=BOOKING_URL, label="🌐 Перейти на сайт"))

    return kb.get_json()


def get_days_keyboard(days_map: dict[str, Any]) -> str:
    kb = Keyboard(inline=True)
    day_items = list(days_map.values())[:8]

    for i in range(0, len(day_items), 2):
        for d in day_items[i : i + 2]:
            kb.add(Callback(f"📅 {d['title']}"[:40], {"cmd": "book_day", "day": d["day_key"]}))
        kb.row()

    if not day_items:
        kb.add(Callback("Нет свободных дат", {"cmd": "noop"}))
        kb.row()

    kb.add(Callback("◀️ К услугам", {"cmd": "book_services"}))
    kb.add(OpenLink(link=BOOKING_URL, label="🌐 Перейти на сайт"))
    return kb.get_json()


PAGE_SLOTS_COUNT = 6


def get_slots_keyboard(slots: list[dict[str, Any]], page: int = 0, day_key: str = "") -> str:
    kb = Keyboard(inline=True)

    if not slots:
        kb.add(Callback("Нет свободных окон", {"cmd": "noop"}))
        kb.row()
        kb.add(Callback("◀️ Другой день", {"cmd": "book_days"}))
        kb.add(OpenLink(link=BOOKING_URL, label="🌐 Перейти на сайт"))
        return kb.get_json()

    total_slots = len(slots)
    start_idx = page * PAGE_SLOTS_COUNT
    end_idx = start_idx + PAGE_SLOTS_COUNT
    page_slots = slots[start_idx:end_idx]

    # Slots grid: 2 buttons per row for maximum readability on mobile screens (at most 3 rows = 6 buttons)
    for i in range(0, len(page_slots), 2):
        for s in page_slots[i : i + 2]:
            kb.add(Callback(f"🕒 {s['time']}", {"cmd": "book_slot", "ts": s["timestamp"]}))
        kb.row()

    # Pagination row if multiple pages (at most 1 row, 2 buttons)
    has_prev = page > 0
    has_next = end_idx < total_slots
    if has_prev or has_next:
        if has_prev:
            kb.add(Callback("◀️ Ранее", {"cmd": "book_slots_page", "page": page - 1, "day": day_key}))
        if has_next:
            kb.add(Callback("Позже ▶️", {"cmd": "book_slots_page", "page": page + 1, "day": day_key}))
        kb.row()

    # Navigation row (1 row, 2 buttons)
    kb.add(Callback("◀️ Другой день", {"cmd": "book_days"}))
    kb.add(OpenLink(link=BOOKING_URL, label="🌐 Перейти на сайт"))
    return kb.get_json()
