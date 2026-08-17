from django.db import migrations


def forwards_func(apps, schema_editor):
    Setting = apps.get_model("dbsettings", "Setting")
    Setting.objects.create(
        key="NOTIFICATION_BANNER_MESSAGE",
        value="",
        description="A message to display on the notification banner",
        public=True,
    )


def reverse_func(apps, schema_editor):
    Setting = apps.get_model("dbsettings", "Setting")
    Setting.objects.filter(
        key="NOTIFICATION_BANNER_MESSAGE",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dbsettings", "00011_migrate_settings_for_public_boolean"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=reverse_func,
        ),
    ]
