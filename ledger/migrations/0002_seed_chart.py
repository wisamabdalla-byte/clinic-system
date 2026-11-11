from django.db import migrations


def seed(apps, schema_editor):
    from ledger.setup import seed_defaults

    seed_defaults(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
        ("core", "0006_exampleitem_alter_clinicprofile_primary_color"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
