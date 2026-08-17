from django.db import migrations


def forwards_func(apps, schema_editor):
    Setting = apps.get_model("dbsettings", "Setting")
    Setting.objects.create(
        key="DATASET_DELETABLE_WITHIN_THRESHOLD",
        value="1440",
        description="How many minutes after publication a dataset can still be deleted",
        public=True,
    )


def reverse_func(apps, schema_editor):
    Setting = apps.get_model("dbsettings", "Setting")
    Setting.objects.filter(
        key="DATASET_DELETABLE_WITHIN_THRESHOLD",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dbsettings", "0012_notification_banner_text_setting"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=reverse_func,
        ),
    ]
