"""Register the .env bot and import real plans, without creating sample products."""

from asgiref.sync import async_to_sync
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.brands.models import Brand, BrandConfiguration, BrandTheme
from apps.vpn_providers.models import VPNProvider
from apps.vpn_providers.tasks import sync_connectix_plans


class Command(BaseCommand):
    help = "Register TELEGRAM_BOT_TOKEN and Connectix from .env; optionally sync plans"

    def add_arguments(self, parser):
        parser.add_argument("--sync-plans", action="store_true")

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            self.stdout.write(
                "No TELEGRAM_BOT_TOKEN configured; using existing brands."
            )
            return
        brand = Brand.objects.filter(bot_token=token).first()
        if brand is None:

            async def identify():
                bot = Bot(token, session=AiohttpSession(proxy=settings.SOCKS5_PROXY))
                try:
                    return await bot.get_me()
                finally:
                    await bot.session.close()

            try:
                identity = async_to_sync(identify)()
            except Exception as exc:
                raise CommandError(
                    f"Telegram identity check failed ({type(exc).__name__})"
                ) from None
            slug = identity.username.lower().replace("_", "-")
            if Brand.objects.filter(slug=slug).exists():
                raise CommandError(
                    "A brand with this bot username already exists; update its token in admin."
                )
            brand = Brand.objects.create(
                name=identity.username,
                slug=slug,
                bot_token=token,
                bot_username=identity.username,
                contact_email=settings.DEFAULT_FROM_EMAIL,
                status=Brand.BrandStatus.ACTIVE,
                currency="T",
                language="fa",
                timezone="Asia/Tehran",
            )
        BrandConfiguration.objects.get_or_create(brand=brand)
        BrandTheme.objects.get_or_create(brand=brand)
        self.stdout.write(f"Bot brand ready: {brand.slug} ({brand.status})")
        if settings.CONNECTIX_USERNAME and settings.CONNECTIX_PASSWORD:
            provider = VPNProvider.objects.filter(
                brand=brand, provider_type=VPNProvider.ProviderType.CONNECTIX
            ).first()
            if provider is None:
                provider = VPNProvider.objects.create(
                    brand=brand,
                    name="Connectix",
                    provider_type=VPNProvider.ProviderType.CONNECTIX,
                    base_url=settings.CONNECTIX_API_BASE_URL,
                    is_default=not brand.vpn_providers.filter(is_default=True).exists(),
                    configuration={"currency": "T"},
                )
            if (
                options["sync_plans"]
                and provider.status == VPNProvider.ProviderStatus.ACTIVE
            ):
                try:
                    count = sync_connectix_plans.run(provider.pk)
                except Exception as exc:
                    raise CommandError(
                        f"Connectix plan sync failed ({type(exc).__name__}); existing plans retained."
                    ) from None
                self.stdout.write(
                    self.style.SUCCESS(f"Imported {count} Connectix plans.")
                )
