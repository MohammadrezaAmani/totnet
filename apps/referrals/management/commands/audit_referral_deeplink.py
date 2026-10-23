"""Inspect and optionally repair provable first-registration referral clicks.

Never attributes users whose first click was made after they were already
registered. No financial rewards are issued by this command.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.referrals.models import Referral, ReferralClick, ReferralLink
from apps.accounts.models import User
from apps.referrals.services import resolve_referral_link
from apps.referrals.services import attribute_referral


class Command(BaseCommand):
    help = "Audit a Telegram /start referral code and repair genuine first-signup attribution"

    def add_arguments(self, parser):
        parser.add_argument("--code", required=True, help="Exact /start code")
        parser.add_argument("--apply", action="store_true", help="Repair verified first-registration clicks")

    def handle(self, *args, **options):
        code = options["code"].strip()
        link = ReferralLink.objects.select_related("brand", "user").filter(code=code).first()
        if link is None:
            legacy_owner = User.objects.select_related("brand").filter(
                referral_code=code, is_active=True, brand__isnull=False,
            ).first()
            if legacy_owner is None:
                raise CommandError(f"No referral link or legacy user referral code found for {code!r}")
            self.stdout.write(self.style.WARNING(
                f"Legacy user referral_code without ReferralLink: brand={legacy_owner.brand.slug}, "
                f"bot=@{legacy_owner.brand.bot_username or '(unset)'}, inviter PK={legacy_owner.pk}"
            ))
            if not options["apply"]:
                self.stdout.write("DRY RUN: no changes made; --apply will materialize the legacy link.")
                return
            link = resolve_referral_link(code=code, brand_id=legacy_owner.brand_id)
            if link is None:
                raise CommandError("Could not safely materialize the legacy referral link")
        self.stdout.write(f"Code: {link.code}")
        self.stdout.write(f"Brand: {link.brand.slug}")
        self.stdout.write(f"Configured bot: @{link.brand.bot_username or '(unset)'}")
        self.stdout.write(f"Active: {link.is_active}")
        self.stdout.write(f"Inviter user PK: {link.user_id or '(none - campaign-only link)'}")
        self.stdout.write(f"Recorded link visits: {link.visits.count()}")
        self.stdout.write(f"Recorded referrals: {Referral.objects.filter(referral_link=link).count()}")
        self.stdout.write(f"Attributed registrations on link: {link.conversion_count}")

        if not link.user_id:
            self.stdout.write(self.style.WARNING(
                "This link has no owning user. Personal referral attribution is impossible until an owner is assigned."
            ))
            return
        if not link.is_active:
            self.stdout.write(self.style.WARNING(
                "This link is inactive. No repairs will be performed."
            ))
            return

        candidates = []
        for click in ReferralClick.objects.filter(
            link=link, brand=link.brand, is_registration=True,
            visitor__referred_by__isnull=True,
        ).select_related("visitor").order_by("pk"):
            # A different link may have been the actual first registration touch.
            first = ReferralClick.objects.filter(
                visitor_id=click.visitor_id, brand=link.brand,
            ).order_by("created_at", "pk").first()
            if first and first.pk == click.pk and click.visitor_id != link.user_id and not Referral.objects.filter(
                referee_id=click.visitor_id, brand=link.brand,
            ).exists():
                candidates.append(click.visitor_id)
        self.stdout.write(f"Unattributed, evidenced FIRST registrations: {len(candidates)}")
        if not options["apply"]:
            self.stdout.write("DRY RUN: no changes made. Use --apply only after reviewing the link/owner.")
            return
        fixed = 0
        with transaction.atomic():
            for user_id in candidates:
                if attribute_referral(
                    user_id=user_id, brand_id=link.brand_id, code=link.code,
                ):
                    fixed += 1
        self.stdout.write(self.style.SUCCESS(
            f"Repaired {fixed} first-signup associations; no points or payments were created."
        ))
