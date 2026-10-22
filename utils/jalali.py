"""Consistent user-visible Jalali dates (storage remains timezone-aware Gregorian)."""

from datetime import date, datetime

import jdatetime
from django.utils import timezone


def jalali_date(value, *, time: bool = False):
    if value is None:
        return "نامشخص"
    if isinstance(value, datetime):
        local = timezone.localtime(value) if timezone.is_aware(value) else value
        converted = jdatetime.datetime.fromgregorian(datetime=local)
        return converted.strftime("%Y/%m/%d %H:%M" if time else "%Y/%m/%d")
    if isinstance(value, date):
        return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")
    raise TypeError("Unsupported date type")
