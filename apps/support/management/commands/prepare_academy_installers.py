from django.core.management.base import BaseCommand

from apps.support.downloads import InstallerTooLarge, prepare_installer
from apps.support.models import UsefulContent


class Command(BaseCommand):
    help = "Save academy installers that fit Telegram's cloud upload limit."

    def add_arguments(self, parser):
        parser.add_argument("--brand-id", type=int)

    def handle(self, *args, **options):
        items = UsefulContent.objects.filter(
            is_active=True, category="downloads"
        ).exclude(installer_url="")
        if options["brand_id"]:
            items = items.filter(brand_id=options["brand_id"])
        for item in items.iterator():
            try:
                prepare_installer(item)
                self.stdout.write(f"Ready: {item.title}")
            except InstallerTooLarge:
                self.stdout.write(f"Needs Telegram file_id: {item.title}")
            except Exception as exc:
                self.stderr.write(f"Deferred: {item.title} ({type(exc).__name__})")
