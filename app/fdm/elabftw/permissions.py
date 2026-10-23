from django.db.models import Q

from fdm.core.rest.permissions import can_manage_subfolders_in_folder, can_view_in_folder
from fdm.elabftw.models import ExperimentLink
from fdm.folders.models import Folder, FolderPermission, Subfolder
from fdm.uploads.models import UploadsDataset

__all__ = [
    "get_link_target",
    "can_view_link_target",
    "can_edit_link_target",
    "get_link_ancestors",
    "get_effective_links",
    "get_viewable_experiment_links",
]


def get_link_target(target_type: str, target_id):
    """
    Returns the linked object, or None if it doesn't exist.
    """
    model = {
        ExperimentLink.TargetType.FOLDER: Folder,
        ExperimentLink.TargetType.SUBFOLDER: Subfolder,
        ExperimentLink.TargetType.DATASET: UploadsDataset,
    }.get(target_type)

    if not model:
        return None

    try:
        return model.objects.filter(pk=target_id).first()
    except (ValueError, TypeError):
        return None


def _get_folder_id(target):
    if isinstance(target, Folder):
        return target.pk

    return target.folder_id


def can_view_link_target(user, target) -> bool:
    if not target or not user.is_authenticated:
        return False

    folder_id = _get_folder_id(target)

    # Draft files without a folder are only visible to the user who uploaded them
    if not folder_id:
        return target.created_by_id == user.pk

    return can_view_in_folder(user, folder_id)


def can_edit_link_target(user, target) -> bool:
    if not target or not user.is_authenticated:
        return False

    folder_id = _get_folder_id(target)

    if not folder_id:
        return target.created_by_id == user.pk

    return can_manage_subfolders_in_folder(user, folder_id)


def get_link_ancestors(target) -> list[tuple[str, object]]:
    """
    Returns the targets whose experiment links also apply to the given target, nearest first: the subfolders it is
    in and its folder.
    """
    if isinstance(target, Folder):
        return []

    ancestors = []
    subfolder = target.parent if isinstance(target, Subfolder) else target.subfolder

    while subfolder:
        ancestors.append((ExperimentLink.TargetType.SUBFOLDER, subfolder))
        subfolder = subfolder.parent

    if target.folder_id:
        ancestors.append((ExperimentLink.TargetType.FOLDER, target.folder))

    return ancestors


def get_effective_links(target_type: str, target) -> list[ExperimentLink]:
    """
    The links of a target plus those inherited from its subfolders and folder. Each experiment is listed once, from the
    nearest place it is linked; inherited links carry `inherited_from` (the target they were set on).
    """
    links = []
    seen = set()

    for link_target_type, link_target in [(target_type, target), *get_link_ancestors(target)]:
        for link in ExperimentLink.objects.filter(target_type=link_target_type, target_id=link_target.pk):
            key = (link.elabftw_url, link.experiment_id)
            if key in seen:
                continue

            seen.add(key)
            link.inherited_from = None if link_target is target else link_target
            links.append(link)

    return links


def get_viewable_experiment_links(user):
    """
    All experiment links on folders, subfolders and files the user can see.
    """
    folder_ids = FolderPermission.objects.filter(project_membership__member=user).values_list("folder_id", flat=True)

    return ExperimentLink.objects.filter(
        Q(target_type=ExperimentLink.TargetType.FOLDER, target_id__in=folder_ids)
        | Q(
            target_type=ExperimentLink.TargetType.SUBFOLDER,
            target_id__in=Subfolder.objects.filter(folder__in=folder_ids).values_list("pk", flat=True),
        )
        | Q(
            target_type=ExperimentLink.TargetType.DATASET,
            target_id__in=UploadsDataset.objects.filter(
                Q(folder__in=folder_ids) | Q(folder__isnull=True, created_by=user),
            ).values_list("pk", flat=True),
        ),
    )
