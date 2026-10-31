"""
VK Community Reviews Synchronization Service.

Synchronizes reviews left in VK community (https://vk.ru/reviews-242475554 or discussions)
with the central database and srezforma.ru website.

Rules (per conditions 9 & 10):
- Imports reviews from VK community and publishes them on the website.
- If author doesn't exist, registers a client profile with vk_id.
- When this client later books an appointment, their data and phone are merged seamlessly.
- Duplicate reviews are strictly skipped using external_id.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
from typing import Any
import urllib.request

import certifi

from services.reviews import (
    import_external_review,
    normalize_review_comment,
    validate_review_comment,
)

logger = logging.getLogger(__name__)

COMMUNITY_ID = 242475554
COMMUNITY_REVIEWS_URL = f"https://vk.ru/reviews-{COMMUNITY_ID}"


async def fetch_community_reviews_html() -> str | None:
    """Fetch public reviews page from VK."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    def _sync_fetch() -> str:
        req = urllib.request.Request(COMMUNITY_REVIEWS_URL, headers=headers)
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    try:
        return await asyncio.to_thread(_sync_fetch)
    except Exception as exc:
        logger.warning("Could not fetch VK community reviews page: %s", exc)
        return None


def parse_vk_reviews(html: str) -> list[dict[str, Any]]:
    """
    Parses review items from the VK reviews page.
    Handles standard VK review layout.
    """
    reviews = []
    if not html:
        return reviews

    # Matches review cards or JSON embedded payloads in VK pages
    # VK frequently bundles initial data in cur.reviews or data-review-id
    # 1. Regex for review blocks with data attributes:
    review_blocks = re.findall(
        r'<div[^>]+(?:class="[^"]*review[^"]*"|data-review-id="(\d+)")[^>]*>(.*?)</div>\s*</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    for item in review_blocks:
        try:
            rev_id = item[0] if isinstance(item, tuple) else ""
            block_html = item[1] if isinstance(item, tuple) else item

            # Author ID
            author_match = re.search(r'href="/(?:id|write)?(\d+)"', block_html)
            vk_user_id = int(author_match.group(1)) if author_match else 0
            if not vk_user_id:
                continue

            # Author name
            name_match = re.search(r'class="[^"]*author[^"]*"[^>]*>([^<]+)</a>', block_html)
            full_name = name_match.group(1).strip() if name_match else "Клиент VK"
            parts = full_name.split(maxsplit=1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""

            # Rating stars count
            stars_count = len(re.findall(r'class="[^"]*star--filled[^"]*"', block_html)) or 5
            stars_count = max(1, min(5, stars_count))

            # Comment text
            text_match = re.search(r'class="[^"]*review__text[^"]*"[^>]*>(.*?)</div>', block_html, re.DOTALL)
            raw_text = text_match.group(1) if text_match else ""
            clean_text = re.sub(r"<[^>]+>", " ", raw_text).strip()

            if clean_text:
                reviews.append({
                    "external_id": f"vk_comm_{rev_id or vk_user_id}",
                    "vk_user_id": vk_user_id,
                    "first_name": first_name,
                    "last_name": last_name,
                    "rating": stars_count,
                    "comment": clean_text,
                })
        except Exception as e:
            logger.debug("Failed parsing review card: %s", e)
            continue

    return reviews


async def sync_vk_community_reviews() -> int:
    """
    Syncs reviews from VK community into the unified database.
    Returns the number of newly imported reviews.
    """
    html = await fetch_community_reviews_html()
    if not html:
        return 0

    items = parse_vk_reviews(html)
    imported_count = 0

    for rev in items:
        try:
            ok, msg, created = import_external_review(
                platform="vk",
                platform_user_id=rev["vk_user_id"],
                rating=rev["rating"],
                comment=rev["comment"],
                author_name=rev["first_name"],
                author_last_name=rev.get("last_name", ""),
                external_id=rev["external_id"],
            )
            if ok and created and not msg.startswith("Отзыв уже был"):
                imported_count += 1
                logger.info(
                    "Синхронизирован отзыв из группы ВК #%s от %s (оценка %s)",
                    rev["external_id"],
                    rev["first_name"],
                    rev["rating"],
                )
        except Exception as e:
            logger.warning("Ошибка импорта отзыва ВК: %s", e)

    return imported_count
