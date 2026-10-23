from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fdm.core.helpers import get_content_type_for_model, get_content_type_for_object, get_content_type_instance
from fdm.core.rest.permissions import can_view_in_folder
from fdm.dbsettings.functions import get_dbsettings_value
from fdm.elabftw.models import ExperimentLink
from fdm.exports.models import ExportJob, MetadataTemplateExportJob
from fdm.folders.models import Folder, Subfolder
from fdm.metadata.models import Metadata, MetadataTemplateContainer
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

__all__ = [
    "ALLOWED_EXPORT_MODELS",
    "get_allowed_export_types",
    "normalize_export_type",
    "get_viewable_export_objects",
    "create_export_job",
    "build_export_zip_file",
    "create_metadata_template_export_job",
    "build_metadata_template_export_zip_file",
    "cleanup_expired_metadata_template_export_jobs",
    "delete_metadata_template_export_job",
    "get_metadata_template_export_download_url",
    "delete_export_job",
    "cleanup_expired_export_jobs",
]


ALLOWED_EXPORT_MODELS = [
    Project,
    Folder,
    Subfolder,
    ExperimentLink,
    UploadsDataset,
    UploadsVersion,
]

CHUNK_SIZE = 1024 * 1024


@dataclass
class DirectoryComponent:
    name: str
    identity: str


@dataclass
class ExportVersion:
    version: UploadsVersion
    directories: list[DirectoryComponent]
    use_dataset_filename: bool
    # Holds the dataset's own files and its metadata sidecars
    dataset_directory: DirectoryComponent | None = None
    # Files-only exports of folder structures skip the dataset directory, so the archive mirrors the original
    # directory structure (e.g. of uploaded NMR experiments)
    skip_dataset_directory_without_metadata: bool = False


class ZipNameRegistry:
    def __init__(self):
        self.directory_names_by_identity = {}
        self.used_children = defaultdict(set)
        self.used_members = set()

    def directory(self, parent: tuple[str, ...], directory: DirectoryComponent) -> str:
        identity_key = (parent, directory.identity)
        if identity_key in self.directory_names_by_identity:
            return self.directory_names_by_identity[identity_key]

        component = self._unique_component(parent, directory.name, is_file=False)
        self.directory_names_by_identity[identity_key] = component
        return component

    def member(self, parent: tuple[str, ...], name: str) -> str:
        component = self._unique_component(parent, name, is_file=True)
        path = "/".join([*parent, component])
        self.used_members.add(path)
        return path

    def _unique_component(self, parent: tuple[str, ...], name: str, is_file: bool) -> str:
        component = sanitize_component(name, fallback="export")
        used = self.used_children[parent]

        if component not in used:
            used.add(component)
            return component

        stem, suffix = split_name(component) if is_file else (component, "")
        counter = 1

        while True:
            candidate = f"{stem} ({counter}){suffix}"
            if candidate not in used:
                used.add(candidate)
                return candidate

            counter += 1


def get_allowed_export_types() -> list[str]:
    try:
        if "django_content_type" not in connection.introspection.table_names():
            return []

        return [get_content_type_for_model(model) for model in ALLOWED_EXPORT_MODELS]
    except Exception:
        return []


def normalize_export_type(export_type: str) -> tuple[str, type]:
    try:
        content_type = get_content_type_instance(export_type)
    except Exception as e:
        raise ValidationError(_("Invalid export type.")) from e

    if not content_type:
        raise ValidationError(_("Invalid export type."))

    model = content_type.model_class()

    if model not in ALLOWED_EXPORT_MODELS:
        raise ValidationError(_("Unsupported export type."))

    return get_content_type_for_object(content_type), model


def get_viewable_export_objects(export_type: str, object_ids: list[str], user) -> list:
    normalized_export_type, model = normalize_export_type(export_type)
    queryset = _get_viewable_queryset(model, user).filter(pk__in=object_ids)
    objects_by_pk = {str(instance.pk): instance for instance in queryset}
    objects = []

    for object_id in object_ids:
        instance = objects_by_pk.get(str(object_id))
        if not instance:
            raise PermissionDenied(_("At least one requested export object does not exist or is not viewable."))

        objects.append(instance)

    return objects


