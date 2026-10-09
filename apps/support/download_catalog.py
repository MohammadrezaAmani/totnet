"""Official download defaults; brand-specific admin edits take precedence."""

CONNECTIX_BASE = "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir"
HAPP_BASE = "https://github.com/Happ-proxy"


def app_downloads():
    return [
        (
            "connectix",
            "iphone",
            "",
            "کانکتیکس آیفون",
            "https://testflight.apple.com/join/FQkEGDfX",
            "",
            "نصب از طریق TestFlight اپل انجام می‌شود.",
        ),
        (
            "connectix",
            "android",
            "priority1",
            "اولویت یک اندروید",
            f"{CONNECTIX_BASE}/android/Connectix-arm64-v8a-2.7.3.apk",
            f"{CONNECTIX_BASE}/android/Connectix-arm64-v8a-2.7.3.apk",
            "نسخه ۶۴ بیتی برای بیشتر گوشی‌های جدید.",
        ),
        (
            "connectix",
            "android",
            "priority2",
            "اولویت ۲ اندروید",
            f"{CONNECTIX_BASE}/android/Connectix-armeabi-v7a-2.7.3.apk",
            f"{CONNECTIX_BASE}/android/Connectix-armeabi-v7a-2.7.3.apk",
            "نسخه ۳۲ بیتی برای گوشی‌های قدیمی‌تر.",
        ),
        (
            "connectix",
            "android",
            "priority3",
            "اولویت ۳ (شیائومی و سایر)",
            f"{CONNECTIX_BASE}/android/Connectix-universal-2.7.3.apk",
            f"{CONNECTIX_BASE}/android/Connectix-universal-2.7.3.apk",
            "نسخه عمومی؛ اگر نسخه‌های دیگر نصب نمی‌شوند، این نسخه را دریافت کنید.",
        ),
        (
            "connectix",
            "windows",
            "",
            "کانکتیکس ویندوز",
            f"{CONNECTIX_BASE}/windows/Connectix-2.7.3+120-windows-setup.zip",
            f"{CONNECTIX_BASE}/windows/Connectix-2.7.3+120-windows-setup.zip",
            "فایل را از حالت فشرده خارج و نصب‌کننده را اجرا کنید.",
        ),
        (
            "connectix",
            "macos",
            "",
            "کانکتیکس مکینتاش",
            f"{CONNECTIX_BASE}/mac/Connectix-2.7.2+119-macos.dmg",
            "",
            "",
        ),
        # The publisher's current DEB URL returns 404. Keep a working official page
        # until a new installer/file_id is supplied, rather than ship a broken URL.
        (
            "connectix",
            "linux",
            "",
            "کانکتیکس لینوکس",
            "https://connectix.space/fa/#downloads",
            "",
            "نسخه لینوکس را از صفحه رسمی دانلودها دریافت کنید.",
        ),
        (
            "v2rayng",
            "android",
            "arm64",
            "v2rayNG اندروید (نسخه اصلی)",
            "https://github.com/2dust/v2rayNG/releases/latest",
            "https://github.com/2dust/v2rayNG/releases/download/2.2.6/v2rayNG_2.2.6_arm64-v8a.apk",
            "برای گوشی‌های جدید اندروید.",
        ),
        (
            "v2rayng",
            "android",
            "arm32",
            "v2rayNG اندروید (گوشی‌های قدیمی)",
            "https://github.com/2dust/v2rayNG/releases/latest",
            "https://github.com/2dust/v2rayNG/releases/download/2.2.6/v2rayNG_2.2.6_armeabi-v7a.apk",
            "برای گوشی‌های ۳۲ بیتی اندروید.",
        ),
        (
            "happ",
            "iphone",
            "",
            "Happ آیفون",
            "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
            "",
            "",
        ),
        (
            "happ",
            "android",
            "",
            "Happ اندروید",
            f"{HAPP_BASE}/happ-android/releases/latest/download/Happ.apk",
            f"{HAPP_BASE}/happ-android/releases/latest/download/Happ.apk",
            "",
        ),
        (
            "happ",
            "windows",
            "",
            "Happ ویندوز",
            f"{HAPP_BASE}/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
            f"{HAPP_BASE}/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
            "",
        ),
        (
            "happ",
            "macos",
            "",
            "Happ مکینتاش",
            "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
            "",
            "",
        ),
        (
            "happ",
            "linux",
            "",
            "Happ لینوکس",
            f"{HAPP_BASE}/happ-desktop/releases/latest/download/Happ.linux.x64.deb",
            f"{HAPP_BASE}/happ-desktop/releases/latest/download/Happ.linux.x64.deb",
            "بسته دبیان / اوبونتو، ۶۴ بیتی.",
        ),
    ]


def seed_academy(brand, content_model):
    for index, (
        family,
        platform,
        variant,
        title,
        link,
        installer,
        description,
    ) in enumerate(app_downloads()):
        content_model.objects.get_or_create(
            brand_id=brand.pk,
            category="downloads",
            app_family=family,
            platform=platform,
            variant=variant,
            defaults={
                "title": title,
                "download_url": link,
                "installer_url": installer,
                "description": description,
                "display_order": index,
            },
        )
    content_model.objects.get_or_create(
        brand_id=brand.pk,
        category="internet_tips",
        title="نکاتی برای افزایش کیفیت و سرعت اینترنت شما",
        defaults={
            "content": "۱. برنامه اتصال را از منبع رسمی به‌روز کنید.\n۲. چند برنامه VPN را هم‌زمان روشن نکنید.\n۳. بین وای‌فای و اینترنت موبایل جابه‌جا شوید و کیفیت اتصال را مقایسه کنید.\n۴. برنامه‌های دانلود و به‌روزرسانی پس‌زمینه را هنگام نیاز به سرعت بیشتر متوقف کنید.\n۵. در صورت افت سرعت، اتصال دیگری از سرویس خود را انتخاب کنید.\n۶. اگر مشکل ادامه داشت، دستگاه و اپراتور خود را در تیکت پشتیبانی مشخص کنید."
        },
    )
