from django.db import migrations


def forwards_func(apps, schema_editor):
    Setting = apps.get_model("dbsettings", "Setting")
    Setting.objects.create(
        key="EXPORT_DOWNLOAD_LINK_LIFETIME_HOURS",
        value="72",
        description="How many hours generated export ZIP download links stay valid",
        public=True,
    )


def reverse_func(apps, schema_editor):
    Setting = apps.get_model("dbsettings", "Setting")
    Setting.objects.filter(
        key="EXPORT_DOWNLOAD_LINK_LIFETIME_HOURS",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dbsettings", "0013_dataset_deletable_within_threshold_setting"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=reverse_func,
        ),
    ]