def create_export_job(
    *,
    export_job_id=None,
    export_type: str,
    object_ids: list[str],
    metadata_format: str,
    include_uploads_version_file: bool,
    include_uploads_version_file_metadata: bool,
    user,
    download_base_url: str | None = None,
) -> ExportJob:
    exported_uploads_version_ids = get_exported_uploads_version_ids(
        export_type=export_type,
        object_ids=object_ids,
        user=user,
    )
    reusable_export_job = find_reusable_export_job(
        export_type=export_type,
        object_ids=object_ids,
        metadata_format=metadata_format,
        include_uploads_version_file=include_uploads_version_file,
        include_uploads_version_file_metadata=include_uploads_version_file_metadata,
        exported_uploads_version_ids=exported_uploads_version_ids,
    )
    base_attrs = {
        "id": export_job_id,
        "export_type": export_type,
        "object_ids": object_ids,
        "exported_uploads_version_ids": exported_uploads_version_ids,
        "metadata_format": metadata_format,
        "include_uploads_version_file": include_uploads_version_file,
        "include_uploads_version_file_metadata": include_uploads_version_file_metadata,
        "download_base_url": download_base_url,
        "created_by": user,
        "last_modified_by": user,
    }
    base_attrs = {key: value for key, value in base_attrs.items() if value is not None}

    if not reusable_export_job:
        return ExportJob.objects.create(**base_attrs)

    if reusable_export_job.status == ExportJob.Status.FINISHED:
        export_job = ExportJob.objects.create(
            **base_attrs,
            source_export_job=reusable_export_job,
            zip_file=reusable_export_job.zip_file.name,
            expires_at=timezone.now() + timezone.timedelta(hours=get_export_lifetime_hours()),
            finished_at=timezone.now(),
            status=ExportJob.Status.FINISHED,
        )
        export_job.set_download_token(generate_download_token())
        export_job.save(update_fields=["download_token_hash"])
        return export_job

    return ExportJob.objects.create(
        **base_attrs,
        source_export_job=reusable_export_job,
        status=reusable_export_job.status,
    )


def create_metadata_template_export_job(
    *,
    export_job_id=None,
    metadata_template_container_ids: list[str],
    user,
    download_base_url: str | None = None,
) -> "MetadataTemplateExportJob":
    from fdm.exports.models import MetadataTemplateExportJob

    normalized_ids = _normalize_object_ids(metadata_template_container_ids)
    get_viewable_metadata_template_containers(normalized_ids, user)

    base_attrs = {
        "id": export_job_id,
        "metadata_template_container_ids": normalized_ids,
        "download_base_url": download_base_url,
        "created_by": user,
        "last_modified_by": user,
    }
    base_attrs = {key: value for key, value in base_attrs.items() if value is not None}

    return MetadataTemplateExportJob.objects.create(**base_attrs)


def get_viewable_metadata_template_containers(object_ids: list[str], user) -> list[MetadataTemplateContainer]:
    from django.contrib.contenttypes.models import ContentType

    from fdm.folders.models import Folder, FolderPermission
    from fdm.projects.models import ProjectMembership

    normalized_ids = _normalize_object_ids(object_ids)

    query = Q(
        assigned_to_content_type__isnull=True,
        assigned_to_object_id__isnull=True,
    )

    if user and user.is_authenticated:
        query |= Q(
            assigned_to_content_type=ContentType.objects.get_for_model(Project),
            assigned_to_object_id__in=ProjectMembership.objects.filter(
                member=user,
            ).values_list(
                "project__pk",
                flat=True,
            ),
        )
        query |= Q(
            assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            assigned_to_object_id__in=FolderPermission.objects.filter(
                project_membership__member=user,
            ).values_list(
                "folder__pk",
                flat=True,
            ),
        )
        query |= Q(
            assigned_to_content_type=ContentType.objects.get_for_model(UploadsDataset),
            assigned_to_object_id__in=UploadsDataset.objects.filter(
                Q(
                    publication_date__isnull=True,
                    created_by=user,
                )
                | Q(
                    folder__folderpermission__project_membership__member=user,
                ),
            ).values_list(
                "pk",
                flat=True,
            ),
        )

    queryset = MetadataTemplateContainer.objects.filter(query, pk__in=normalized_ids).distinct()
    containers_by_pk = {str(container.pk): container for container in queryset}
    containers = []

    for object_id in normalized_ids:
        container = containers_by_pk.get(str(object_id))
        if not container:
            raise PermissionDenied(
                _("At least one requested metadata template container does not exist or is not viewable."),
            )

        if not container.latest_metadata_template:
            raise ValidationError(_("At least one requested metadata template container has no metadata template."))

        containers.append(container)

    return containers


