"""
Management command to setup admins and superadmins
Usage:
    python manage.py setup_admin --superadmin username email password
    python manage.py setup_admin --admin username brand_slug
    python manage.py setup_admin --list
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.brands.models import Brand

User = get_user_model()


class Command(BaseCommand):
    help = "Manage admins and superadmins for the platform"

    def add_arguments(self, parser):
        parser.add_argument(
            "--superadmin",
            nargs=3,
            metavar=("USERNAME", "EMAIL", "PASSWORD"),
            help="Create a superadmin user",
        )
        parser.add_argument(
            "--admin",
            nargs=2,
            metavar=("USERNAME", "BRAND_SLUG"),
            help="Add an admin user to a brand",
        )
        parser.add_argument(
            "--remove-admin",
            nargs=2,
            metavar=("USERNAME", "BRAND_SLUG"),
            help="Remove admin access from a user for a brand",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all admins and superadmins",
        )
        parser.add_argument(
            "--brand",
            type=str,
            help="Filter by brand slug (use with --list)",
        )

    def handle(self, *args, **options):
        if options["superadmin"]:
            self.create_superadmin(
                options["superadmin"][0],
                options["superadmin"][1],
                options["superadmin"][2],
            )
        elif options["admin"]:
            self.add_brand_admin(options["admin"][0], options["admin"][1])
        elif options["remove_admin"]:
            self.remove_brand_admin(
                options["remove_admin"][0], options["remove_admin"][1]
            )
        elif options["list"]:
            self.list_admins(options.get("brand"))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Please provide one of the options: --superadmin, --admin, --remove-admin, or --list"
                )
            )

    def create_superadmin(self, username, email, password):
        """Create a superadmin user"""
        try:
            user = User.objects.get(username=username)
            raise CommandError(f"User '{username}' already exists")
        except User.DoesNotExist:
            pass

        try:
            user = User.objects.create_superuser(
                username=username, email=email, password=password
            )
            user.user_type = User.UserType.PLATFORM_OWNER
            user.is_staff = True
            user.is_superuser = True
            user.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Superadmin '{username}' created successfully\n"
                    f"   Email: {email}\n"
                    f"   Access: Full platform access\n"
                    f"   Can manage: All brands"
                )
            )
        except Exception as e:
            raise CommandError(f"Failed to create superadmin: {e}")

    def add_brand_admin(self, username, brand_slug):
        """Add a user as admin to a brand"""
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' not found")

        try:
            brand = Brand.objects.get(slug=brand_slug)
        except Brand.DoesNotExist:
            raise CommandError(f"Brand '{brand_slug}' not found")

        user.admin_brands.add(brand)

        if user.user_type == User.UserType.CUSTOMER:
            user.user_type = User.UserType.BRAND_ADMIN
            user.save()

        if not user.brand:
            user.brand = brand
            user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ User '{username}' added as admin to brand '{brand.name}'\n"
                f"   Brand: {brand.name}\n"
                f"   Slug: {brand.slug}\n"
                f"   User Type: {user.get_user_type_display()}"
            )
        )

    def remove_brand_admin(self, username, brand_slug):
        """Remove admin access from a user for a brand"""
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' not found")

        try:
            brand = Brand.objects.get(slug=brand_slug)
        except Brand.DoesNotExist:
            raise CommandError(f"Brand '{brand_slug}' not found")

        if user.admin_brands.filter(id=brand.id).exists():
            user.admin_brands.remove(brand)
            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Admin access removed for user '{username}' from brand '{brand.name}'"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"⚠️  User '{username}' is not an admin for brand '{brand.name}'"
                )
            )

    def list_admins(self, brand_slug=None):
        """List all admins and superadmins"""
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("🔑 SUPERADMINS & ADMINS"))
        self.stdout.write("=" * 80 + "\n")

        superadmins = User.objects.filter(is_superuser=True)
        if superadmins.exists():
            self.stdout.write(
                self.style.WARNING("🔴 SUPERADMINS (Full Platform Access)")
            )
            self.stdout.write("-" * 80)
            for user in superadmins:
                self.stdout.write(
                    f"  👤 {user.username:<20} | Email: {user.email:<30} | Type: {user.get_user_type_display()}"
                )
            self.stdout.write("")

        brand_admins = User.objects.filter(admin_brands__isnull=False).distinct()

        if brand_slug:
            try:
                brand = Brand.objects.get(slug=brand_slug)
                brand_admins = brand_admins.filter(admin_brands=brand)
                self.stdout.write(
                    self.style.WARNING(f"🟠 BRAND ADMINS for '{brand.name}'")
                )
            except Brand.DoesNotExist:
                raise CommandError(f"Brand '{brand_slug}' not found")
        else:
            self.stdout.write(self.style.WARNING("🟠 BRAND ADMINS"))

        self.stdout.write("-" * 80)

        if brand_admins.exists():
            for user in brand_admins:
                brands_list = ", ".join([b.name for b in user.admin_brands.all()])
                self.stdout.write(
                    f"  👤 {user.username:<20} | Brands: {brands_list:<30} | Type: {user.get_user_type_display()}"
                )
        else:
            if brand_slug:
                self.stdout.write("  No admins found for this brand")
            else:
                self.stdout.write("  No brand admins found")

        self.stdout.write("\n" + "=" * 80 + "\n")

        total_superadmins = User.objects.filter(is_superuser=True).count()
        total_brand_admins = (
            User.objects.filter(admin_brands__isnull=False).distinct().count()
        )
        total_brands = Brand.objects.count()

        self.stdout.write(self.style.SUCCESS("📊 SUMMARY"))
        self.stdout.write("-" * 80)
        self.stdout.write(f"  Superadmins: {total_superadmins}")
        self.stdout.write(f"  Brand Admins: {total_brand_admins}")
        self.stdout.write(f"  Total Brands: {total_brands}")
        self.stdout.write("\n")
