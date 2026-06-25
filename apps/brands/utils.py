import re
from typing import Any, Dict

TEMPLATE_PATTERN = re.compile(r"\{(\w+)\}")


class SafeTemplateRenderer:
    """
    بسیار ساده، deterministic و امن.
    فقط {key} را replace می‌کند.
    هیچ expression یا code execution ندارد.
    """

    def __init__(self, allowed_keys=None):
        self.allowed_keys = set(allowed_keys or [])

    def render(self, template: str, context: Dict[str, Any]) -> str:
        if not template:
            return ""

        safe_context = self._filter_context(context)

        def replace(match):
            key = match.group(1)
            return str(safe_context.get(key, ""))

        return TEMPLATE_PATTERN.sub(replace, template)

    def _filter_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.allowed_keys:
            return context

        return {k: context.get(k) for k in self.allowed_keys if k in context}


renderer = SafeTemplateRenderer(
    allowed_keys={
        "name",
        "level",
        "wallet",
        "brand",
        "subscriptions",
        "referrals",
    }
)