def get_exported_uploads_version_ids(*, export_type: str, object_ids: list[str], user) -> list[str]:
    export_objects = get_viewable_export_objects(export_type, object_ids, user)
    export_versions = _collect_export_versions(export_type, export_objects, user)

    return [str(export_version.version.pk) for export_version in export_versions]


def find_reusable_export_job(
    *,
    export_type: str,
    object_ids: list[str],
    metadata_format: str,
    include_uploads_version_file: bool,
    include_uploads_version_file_metadata: bool,
    exported_uploads_version_ids: list[str],
) -> ExportJob | None:
    matching_jobs = ExportJob.objects.filter(
        export_type=export_type,
        object_ids=object_ids,
        exported_uploads_version_ids=exported_uploads_version_ids,
        metadata_format=metadata_format,
        include_uploads_version_file=include_uploads_version_file,
        include_uploads_version_file_metadata=include_uploads_version_file_metadata,
    ).order_by("creation_date")

    finished_jobs = (
        matching_jobs.filter(
            status=ExportJob.Status.FINISHED,
            expires_at__gt=timezone.now(),
        )
        .exclude(zip_file="")
        .exclude(zip_file__isnull=True)
        .order_by("-expires_at", "-creation_date")
    )

    for finished_job in finished_jobs:
        if _export_zip_file_exists(finished_job):
            return finished_job

    return (
        matching_jobs.filter(
            source_export_job__isnull=True,
            status__in=[
                ExportJob.Status.SCHEDULED,
                ExportJob.Status.IN_PROGRESS,
            ],
        )
        .order_by("creation_date")
        .first()
    )


