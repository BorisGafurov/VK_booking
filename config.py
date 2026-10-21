import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

VK_TOKEN = (os.getenv("VK_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()

BOOKING_URL = os.getenv(
    "BOOKING_URL",
    "https://mst.link/boris_gafurov/price?cid=vddrm99a3uu0o9cgk508u6uctp",
)

SALON_ADDRESS = os.getenv("SALON_ADDRESS", "Саратов, ул. имени Е.И. Пугачёва, 110")
SALON_LATITUDE = float(os.getenv("SALON_LATITUDE", "51.536988"))
SALON_LONGITUDE = float(os.getenv("SALON_LONGITUDE", "46.013896"))
WORK_HOURS = os.getenv("WORK_HOURS", "Вторник и суббота: 10:00–19:00")

ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}

REFERRAL_REWARD_AMOUNT = int(os.getenv("REFERRAL_REWARD_AMOUNT", "300"))
REFERRAL_REWARD_TEXT = f"{REFERRAL_REWARD_AMOUNT} рублей"

# Optional explicit group identification; if omitted, resolved dynamically at startup via VK API
VK_GROUP_ID_ENV = os.getenv("VK_GROUP_ID", "").strip()
GROUP_ID: int | None = int(VK_GROUP_ID_ENV) if VK_GROUP_ID_ENV.isdigit() else None
GROUP_DOMAIN: str | None = os.getenv("VK_GROUP_DOMAIN", "").strip() or None
