import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def forwards_func(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Folder = apps.get_model("folders", "Folder")
    UploadsDataset = apps.get_model("uploads", "UploadsDataset")

    # Step 1: Clear all datasets of duplicate active metadata templates from folders
    folders = Folder.objects.all()
    for folder in folders:
        if folder.metadata_template:
            uploads_datasets = UploadsDataset.objects.filter(
                folder=folder,
                metadata_template=folder.metadata_template,
            ).all()

            try:
                uploads_datasets.update(metadata_template=None)
            except Exception as e:
                logger.error(e)
                pass

    # Step 2: Clear all folders of duplicate active metadata templates from projects
    projects = Project.objects.all()
    for project in projects:
        if project.metadata_template:
            folders = Folder.objects.filter(
                project=project,
                metadata_template=project.metadata_template,
            ).all()

            try:
                folders.update(metadata_template=None)
            except Exception as e:
                logger.error(e)
                pass

    # Step 3: Clear all datasets of duplicate active metadata templates from projects
    for project in projects:
        if project.metadata_template:
            uploads_datasets = UploadsDataset.objects.filter(
                folder__project=project,
                metadata_template=project.metadata_template,
            ).all()

            try:
                uploads_datasets.update(metadata_template=None)
            except Exception as e:
                logger.error(e)
                pass


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0014_project_metadata_template"),
        ("folders", "0007_folder_metadata_template"),
        ("uploads", "0036_uploadsversion_metadata_templates"),
        ("metadata", "0042_migrate_uploads_version_file_metadata_ordering"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
