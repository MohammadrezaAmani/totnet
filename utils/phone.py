import re


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_iranian_phone(value: str) -> str:
    """Store local mobile numbers consistently, including Persian/Arabic input."""
    value = re.sub(r"[\s\-()]+", "", value.translate(_DIGITS).strip())
    if value.startswith("+98"):
        value = "0" + value[3:]
    elif value.startswith("0098"):
        value = "0" + value[4:]
    elif value.startswith("98"):
        value = "0" + value[2:]
    elif re.fullmatch(r"9[0-9]{9}", value):
        value = "0" + value
    return value


def is_valid_iranian_phone(value: str) -> bool:
    return bool(re.fullmatch(r"09[0-9]{9}", value))
