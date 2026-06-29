from django.db import migrations, models


def set_netuid_to_12(apps, schema_editor):
    # Before multi-netuid support, this proxy exclusively served compute horde (netuid 12).
    # Backfill existing rows so they remain valid after the migration instead of getting
    # stranded with the placeholder default of 0.
    Validator = apps.get_model("core", "Validator")
    Validator.objects.update(netuid=12)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="validator",
            name="netuid",
            field=models.IntegerField(default=0),
        ),
        migrations.RunPython(set_netuid_to_12, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="validator",
            name="public_key",
            field=models.TextField(),
        ),
        migrations.AlterUniqueTogether(
            name="validator",
            unique_together={("public_key", "netuid")},
        ),
    ]
