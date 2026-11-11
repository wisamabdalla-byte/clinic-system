"""Keep the ledger in step with the finance app: every save/delete of a money document
re-syncs its journal entry (see ledger/posting.py)."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import posting
from .engine import unsync_source
from .models import Party


def _connect(model_label, fn):
    from django.apps import apps

    model = apps.get_model(model_label)

    def on_save(sender, instance, raw=False, **kwargs):
        if not raw:
            fn(instance)

    def on_delete(sender, instance, **kwargs):
        unsync_source(model_label, instance.pk)

    post_save.connect(on_save, sender=model, weak=False, dispatch_uid=f"ledger-save-{model_label}")
    post_delete.connect(on_delete, sender=model, weak=False, dispatch_uid=f"ledger-del-{model_label}")


for _label, _fn in posting.SYNCERS.items():
    _connect(_label, _fn)


@receiver(post_save, sender="clinic.Patient", dispatch_uid="ledger-patient-name")
def patient_renamed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    Party.objects.filter(patient=instance).exclude(name=instance.full_name).update(name=instance.full_name)

