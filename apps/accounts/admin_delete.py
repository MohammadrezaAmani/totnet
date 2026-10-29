"""Hard deletion from Django admin, including protected dependent records.

The model-level PROTECT rules still apply outside Django admin. Admin deletion
explicitly clears referencing records first, within a single transaction, so
one user/order/plan can be fully removed without leaving an incomplete tree.
"""

from collections import defaultdict

from django.db import transaction
from django.db.models.deletion import ProtectedError


def delete_with_protected_relations(queryset):
    """Delete selected rows and PROTECT dependents (recursively), atomically."""
    using = queryset.db
    model = queryset.model
    primary_keys = set(queryset.values_list("pk", flat=True))
    if not primary_keys:
        return

    with transaction.atomic(using=using):
        _delete_model_rows(model, primary_keys, using=using, active=set())


def _delete_model_rows(model, primary_keys, *, using, active):
    primary_keys = frozenset(primary_keys)
    if not primary_keys:
        return
    marker = (model, primary_keys)
    if marker in active:
        raise ProtectedError("Circular protected deletion dependency", set())

    active.add(marker)
    try:
        seen_blockers = set()
        while True:
            try:
                model._base_manager.db_manager(using).filter(pk__in=primary_keys).delete()
                return
            except ProtectedError as error:
                # PROTECT is evaluated during the collection phase, before
                # the collector deletes anything. Clear these dependencies
                # before retrying the original deletion.
                blockers = defaultdict(set)
                for obj in error.protected_objects:
                    blockers[obj.__class__].add(obj.pk)
                if not blockers:
                    raise

                signature = frozenset(
                    (dependent, pk)
                    for dependent, pks in blockers.items()
                    for pk in pks
                )
                if signature in seen_blockers:
                    raise
                seen_blockers.add(signature)
                for dependent, pks in blockers.items():
                    _delete_model_rows(dependent, pks, using=using, active=active)
    finally:
        active.remove(marker)


class ForceDeleteAdminMixin:
    """Enable standard admin Delete and Delete selected with full DB cleanup."""

    def get_deleted_objects(self, objs, request):
        deleted, counts, _missing_permissions, protected = super().get_deleted_objects(
            objs, request
        )
        # Django's normal confirmation blocks on PROTECT. The deletion service
        # explicitly removes those references, so allow the normal two-step UI.
        if protected:
            deleted.append(
                "رکوردهای وابسته محافظت‌شده نیز همراه این موارد کاملاً حذف می‌شوند."
            )
        return deleted, counts, set(), []

    def delete_model(self, request, obj):
        delete_with_protected_relations(
            self.model._base_manager.filter(pk=obj.pk)
        )

    def delete_queryset(self, request, queryset):
        delete_with_protected_relations(queryset)
