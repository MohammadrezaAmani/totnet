"""Capabilities that the application has implemented for each provider adapter."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    provision: bool = False
    reconcile: bool = False
    sync_status: bool = False
    sync_usage: bool = False
    renew: bool = False
    suspend: bool = False
    delete: bool = False


_CAPABILITIES = {
    # The captured seller API supports create and client-list reads. The application
    # can reconcile a saved remote ID and sync status/usage from those records.
    "connectix": ProviderCapabilities(provision=True, reconcile=True, sync_status=True),
    # These operations are exercised by the current provisioning service/client.
    # Renewal and lifecycle actions still require separate domain workflows.
    "hiddify": ProviderCapabilities(
        provision=True, reconcile=True, sync_status=True, sync_usage=True, delete=True
    ),
}


def capabilities_for(provider_type: str) -> ProviderCapabilities:
    """Return the implemented capabilities; unknown adapters fail closed."""
    return _CAPABILITIES.get(provider_type, ProviderCapabilities())