def build_export_zip_file(export_job: ExportJob) -> str:
    user = export_job.created_by
    if not user:
        raise PermissionDenied(_("The export has no owner."))

    export_objects = get_viewable_export_objects(export_job.export_type, export_job.object_ids, user)
    export_versions = _collect_export_versions(export_job.export_type, export_objects, user)
    exports_root = Path(settings.MEDIA_ROOT) / "exports"
    tmp_root = exports_root / "tmp"
    final_root = exports_root / str(export_job.pk)
    tmp_root.mkdir(parents=True, exist_ok=True)
    final_root.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_root / f"{export_job.pk}.zip.tmp"
    final_name = _get_export_zip_file_name(export_job)
    final_path = final_root / final_name
    relative_path = Path("exports") / str(export_job.pk) / final_name

    registry = ZipNameRegistry()

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            # Evaluation exports of eLabFTW experiments keep the folder structure and add one metadata file per experiment
            if normalize_export_type(export_job.export_type)[1] == ExperimentLink:
                from fdm.elabftw.exports import write_experiment_export

                write_experiment_export(archive, export_versions, export_job, registry)
                export_versions = []

            for export_version in export_versions:
                _write_export_version(
                    archive=archive,
                    export_version=export_version,
                    metadata_format=export_job.metadata_format,
                    include_uploads_version_file=export_job.include_uploads_version_file,
                    include_uploads_version_file_metadata=export_job.include_uploads_version_file_metadata,
                    registry=registry,
                )

        os.replace(tmp_path, final_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return relative_path.as_posix()


def build_metadata_template_export_zip_file(export_job: "MetadataTemplateExportJob") -> str:
    user = export_job.created_by
    if not user:
        raise PermissionDenied(_("The export has no owner."))

    containers = get_viewable_metadata_template_containers(export_job.metadata_template_container_ids, user)
    exports_root = Path(settings.MEDIA_ROOT) / "exports"
    tmp_root = exports_root / "tmp"
    final_root = exports_root / str(export_job.pk)
    tmp_root.mkdir(parents=True, exist_ok=True)
    final_root.mkdir(parents=True, exist_ok=True)

    tmp_path = tmp_root / f"{export_job.pk}.zip.tmp"
    final_name = _get_metadata_template_export_zip_file_name(export_job)
    final_path = final_root / final_name
    relative_path = Path("exports") / str(export_job.pk) / final_name
    registry = ZipNameRegistry()

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for container in containers:
                member_path = registry.member(tuple(), f"{container.name}.json")
                archive.writestr(
                    member_path,
                    json.dumps(_metadata_template_container_payload(container), indent=2, default=str).encode("utf-8"),
                )

        os.replace(tmp_path, final_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return relative_path.as_posix()


def cleanup_expired_export_jobs() -> int:
    expired_jobs = ExportJob.objects.filter(
        Q(expires_at__isnull=False, expires_at__lte=timezone.now())
        | (
            Q(status=ExportJob.Status.ERROR)
            & Q(creation_date__lte=_failed_job_cutoff())
            & (Q(zip_file="") | Q(zip_file__isnull=True))
        ),
    )
    deleted_count = 0

    for export_job in expired_jobs:
        delete_export_job(export_job)
        deleted_count += 1

    return deleted_count


def cleanup_expired_metadata_template_export_jobs() -> int:
    from fdm.exports.models import MetadataTemplateExportJob

    expired_jobs = MetadataTemplateExportJob.objects.filter(
        Q(expires_at__isnull=False, expires_at__lte=timezone.now())
        | (
            Q(status=MetadataTemplateExportJob.Status.ERROR)
            & Q(creation_date__lte=_failed_job_cutoff())
            & (Q(zip_file="") | Q(zip_file__isnull=True))
        ),
    )
    deleted_count = 0

    for export_job in expired_jobs:
        delete_metadata_template_export_job(export_job)
        deleted_count += 1

    return deleted_count


def delete_export_job(export_job: ExportJob) -> None:
    _delete_export_zip_file(export_job)
    export_job.delete()


def delete_metadata_template_export_job(export_job: "MetadataTemplateExportJob") -> None:
    _delete_export_zip_file(export_job)
    export_job.delete()


def get_export_lifetime_hours() -> int:
    try:
        return int(get_dbsettings_value("EXPORT_DOWNLOAD_LINK_LIFETIME_HOURS", "72"))
    except (TypeError, ValueError):
        return 72


def generate_download_token() -> str:
    return secrets.token_urlsafe(32)


def get_download_url(export_job: ExportJob, token: str) -> str:
    base_url = export_job.download_base_url
    if not base_url:
        path = reverse("export-download", kwargs={"pk": export_job.pk})
        base_url = f"{settings.FRONTEND_WEB_URL}{path}"

    return f"{base_url}?token={token}"


def get_metadata_template_export_download_url(export_job: "MetadataTemplateExportJob", token: str) -> str:
    base_url = export_job.download_base_url
    if not base_url:
        path = reverse("metadata-template-export-download", kwargs={"pk": export_job.pk})
        base_url = f"{settings.FRONTEND_WEB_URL}{path}"

    return f"{base_url}?token={token}"


def _get_export_zip_file_name(export_job: ExportJob) -> str:
    _, model = normalize_export_type(export_job.export_type)
    app_title = get_dbsettings_value("APP_TITLE", "FDM")
    export_date = timezone.localtime(export_job.creation_date).strftime("%Y-%m-%d %H-%M-%S")
    stem = sanitize_component(
        f"{app_title} {'Evaluation' if model == ExperimentLink else model.__name__} export from {export_date}",
        fallback="export",
    )

    return f"{stem[:251].rstrip('. ')}.zip"


def _get_metadata_template_export_zip_file_name(export_job: "MetadataTemplateExportJob") -> str:
    app_title = get_dbsettings_value("APP_TITLE", "FDM")
    export_date = timezone.localtime(export_job.creation_date).strftime("%Y-%m-%d %H-%M-%S")
    stem = sanitize_component(
        f"{app_title} Metadata template export from {export_date}",
        fallback="metadata-template-export",
    )

    return f"{stem[:251].rstrip('. ')}.zip"


def _metadata_template_container_payload(container: MetadataTemplateContainer) -> dict:
    latest_template = container.latest_metadata_template
    if not latest_template:
        raise ValidationError(_("The metadata template container has no metadata template."))

    return {
        "name": container.name,
        "metadata_template_fields": [
            _metadata_template_field_payload(field)
            for field in latest_template.metadata_template_fields.all().order_by("order", "custom_key")
        ],
    }


def _metadata_template_field_payload(field) -> dict:
    payload = {
        "field_type": field.field_type,
        "value": field.get_value(),
        "config": field.config,
        "mandatory": field.mandatory,
        "order": field.order if field.order is not None else 0,
    }

    if field.field_id:
        payload["field"] = str(field.field_id)
    else:
        payload["custom_key"] = field.custom_key

    return payload


def _normalize_object_ids(object_ids: list[str]) -> list[str]:
    normalized_ids = []
    seen_ids = set()

    for object_id in object_ids:
        normalized_id = str(object_id)
        if normalized_id not in seen_ids:
            normalized_ids.append(normalized_id)
            seen_ids.add(normalized_id)

    return normalized_ids


def _get_viewable_queryset(model, user):
    if model == Project:
        return Project.objects.filter(project_members__member=user).distinct()

    if model == Folder:
        return Folder.objects.filter(folderpermission__project_membership__member=user).distinct()

    if model == Subfolder:
        return Subfolder.objects.filter(folder__folderpermission__project_membership__member=user).distinct()

    if model == ExperimentLink:
        from fdm.elabftw.permissions import get_viewable_experiment_links

        return get_viewable_experiment_links(user)

    if model == UploadsDataset:
        return UploadsDataset.objects.filter(
            Q(publication_date__isnull=True, created_by=user)
            | Q(folder__folderpermission__project_membership__member=user),
        ).distinct()

    if model == UploadsVersion:
        return UploadsVersion.objects.filter(
            Q(dataset__folder__isnull=True, publication_date__isnull=True, created_by=user)
            | Q(dataset__folder__folderpermission__project_membership__member=user),
        ).distinct()

    return model.objects.none()


def _collect_export_versions(export_type: str, export_objects: list, user) -> list[ExportVersion]:
    model = normalize_export_type(export_type)[1]
    export_versions = []

    if model == Project:
        for project in export_objects:
            project_dir = _directory_component(project, fallback=_("Project"))
            folders = Folder.objects.filter(
                project=project,
                folderpermission__project_membership__member=user,
            ).order_by("name")

            for folder in folders:
                export_versions += _collect_subfolder_dataset_versions(
                    UploadsDataset.objects.filter(
                        folder=folder,
                        publication_date__isnull=False,
                    ).order_by("name", "creation_date"),
                    [project_dir, _directory_component(folder, fallback=_("Folder"))],
                )

    elif model == Folder:
        for folder in export_objects:
            export_versions += _collect_subfolder_dataset_versions(
                UploadsDataset.objects.filter(
                    folder=folder,
                    publication_date__isnull=False,
                ).order_by("name", "creation_date"),
                [_directory_component(folder, fallback=_("Folder"))],
            )

    elif model == Subfolder:
        for subfolder in export_objects:
            export_versions += _collect_subfolder_dataset_versions(
                UploadsDataset.objects.filter(
                    folder=subfolder.folder,
                    subfolder__in=subfolder.get_descendant_pks(),
                    publication_date__isnull=False,
                ).order_by("name", "creation_date"),
                [],
                root_subfolder=subfolder,
            )

    elif model == ExperimentLink:
        from fdm.elabftw.exports import collect_experiment_export_versions

        export_versions += collect_experiment_export_versions(export_objects)

    elif model == UploadsDataset:
        export_versions += _collect_dataset_versions(export_objects, [])

    elif model == UploadsVersion:
        for version in export_objects:
            export_versions.append(
                ExportVersion(
                    version=version,
                    directories=[],
                    use_dataset_filename=False,
                ),
            )

    return [export_version for export_version in export_versions if export_version.version.version_file_id]


def _collect_dataset_versions(datasets, base_directories: list[DirectoryComponent]) -> list[ExportVersion]:
    export_versions = []

    for dataset in datasets:
        latest_version = dataset.latest_version
        if not latest_version or not latest_version.version_file_id:
            continue

        export_versions.append(
            ExportVersion(
                version=latest_version,
                directories=list(base_directories),
                use_dataset_filename=True,
                dataset_directory=_directory_component(dataset, fallback=_("Dataset")),
            ),
        )

    return export_versions


def _collect_subfolder_dataset_versions(
    datasets,
    base_directories: list[DirectoryComponent],
    root_subfolder: Subfolder | None = None,
) -> list[ExportVersion]:
    """
    Places each dataset below the directories of its subfolder path. When exporting a single subfolder, the path
    starts at that subfolder instead of the top level of the folder.
    """
    export_versions = []
    directories_by_subfolder = {}

    for dataset in datasets:
        directories = list(base_directories)

        if dataset.subfolder_id:
            if dataset.subfolder_id not in directories_by_subfolder:
                path = dataset.subfolder.get_path()
                if root_subfolder:
                    path = path[[subfolder.pk for subfolder in path].index(root_subfolder.pk) :]

                directories_by_subfolder[dataset.subfolder_id] = [
                    _directory_component(subfolder, fallback=_("Subfolder")) for subfolder in path
                ]

            directories += directories_by_subfolder[dataset.subfolder_id]

        for export_version in _collect_dataset_versions([dataset], directories):
            export_version.skip_dataset_directory_without_metadata = True
            export_versions.append(export_version)

    return export_versions


def _write_export_version(
    archive: zipfile.ZipFile,
    export_version: ExportVersion,
    metadata_format: str,
    include_uploads_version_file: bool,
    include_uploads_version_file_metadata: bool,
    registry: ZipNameRegistry,
) -> None:
    version = export_version.version
    version_file = version.version_file
    directories = export_version.directories
    if export_version.dataset_directory and (
        include_uploads_version_file_metadata or not export_version.skip_dataset_directory_without_metadata
    ):
        directories = [*directories, export_version.dataset_directory]
    parent = _build_parent_path(directories, registry)

    if include_uploads_version_file:
        file_name = _get_export_file_name(export_version)
        file_member_path = registry.member(parent, file_name)
        _write_uploaded_file(archive, version_file, file_member_path)

    if include_uploads_version_file_metadata:
        file_metadata_member_path = registry.member(parent, f"file-metadata.{metadata_format}")
        additional_metadata_member_path = registry.member(parent, f"additional-metadata.{metadata_format}")
        archive.writestr(
            file_metadata_member_path,
            _render_file_metadata_sidecar(version, metadata_format),
        )
        archive.writestr(
            additional_metadata_member_path,
            _render_uploads_version_metadata_sidecar(version, metadata_format),
        )


def _build_parent_path(directories: list[DirectoryComponent], registry: ZipNameRegistry) -> tuple[str, ...]:
    parent = tuple()

    for directory in directories:
        parent = (*parent, registry.directory(parent, directory))

    return parent


def _directory_component(instance, fallback: str) -> DirectoryComponent:
    name = getattr(instance, "name", None) or getattr(instance, "display_name", None) or fallback

    return DirectoryComponent(
        name=str(name),
        identity=f"{get_content_type_for_model(instance.__class__)}:{instance.pk}",
    )


def _get_export_file_name(export_version: ExportVersion) -> str:
    version = export_version.version
    version_file = version.version_file

    if export_version.use_dataset_filename:
        dataset_name = sanitize_component(version.dataset.name if version.dataset else None, fallback="")
        if dataset_name:
            _, dataset_suffix = split_name(dataset_name)
            _, file_suffix = split_name(version_file.name)
            if not dataset_suffix and file_suffix:
                return f"{dataset_name}{file_suffix}"

            return dataset_name

        return sanitize_component(version_file.name, fallback="file")

    return (
        sanitize_component(_get_metadata_value(version_file, "ORIGINAL_FILE_NAME"), fallback="")
        or sanitize_component(_get_metadata_value(version_file, "FILE_NAME"), fallback="")
        or sanitize_component(version_file.name, fallback="file")
    )


def _write_uploaded_file(archive: zipfile.ZipFile, version_file: UploadsVersionFile, member_path: str) -> None:
    uploaded_file = version_file.uploaded_file
    uploaded_file.open("rb")

    try:
        with archive.open(member_path, "w") as target:
            for chunk in uploaded_file.chunks(chunk_size=CHUNK_SIZE):
                target.write(chunk)
    finally:
        uploaded_file.close()


def _render_file_metadata_sidecar(version: UploadsVersion, metadata_format: str) -> bytes:
    payload = _get_metadata_payload(version.version_file.metadata.all())
    return _render_metadata_payload(payload, metadata_format)


def _render_uploads_version_metadata_sidecar(version: UploadsVersion, metadata_format: str) -> bytes:
    payload = _get_metadata_payload(version.metadata.all())
    return _render_metadata_payload(payload, metadata_format)


def _render_metadata_payload(payload: dict, metadata_format: str) -> bytes:

    if metadata_format == ExportJob.MetadataFormat.JSON:
        return json.dumps(payload, indent=2, default=str).encode("utf-8")

    if metadata_format == ExportJob.MetadataFormat.CSV:
        return _render_metadata_csv(payload)

    if metadata_format == ExportJob.MetadataFormat.XML:
        return _render_metadata_xml(payload)

    raise ValidationError(_("Unsupported metadata format."))


def _get_metadata_payload(metadata_list) -> dict:
    return {
        "metadata": [_metadata_payload(metadata) for metadata in metadata_list],
    }


def _metadata_payload(metadata: Metadata) -> dict:
    return {
        "pk": str(metadata.pk),
        "scope": (
            get_content_type_for_object(metadata.assigned_to_content_type)
            if metadata.assigned_to_content_type_id
            else None
        ),
        "assigned_to_object_id": str(metadata.assigned_to_object_id) if metadata.assigned_to_object_id else None,
        "key": metadata.custom_key or (metadata.field.key if metadata.field else None),
        "field_type": metadata.field_type,
        "read_only": metadata.read_only,
        "value": metadata.get_value(),
        "config": metadata.config,
        "previous_version_field": (
            str(metadata.previous_version_field_id) if metadata.previous_version_field_id else None
        ),
        "metadata_template_field": (
            str(metadata.metadata_template_field_id) if metadata.metadata_template_field_id else None
        ),
        "mandatory": metadata.metadata_template_field.mandatory if metadata.metadata_template_field else False,
        "order": metadata.order if metadata.order is not None else 0,
    }


def _render_metadata_csv(payload: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "scope",
            "assigned_to_object_id",
            "key",
            "field_type",
            "value",
            "read_only",
            "mandatory",
            "order",
        ],
    )
    writer.writeheader()

    for item in payload["metadata"]:
        writer.writerow(
            {
                "scope": item["scope"],
                "assigned_to_object_id": item["assigned_to_object_id"],
                "key": item["key"],
                "field_type": item["field_type"],
                "value": json.dumps(item["value"], default=str)
                if isinstance(item["value"], (dict, list))
                else item["value"],
                "read_only": item["read_only"],
                "mandatory": item["mandatory"],
                "order": item["order"],
            },
        )

    return buffer.getvalue().encode("utf-8")


def _render_metadata_xml(payload: dict) -> bytes:
    root = ElementTree.Element("export")
    metadata_element = ElementTree.SubElement(root, "metadata")
    for item in payload["metadata"]:
        item_element = ElementTree.SubElement(metadata_element, "item")
        _append_xml_value(item_element, item)

    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _append_xml_value(element, value) -> None:
    if value is None:
        element.set("nil", "true")
    elif isinstance(value, dict):
        for key, child_value in value.items():
            child = ElementTree.SubElement(element, str(key))
            _append_xml_value(child, child_value)
    elif isinstance(value, list):
        for child_value in value:
            child = ElementTree.SubElement(element, "value")
            _append_xml_value(child, child_value)
    else:
        element.text = str(value)


def _get_metadata_value(relation, custom_key: str):
    metadata = relation.metadata.filter(custom_key=custom_key).first()
    if not metadata:
        return None

    return metadata.get_value()


def sanitize_component(value, fallback: str = "export") -> str:
    if value is None:
        value = ""

    value = str(value)
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    value = re.sub(r'[<>:"|?*]', " ", value)
    value = value.replace("/", " ").replace("\\", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip(". ")

    if not value or value in {".", ".."}:
        return fallback

    return value[:255]


def split_name(name: str) -> tuple[str, str]:
    stem, suffix = os.path.splitext(name)
    return stem or name, suffix


def _failed_job_cutoff():
    return timezone.now() - timezone.timedelta(hours=get_export_lifetime_hours())


def _delete_export_zip_file(export_job: ExportJob) -> None:
    if not export_job.zip_file:
        return

    if ExportJob.objects.exclude(pk=export_job.pk).filter(zip_file=export_job.zip_file.name).exists():
        return

    try:
        zip_path = Path(export_job.zip_file.path).resolve()
        exports_root = (Path(settings.MEDIA_ROOT) / "exports").resolve()
        if zip_path.is_relative_to(exports_root) and zip_path.exists():
            export_job.zip_file.delete(save=False)
    except Exception:
        return


def _export_zip_file_exists(export_job: ExportJob) -> bool:
    if not export_job.zip_file:
        return False

    try:
        return export_job.zip_file.storage.exists(export_job.zip_file.name)
    except Exception:
        return False
