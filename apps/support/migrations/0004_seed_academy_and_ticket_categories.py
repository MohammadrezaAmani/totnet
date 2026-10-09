from django.db import migrations


DOWNLOADS = [
    (
        "connectix",
        "iphone",
        "",
        "کانکتیکس آیفون",
        "https://testflight.apple.com/join/FQkEGDfX",
        "",
        "نصب از طریق TestFlight اپل انجام می\u200cشود.",
    ),
    (
        "connectix",
        "android",
        "priority1",
        "اولویت یک اندروید",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/android/Connectix-arm64-v8a-2.7.3.apk",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/android/Connectix-arm64-v8a-2.7.3.apk",
        "نسخه ۶۴ بیتی برای بیشتر گوشی\u200cهای جدید.",
    ),
    (
        "connectix",
        "android",
        "priority2",
        "اولویت ۲ اندروید",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/android/Connectix-armeabi-v7a-2.7.3.apk",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/android/Connectix-armeabi-v7a-2.7.3.apk",
        "نسخه ۳۲ بیتی برای گوشی\u200cهای قدیمی\u200cتر.",
    ),
    (
        "connectix",
        "android",
        "priority3",
        "اولویت ۳ (شیائومی و سایر)",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/android/Connectix-universal-2.7.3.apk",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/android/Connectix-universal-2.7.3.apk",
        "نسخه عمومی؛ اگر نسخه\u200cهای دیگر نصب نمی\u200cشوند، این نسخه را دریافت کنید.",
    ),
    (
        "connectix",
        "windows",
        "",
        "کانکتیکس ویندوز",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/windows/Connectix-2.7.3+120-windows-setup.zip",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/windows/Connectix-2.7.3+120-windows-setup.zip",
        "فایل را از حالت فشرده خارج و نصب\u200cکننده را اجرا کنید.",
    ),
    (
        "connectix",
        "macos",
        "",
        "کانکتیکس مکینتاش",
        "https://connect-apps.s3.ir-thr-at1.arvanstorage.ir/mac/Connectix-2.7.2+119-macos.dmg",
        "",
        "",
    ),
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
        "برای گوشی\u200cهای جدید اندروید.",
    ),
    (
        "v2rayng",
        "android",
        "arm32",
        "v2rayNG اندروید (گوشی\u200cهای قدیمی)",
        "https://github.com/2dust/v2rayNG/releases/latest",
        "https://github.com/2dust/v2rayNG/releases/download/2.2.6/v2rayNG_2.2.6_armeabi-v7a.apk",
        "برای گوشی\u200cهای ۳۲ بیتی اندروید.",
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
        "https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk",
        "https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk",
        "",
    ),
    (
        "happ",
        "windows",
        "",
        "Happ ویندوز",
        "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
        "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
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
        "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.deb",
        "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.deb",
        "بسته دبیان / اوبونتو، ۶۴ بیتی.",
    ),
]


def seed_content(apps, schema_editor):
    alias = schema_editor.connection.alias
    Brand = apps.get_model("brands", "Brand")
    Content = apps.get_model("support", "UsefulContent")
    Category = apps.get_model("support", "SupportCategory")
    for brand in Brand.objects.using(alias).iterator():
        for index, (
            family,
            platform,
            variant,
            title,
            url,
            installer,
            description,
        ) in enumerate(DOWNLOADS):
            Content.objects.using(alias).get_or_create(
                brand_id=brand.pk,
                category="downloads",
                app_family=family,
                platform=platform,
                variant=variant,
                defaults={
                    "title": title,
                    "download_url": url,
                    "installer_url": installer,
                    "description": description,
                    "display_order": index,
                },
            )
        Content.objects.using(alias).get_or_create(
            brand_id=brand.pk,
            category="internet_tips",
            title="نکاتی برای افزایش کیفیت و سرعت اینترنت شما",
            defaults={
                "content": "۱. برنامه اتصال را از منبع رسمی به‌روز کنید.\n۲. چند برنامه VPN را هم‌زمان روشن نکنید.\n۳. بین وای‌فای و اینترنت موبایل جابه‌جا شوید و کیفیت اتصال را مقایسه کنید.\n۴. دانلود و به‌روزرسانی پس‌زمینه را هنگام نیاز به سرعت بیشتر متوقف کنید.\n۵. در صورت افت سرعت، اتصال دیگری از سرویس خود را انتخاب کنید.\n۶. اگر مشکل ادامه داشت، دستگاه و اپراتور خود را در تیکت مشخص کنید."
            },
        )
        if not Category.objects.using(alias).filter(brand_id=brand.pk).exists():
            for index, title in enumerate(
                ["مشکل اتصال", "مشکل پرداخت", "اشتراک و تمدید", "سایر مسائل"]
            ):
                Category.objects.using(alias).create(
                    brand_id=brand.pk, name=title, display_order=index
                )


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0003_supportticket_device_type_usefulcontent_app_family_and_more"),
    ]
    operations = [migrations.RunPython(seed_content, migrations.RunPython.noop)]
