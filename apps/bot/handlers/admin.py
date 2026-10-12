"""Compatibility entry point for the shared, provider-aware admin implementation."""
from .admin_hiddify import AdminRole as AdminRole, HiddifyAdminHandler


class AdminHandler(HiddifyAdminHandler):
    """Keep older imports working without a second, inconsistent admin panel."""
