from django.db import migrations, models


def forwards_func(apps, schema_editor):
    UploadsDataset = apps.get_model("uploads", "UploadsDataset")
    UploadsVersion = apps.get_model("uploads", "UploadsVersion")

    datasets = UploadsDataset.objects.all()

    for dataset in datasets:
        dataset.uploads_versions_count = UploadsVersion.objects.filter(dataset=dataset).count()
        dataset.save()


class Migration(migrations.Migration):
    dependencies = [
        ("uploads", "0033_uploadsdataset_uploads_versions_count"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
