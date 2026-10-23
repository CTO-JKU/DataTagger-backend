from django.db.models.signals import post_delete
from django.dispatch import receiver

from fdm.elabftw.models import ExperimentLink
from fdm.folders.models import Folder, Subfolder
from fdm.uploads.models import UploadsDataset

TARGET_TYPES_BY_MODEL = {
    Folder: ExperimentLink.TargetType.FOLDER,
    Subfolder: ExperimentLink.TargetType.SUBFOLDER,
    UploadsDataset: ExperimentLink.TargetType.DATASET,
}


@receiver(post_delete, sender=Folder)
@receiver(post_delete, sender=Subfolder)
@receiver(post_delete, sender=UploadsDataset)
def delete_experiment_links_of_deleted_target(sender, instance, **kwargs):
    ExperimentLink.objects.filter(
        target_type=TARGET_TYPES_BY_MODEL[sender],
        target_id=instance.pk,
    ).delete()
