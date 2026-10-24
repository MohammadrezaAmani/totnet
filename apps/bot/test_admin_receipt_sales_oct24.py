"""Regression checks for gift receipt stages and admin receipt lifecycle."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import User
from apps.brands.models import Brand
from apps.bot.handlers.purchase import PurchaseHandler
from apps.bot.handlers.admin_hiddify import HiddifyAdminHandler
from apps.orders.models import Payment, AdminReceiptMessage
from apps.orders.tasks import delete_confirmed_admin_receipts


class AdminReceiptTextTests(SimpleTestCase):
    def test_admin_receipt_only_includes_requested_fields(self):
        bot = MagicMock()
        bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=None))
        handler = PurchaseHandler(bot, SimpleNamespace(pk=1))
        buyer = SimpleNamespace(
            full_name="Buyer", username="buyer", telegram_id=12345,
            get_full_name=lambda: "Buyer",
            referred_by=SimpleNamespace(telegram_id=67890),
        )
        order = SimpleNamespace(
            user=buyer, order_number="ORD-TEST", currency="T",
            plan=SimpleNamespace(name="10g_1m_royal"),
        )
        payment = SimpleNamespace(
            pk=44, id=44, amount=Decimal("180000"), created_at=None,
            card_number="6037990000001234", cardholder_name="Owner",
            receipt_reference="Receipt info",
        )

        async def admins():
            yield SimpleNamespace(telegram_id=555, pk=1)

        handler.get_brand_admin_recipients = admins
        handler.format_price = lambda amount, currency: "180,000 تومان"
        with patch("apps.bot.handlers.purchase.jalali_date", return_value="1405/08/01"):
            self.assertTrue(async_to_sync(handler._notify_admin_receipt)(order, payment, "file-id"))
        caption = bot.send_photo.await_args.kwargs["caption"]
        fields = [line.split(":", 1)[0] for line in caption.splitlines()[1:]]
        self.assertEqual(fields, [
            "حساب مقصد", "مبلغ", "پلن", "تاریخ", "خریدار",
            "آیدی عددی خریدار", "آیدی عددی معرف",
        ])
        for label in ("ORD-TEST", "متن رسید", "payment_id"):
            self.assertNotIn(label, caption)

    def test_admin_menu_routes_exist(self):
        import inspect
        from apps.bot.dispatcher import MultiBrandDispatcher
        source = inspect.getsource(MultiBrandDispatcher.route_callback)
        self.assertIn('data == "admin_today_sales"', source)
        self.assertIn('data.startswith("admin_today_sale_")', source)


class AdminReceiptPersistenceTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            slug="receipt-admin-test", name="Receipt Test", bot_token="123456:FAKE",
            currency="T", contact_email="test@example.invalid",
        )
        self.user = User.objects.create_user(
            username="receipt-buyer", brand=self.brand, telegram_id=190001,
        )
        self.payment = Payment.objects.create(
            brand=self.brand, user=self.user,
            status=Payment.PaymentStatus.PENDING,
            payment_method=Payment.PaymentMethod.CARD_TRANSFER,
            amount=Decimal("180000"), currency="T",
        )
        self.receipt = AdminReceiptMessage.objects.create(
            payment=self.payment, chat_id=190002, message_id=3399,
        )

    def test_confirm_queues_delete_only_after_db_commit(self):
        with patch("apps.orders.tasks.delete_confirmed_admin_receipts.delay") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self.payment.status = Payment.PaymentStatus.CONFIRMED
                self.payment.save(update_fields=["status"])
                self.assertFalse(enqueue.called)
        enqueue.assert_called_once_with(self.payment.pk)

    def test_delete_never_runs_for_pending_payment(self):
        with patch("apps.orders.tasks.Bot") as bot:
            self.assertEqual(delete_confirmed_admin_receipts.run(self.payment.pk), 0)
        bot.assert_not_called()
        self.receipt.refresh_from_db()
        self.assertIsNone(self.receipt.deleted_at)

    def test_delete_confirmed_payment_marks_message_only_on_success(self):
        self.payment.status = Payment.PaymentStatus.CONFIRMED
        self.payment.save(update_fields=["status"])
        with patch("apps.orders.tasks.Bot") as bot_type:
            bot = bot_type.return_value
            bot.delete_message = AsyncMock(return_value=True)
            bot.session.close = AsyncMock()
            self.assertEqual(delete_confirmed_admin_receipts.run(self.payment.pk), 1)
        bot.delete_message.assert_awaited_once_with(chat_id=190002, message_id=3399)
        self.receipt.refresh_from_db()
        self.assertIsNotNone(self.receipt.deleted_at)
