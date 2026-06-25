"""
Django management command to set up the Multi-Tenant VPN Platform
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.brands.models import Brand, BrandConfiguration, BrandTheme
from apps.orders.models import CryptoCurrency, PaymentCard
from apps.referrals.models import ReferralProgram
from apps.subscriptions.models import SubscriptionPlan

User = get_user_model()


class Command(BaseCommand):
    help = "Set up the Multi-Tenant VPN Platform with initial data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--brand-name", type=str, help="Name of the brand to create"
        )
        parser.add_argument("--bot-token", type=str, help="Telegram bot token")
        parser.add_argument("--domain", type=str, help="Brand domain")
        parser.add_argument("--admin-email", type=str, help="Admin email for the brand")
        parser.add_argument(
            "--currency", type=str, default="USD", help="Brand currency (default: USD)"
        )
        parser.add_argument(
            "--sample-data",
            action="store_true",
            help="Create sample subscription plans",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Setting up Multi-Tenant VPN Platform..."))

        if options["brand_name"] and options["bot_token"]:
            brand = self.create_brand(options)
            self.setup_brand_configuration(brand)
            self.setup_brand_theme(brand)
            self.setup_referral_program(brand)

            if options["sample_data"]:
                self.create_sample_plans(brand)
                self.create_sample_payment_methods(brand)

            self.stdout.write(
                self.style.SUCCESS(f"Successfully created brand: {brand.name}")
            )
        else:
            self.stdout.write(
                self.style.ERROR("--brand-name and --bot-token are required")
            )

    def create_brand(self, options):
        """Create a new brand"""
        slug = options["brand_name"].lower().replace(" ", "_").replace("-", "_")

        brand, created = Brand.objects.get_or_create(
            slug=slug,
            defaults={
                "name": options["brand_name"],
                "bot_token": options["bot_token"],
                "domain": options.get("domain"),
                "contact_email": options.get("admin_email", "admin@example.com"),
                "currency": options.get("currency", "USD"),
                "status": Brand.BrandStatus.ACTIVE,
                "is_verified": True,
            },
        )

        if created:
            self.stdout.write(f"Created brand: {brand.name}")
        else:
            self.stdout.write(f"Brand already exists: {brand.name}")

        return brand

    def setup_brand_configuration(self, brand):
        """Set up brand configuration"""
        config, created = BrandConfiguration.objects.get_or_create(
            brand=brand,
            defaults={
                "welcome_message": f"خوش آمدید به {brand.name}! بهترین سرویس VPN را تجربه کنید.",
                "help_message": "برای دریافت کمک، از منوی پشتیبانی استفاده کنید.",
                "referral_system_enabled": True,
                "wallet_system_enabled": True,
                "support_system_enabled": True,
                "analytics_enabled": True,
                "max_subscriptions_per_user": 10,
                "max_referrals_per_day": 100,
            },
        )

        if created:
            self.stdout.write(f"Created configuration for: {brand.name}")

    def setup_brand_theme(self, brand):
        """Set up brand theme"""
        theme, created = BrandTheme.objects.get_or_create(
            brand=brand,
            defaults={
                "button_style": "rounded",
                "menu_style": "grid",
                "font_family": "Vazir",
            },
        )

        if created:
            self.stdout.write(f"Created theme for: {brand.name}")

    def setup_referral_program(self, brand):
        """Set up referral program"""
        program, created = ReferralProgram.objects.get_or_create(
            brand=brand,
            defaults={
                "is_active": True,
                "name": "برنامه معرفی دوستان",
                "description": "با معرفی دوستان خود امتیاز و جایزه کسب کنید!",
                "referrer_reward_type": ReferralProgram.RewardType.PERCENTAGE,
                "referrer_reward_value": 10,
                "referee_reward_type": ReferralProgram.RewardType.PERCENTAGE,
                "referee_reward_value": 5,
                "require_purchase": True,
                "minimum_purchase_amount": 10,
                "conversion_window_days": 30,
                "max_referrals_per_day": 10,
                "same_ip_limit": 3,
            },
        )

        if created:
            self.stdout.write(f"Created referral program for: {brand.name}")

    def create_sample_plans(self, brand):
        """Create sample subscription plans"""
        plans_data = [
            {
                "name": "پلن ماهانه",
                "plan_type": SubscriptionPlan.PlanType.UNLIMITED,
                "price": 15.00,
                "duration_value": 30,
                "duration_unit": SubscriptionPlan.DurationUnit.DAYS,
                "max_users": 1,
                "features": ["دسترسی نامحدود", "پشتیبانی 24/7", "سرعت بالا"],
                "display_order": 1,
            },
            {
                "name": "پلن سه ماهه",
                "plan_type": SubscriptionPlan.PlanType.UNLIMITED,
                "price": 40.00,
                "duration_value": 90,
                "duration_unit": SubscriptionPlan.DurationUnit.DAYS,
                "max_users": 2,
                "features": [
                    "دسترسی نامحدود",
                    "پشتیبانی 24/7",
                    "سرعت بالا",
                    "2 کاربر همزمان",
                ],
                "display_order": 2,
                "discount_percentage": 10,
            },
            {
                "name": "پلن سالانه VIP",
                "plan_type": SubscriptionPlan.PlanType.UNLIMITED,
                "price": 120.00,
                "duration_value": 365,
                "duration_unit": SubscriptionPlan.DurationUnit.DAYS,
                "max_users": 4,
                "features": [
                    "دسترسی نامحدود",
                    "پشتیبانی اختصاصی",
                    "سرعت فوق العاده",
                    "4 کاربر همزمان",
                ],
                "display_order": 3,
                "discount_percentage": 25,
                "is_featured": True,
            },
            {
                "name": "پلن ترافیکی 100GB",
                "plan_type": SubscriptionPlan.PlanType.TRAFFIC_BASED,
                "price": 10.00,
                "traffic_limit_gb": 100,
                "max_users": 1,
                "features": ["100 گیگابایت ترافیک", "بدون محدودیت زمانی"],
                "display_order": 4,
            },
        ]

        for plan_data in plans_data:
            plan, created = SubscriptionPlan.objects.get_or_create(
                brand=brand,
                name=plan_data["name"],
                defaults={**plan_data, "currency": brand.currency},
            )

            if created:
                self.stdout.write(f"Created plan: {plan.name}")

    def create_sample_payment_methods(self, brand):
        """Create sample payment methods"""
        card, created = PaymentCard.objects.get_or_create(
            brand=brand,
            card_number="6037-9915-****-****",
            defaults={
                "bank_name": "بانک ملی ایران",
                "cardholder_name": "احمد احمدی",
                "card_type": "ملی کارت",
                "is_active": True,
                "display_order": 1,
            },
        )

        if created:
            self.stdout.write(f"Created payment card: {card.bank_name}")

        crypto, created = CryptoCurrency.objects.get_or_create(
            brand=brand,
            symbol="BTC",
            defaults={
                "name": "Bitcoin",
                "network": CryptoCurrency.NetworkType.BITCOIN,
                "wallet_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                "is_active": True,
                "display_order": 1,
                "required_confirmations": 3,
            },
        )

        if created:
            self.stdout.write(f"Created cryptocurrency: {crypto.name}")

        self.stdout.write(self.style.SUCCESS("Platform setup completed successfully!"))
        self.stdout.write("")
        self.stdout.write("Next steps:")
        self.stdout.write("1. Configure your VPN providers in Django admin")
        self.stdout.write("2. Set up payment gateway credentials")
        self.stdout.write("3. Customize your bot messages and themes")
        self.stdout.write("4. Start the bot with: python manage_bot.py")
        self.stdout.write("")
        self.stdout.write(f"Brand created: {brand.name}")
        self.stdout.write(f"Bot token: {brand.bot_token[:10]}...")
        self.stdout.write("Django admin: http://localhost:8000/admin/")
