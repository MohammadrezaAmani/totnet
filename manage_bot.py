#!/usr/bin/env python

import asyncio
import os
import sys
from pathlib import Path

import django

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import logging  # noqa

from apps.bot.dispatcher import start_bots  # noqa
from apps.brands.models import Brand  # noqa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)

logger = logging.getLogger(__name__)


async def main():
    """Main function to start all brand bots"""
    try:
        logger.info("Starting Multi-Tenant VPN Bot Platform...")

        while True:
            brand_count = await Brand.objects.filter(
                status=Brand.BrandStatus.ACTIVE, bot_token__isnull=False
            ).acount()

            if brand_count > 0:
                logger.info(f"Found {brand_count} active brands, initializing bots...")
                await start_bots()
                break
            else:
                logger.info(
                    "No active brands found. Waiting for brands to be configured..."
                )
                await asyncio.sleep(5)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "init":
            from django.core.management import execute_from_command_line

            execute_from_command_line(["manage.py", "migrate"])
            execute_from_command_line(["manage.py", "collectstatic", "--noinput"])
            print("Database initialized. Create a superuser:")
            execute_from_command_line(["manage.py", "createsuperuser"])
        elif command == "webhook":
            from aiohttp import web

            from apps.bot.dispatcher import create_webhook_app

            async def start_webhook():
                await start_bots()
                app = create_webhook_app()
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, "localhost", 8080)
                await site.start()
                logger.info("Webhook server started on http://localhost:8080")
                await asyncio.Event().wait()

            asyncio.run(start_webhook())
        else:
            print("Usage:")
            print("  python manage_bot.py        - Start bots in polling mode")
            print("  python manage_bot.py init   - Initialize database")
            print("  python manage_bot.py webhook - Start webhook server")
    else:
        asyncio.run(main())
