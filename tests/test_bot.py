import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

test_dir = tempfile.mkdtemp()
os.environ["DATA_DIR"] = test_dir

import database
from database import get_connection, init_db
from handlers.advice import labeler as advice_labeler
from handlers.booking import labeler as booking_labeler
from handlers.navigation import labeler as navigation_labeler
from handlers.referrals import (
    active_rewards_keyboard,
    admin_menu_keyboard,
    labeler as referrals_labeler,
    pending_referrals_keyboard,
)
from handlers.start import extract_ref_id, is_start_message, labeler as start_labeler, start_handler
from keyboards.advice import (
    get_article_detail_keyboard,
    get_articles_keyboard,
    get_product_detail_keyboard,
    get_products_keyboard,
    get_recommendations_hub_keyboard,
)
from keyboards.booking import get_booking_keyboard
from keyboards.menu import get_main_menu
from keyboards.navigation import get_navigation_keyboard
from main import fallback_labeler
from services.recommendations import get_article, get_articles, get_product, get_products
from services.referrals import (
    get_active_rewards,
    get_pending_referrals,
    get_referral_stats,
    mark_referral_visited,
    mark_reward_used,
    register_referral,
    save_user,
)
from vkbottle import Bot


class TestVkBookingBot(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(test_dir, ignore_errors=True)

    def setUp(self):
        with get_connection() as conn:
            conn.execute("DELETE FROM rewards")
            conn.execute("DELETE FROM referrals")
            conn.execute("DELETE FROM users")

    def test_database_and_referral_cycle(self):
        # 1. Save referrer
        referrer_id, is_new1 = save_user(
            vk_id=1001,
            domain="referrer_user",
            first_name="Boris",
            last_name="Gafurov",
        )
        self.assertTrue(is_new1)

        # Update existing user
        ref_id2, is_new1_updated = save_user(
            vk_id=1001,
            domain="referrer_user_updated",
            first_name="Boris",
            last_name="Gafurov",
        )
        self.assertEqual(referrer_id, ref_id2)
        self.assertFalse(is_new1_updated)

        # 2. Save new friend
        friend_id, is_new2 = save_user(
            vk_id=2002,
            domain="friend_user",
            first_name="Alex",
            last_name="Smith",
        )
        self.assertTrue(is_new2)

        # 3. Test self-referral prevention
        success_self, msg_self, _ = register_referral(1001, 1001)
        self.assertFalse(success_self)
        self.assertIn("самого себя", msg_self)

        # 4. Register valid referral
        success_ref, msg_ref, notify_data = register_referral(
            new_user_vk_id=2002, referrer_vk_id=1001
        )
        self.assertTrue(success_ref)
        self.assertIsNotNone(notify_data)
        self.assertEqual(notify_data["referrer_vk_id"], 1001)
        self.assertEqual(notify_data["invited_first_name"], "Alex")

        # 5. Prevent duplicate referral
        success_dup, msg_dup, _ = register_referral(
            new_user_vk_id=2002, referrer_vk_id=1001
        )
        self.assertFalse(success_dup)

        # 6. Check stats before visit
        stats = get_referral_stats(1001)
        self.assertEqual(stats["started"], 1)
        self.assertEqual(stats["confirmed"], 0)
        self.assertEqual(stats["active_rewards_count"], 0)
        self.assertEqual(stats["active_rewards_sum"], 0)

        # 7. Check pending referrals for admin
        pending_list, total_pending = get_pending_referrals(limit=10, offset=0)
        self.assertGreaterEqual(total_pending, 1)
        ref_entry = [p for p in pending_list if p["invited_vk_id"] == 2002][0]

        # 8. Mark visited (confirm referral)
        v_success, v_msg, v_notify = mark_referral_visited(ref_entry["id"])
        self.assertTrue(v_success)
        self.assertEqual(v_notify["referrer_vk_id"], 1001)
        self.assertEqual(v_notify["amount"], 300)
        self.assertEqual(v_notify["new_balance"], 300)

        # Try to confirm again (idempotent / should fail)
        v_again, _, _ = mark_referral_visited(ref_entry["id"])
        self.assertFalse(v_again)

        # 9. Check stats after confirmation
        stats_after = get_referral_stats(1001)
        self.assertEqual(stats_after["confirmed"], 1)
        self.assertEqual(stats_after["active_rewards_count"], 1)
        self.assertEqual(stats_after["active_rewards_sum"], 300)

        # 10. Check active rewards for admin
        rewards_list, total_rewards = get_active_rewards(limit=10, offset=0)
        self.assertGreaterEqual(total_rewards, 1)
        reward_entry = [r for r in rewards_list if r["vk_id"] == 1001][0]

        # 11. Mark reward used
        u_success, u_msg, u_notify = mark_reward_used(reward_entry["id"])
        self.assertTrue(u_success)
        self.assertEqual(u_notify["user_vk_id"], 1001)
        self.assertEqual(u_notify["amount"], 300)
        self.assertEqual(u_notify["remaining_balance"], 0)

        # Try to use again
        u_again, _, _ = mark_reward_used(reward_entry["id"])
        self.assertFalse(u_again)

        # 12. Stats after reward used
        stats_final = get_referral_stats(1001)
        self.assertEqual(stats_final["active_rewards_count"], 0)
        self.assertEqual(stats_final["active_rewards_sum"], 0)

    def test_recommendations(self):
        articles = get_articles()
        self.assertIn("styling", articles)
        self.assertIn("beard", articles)

        styling = get_article("styling")
        self.assertIsNotNone(styling)
        self.assertIn("укладывать", styling["title"].lower())

        products = get_products()
        self.assertIn("paste", products)
        self.assertIn("pomade", products)
        self.assertIn("shampoo", products)

        paste = get_product("paste")
        self.assertIsNotNone(paste)
        self.assertTrue(len(paste["links"]) > 0)

        pomade = get_product("pomade")
        self.assertIsNotNone(pomade)
        self.assertTrue(any("morgans" in label.lower() for label, _ in pomade["links"]))

    def test_keyboards(self):
        import json

        menu_admin = get_main_menu(is_admin=True)
        self.assertIsNotNone(menu_admin)
        data_admin = json.loads(menu_admin)
        labels_admin = [
            btn["action"]["label"]
            for row in data_admin["buttons"]
            for btn in row
        ]
        self.assertIn("⚙️ Админ-панель", labels_admin)

        menu_user = get_main_menu(is_admin=False)
        data_user = json.loads(menu_user)
        labels_user = [
            btn["action"]["label"]
            for row in data_user["buttons"]
            for btn in row
        ]
        self.assertNotIn("⚙️ Админ-панель", labels_user)

        booking_kb = get_booking_keyboard()
        self.assertIsNotNone(booking_kb)
        self.assertIn("open_link", booking_kb)

        nav_kb = get_navigation_keyboard()
        self.assertIsNotNone(nav_kb)
        self.assertIn("yandex.ru", nav_kb)

        hub_kb = get_recommendations_hub_keyboard()
        self.assertIsNotNone(hub_kb)

        art_kb = get_articles_keyboard()
        self.assertIsNotNone(art_kb)

        prod_kb = get_products_keyboard(page=0)
        self.assertIsNotNone(prod_kb)

        art_det_kb = get_article_detail_keyboard("styling")
        self.assertIsNotNone(art_det_kb)

        prod_det_kb = get_product_detail_keyboard([("Помада Morgans", "https://ozon.ru/p/o3357125")], page=0)
        self.assertIsNotNone(prod_det_kb)

        adm_kb = admin_menu_keyboard()
        self.assertIsNotNone(adm_kb)

        pending_kb = pending_referrals_keyboard([], 0, 0)
        self.assertIsNotNone(pending_kb)

        rewards_kb = active_rewards_keyboard([], 0, 0)
        self.assertIsNotNone(rewards_kb)

    async def test_bot_and_handlers_integration(self):
        bot = Bot("test_token")
        bot.labeler.load(start_labeler)
        bot.labeler.load(booking_labeler)
        bot.labeler.load(navigation_labeler)
        bot.labeler.load(advice_labeler)
        bot.labeler.load(referrals_labeler)
        bot.labeler.load(fallback_labeler)

        # Test helper functions
        msg = MagicMock()
        msg.ref = "ref_1001"
        msg.text = ""
        self.assertEqual(extract_ref_id(msg), 1001)
        self.assertTrue(is_start_message(msg))

        msg2 = MagicMock()
        msg2.ref = None
        msg2.payload = None
        msg2.text = "начать"
        self.assertTrue(is_start_message(msg2))

        # Test start_handler execution with mock message
        mock_user = MagicMock()
        mock_user.first_name = "Test"
        mock_user.last_name = "User"
        mock_user.domain = "test_user"

        mock_msg = MagicMock()
        mock_msg.from_id = 7777
        mock_msg.ref = "ref_1001"
        mock_msg.text = "/start"
        mock_msg.get_user = AsyncMock(return_value=mock_user)
        mock_msg.answer = AsyncMock()

        mock_api = MagicMock()
        mock_api.messages = MagicMock()
        mock_api.messages.send = AsyncMock()
        mock_msg.ctx_api = mock_api

        await start_handler(mock_msg)
        mock_msg.answer.assert_called_once()
        mock_api.messages.send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
