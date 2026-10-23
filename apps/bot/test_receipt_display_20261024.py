"""Regressions for receipt fields, point formatting and transient payment notices."""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.handlers.start import StartHandler
from apps.bot.services.broadcaster import BroadcastSubscriber
from apps.orders.tasks import delete_transient_payment_message
from utils.display_numbers import format_amount, format_point_count
from utils.message import broadcast_message


class ReceiptAndFormattingTests(SimpleTestCase):
    def test_no_padding_in_point_counts_and_amounts(self):
        self.assertEqual(format_point_count(Decimal("1.00000000")), "1")
        self.assertEqual(format_point_count(Decimal("0E-8")), "0")
        self.assertEqual(format_amount(Decimal("180000.00")), "180,000")
        self.assertEqual(format_amount(Decimal("0.00")), "0")
        self.assertEqual(format_amount(Decimal("13.25000")), "13.25")

    def test_receipt_contains_cardholder_buyer_referrer_telegram_ids_and_plan(self):
        brand = SimpleNamespace(pk=1)
        bot = MagicMock()
        bot.send_photo = AsyncMock()
        handler = PurchaseHandler(bot, brand)
        buyer = SimpleNamespace(
            full_name="Ali <buyer>", username="buyer", telegram_id=123456,
            get_full_name=lambda: "Ali", referred_by=SimpleNamespace(telegram_id=98765),
        )
        order = SimpleNamespace(
            order_number="ORD-123", currency="T", user=buyer,
            plan=SimpleNamespace(name="Royal <monthly>"),
        )
        payment = SimpleNamespace(
            id=33, amount=Decimal("180000.00"), created_at=None,
            card_number="6037991234567890", cardholder_name="Card & Owner",
            receipt_reference=None,
        )

        async def admins():
            yield SimpleNamespace(telegram_id=11001, pk=1)

        handler.get_brand_admin_recipients = admins
        handler.format_price = lambda value, currency: "180,000 تومان"
        with patch("apps.bot.handlers.purchase.jalali_date", return_value="1405/08/01"):
            self.assertTrue(async_to_sync(handler._notify_admin_receipt)(order, payment, "photo-file"))
        caption = bot.send_photo.await_args.kwargs["caption"]
        self.assertIn("Card &amp; Owner", caption)
        self.assertIn("Ali &lt;buyer&gt;", caption)
        self.assertIn("123456", caption)
        self.assertIn("98765", caption)
        self.assertIn("Royal &lt;monthly&gt;", caption)

    def test_registration_prompt_does_not_show_status_checklist(self):
        handler = StartHandler(MagicMock(), SimpleNamespace(name="Test", pk=1))
        handler.update_user_state = AsyncMock()
        handler.send_profile_prompt = AsyncMock()
        with patch("apps.bot.handlers.start.BotState") as states:
            states.objects.filter.return_value.aupdate = AsyncMock()
            async_to_sync(handler.show_profile_setup)(1, SimpleNamespace(pk=2))
        prompt = handler.send_profile_prompt.await_args.args[2]
        self.assertNotIn("❌", prompt)
        self.assertNotIn("👤 نام کامل:", prompt)
        self.assertNotIn("📱 شماره تلفن:", prompt)
        self.assertNotIn("💻 دستگاه اصلی:", prompt)


class PaymentAutoDeleteTests(SimpleTestCase):
    def test_broadcast_payload_marks_only_selected_messages_for_deletion(self):
        with patch("utils.message.redis_client.publish") as pub:
            broadcast_message(brand_id=1, user_ids=[42], text="paid", delete_after_seconds=60)
            payload = json.loads(pub.call_args.args[1])
            self.assertEqual(payload["delete_after_seconds"], 60)
            broadcast_message(brand_id=1, user_ids=[42], text="other")
            self.assertNotIn("delete_after_seconds", json.loads(pub.call_args.args[1]))

    def test_payment_notice_deletion_is_queued_after_telegram_send(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=931))
        with patch("apps.bot.services.broadcaster.aioredis.from_url"):
            subscriber = BroadcastSubscriber(bot, "17")
        with patch("apps.orders.tasks.delete_transient_payment_message.apply_async") as enqueue:
            async_to_sync(subscriber._send_single_message)(42, {
                "text": "paid", "delete_after_seconds": 60,
            })
        enqueue.assert_called_once_with(
            kwargs={"brand_id": 17, "chat_id": 42, "message_id": 931},
            countdown=60,
        )

    def test_regular_notice_is_not_scheduled_for_deletion(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=932))
        with patch("apps.bot.services.broadcaster.aioredis.from_url"):
            subscriber = BroadcastSubscriber(bot, "17")
        with patch("apps.orders.tasks.delete_transient_payment_message.apply_async") as enqueue:
            async_to_sync(subscriber._send_single_message)(42, {"text": "ordinary"})
        enqueue.assert_not_called()

    def test_delivered_notice_can_be_deleted_by_worker(self):
        with patch("apps.orders.tasks.Brand.objects.filter") as brands, patch("apps.orders.tasks.Bot") as bot_cls:
            brands.return_value.values_list.return_value.first.return_value = "TEST:token"
            bot = bot_cls.return_value
            bot.delete_message = AsyncMock(return_value=True)
            bot.session.close = AsyncMock()
            self.assertTrue(delete_transient_payment_message.run(
                brand_id=17, chat_id=42, message_id=931,
            ))
            bot.delete_message.assert_awaited_once_with(chat_id=42, message_id=931)
