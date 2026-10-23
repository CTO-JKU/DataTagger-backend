"""
Evaluation exports: all files linked to selected eLabFTW experiments, in their folder structure, plus one metadata
file per experiment describing the experiment, the folders and subfolders and every file.
"""

import csv
import io
import json
import logging
from collections import OrderedDict
from xml.etree import ElementTree

from django.utils import timezone

from fdm.elabftw.models import ElabftwConnection, ExperimentLink, normalize_elabftw_url
from fdm.elabftw.permissions import get_link_target
from fdm.elabftw.services import ElabftwError, get_experiment_details
from fdm.folders.models import Folder, Subfolder
from fdm.uploads.models import UploadsDataset

logger = logging.getLogger(__name__)

__all__ = [
    "datasets_for_link",
    "collect_experiment_export_versions",
    "write_experiment_export",
]


def _experiment_identity(elabftw_url: str, experiment_id: int) -> str:
    return f"elabftw-experiment:{normalize_elabftw_url(elabftw_url)}:{experiment_id}"


def datasets_for_link(link: ExperimentLink):
    target = get_link_target(link.target_type, link.target_id)
    published = UploadsDataset.objects.filter(publication_date__isnull=False)

    if isinstance(target, Folder):
        return published.filter(folder=target)

    if isinstance(target, Subfolder):
        return published.filter(folder=target.folder, subfolder__in=target.get_descendant_pks())

    if isinstance(target, UploadsDataset):
        return [target]

    return []


def collect_experiment_export_versions(links) -> list:
    from fdm.exports.services import DirectoryComponent, ExportVersion, _directory_component

    links_by_experiment = OrderedDict()
    for link in sorted(links, key=lambda link: (link.title.lower(), link.experiment_id)):
        links_by_experiment.setdefault((normalize_elabftw_url(link.elabftw_url), link.experiment_id), []).append(link)

    export_versions = []

    for (elabftw_url, experiment_id), experiment_links in links_by_experiment.items():
        experiment_directory = DirectoryComponent(
            name=f"{experiment_links[0].title or 'Experiment'} (#{experiment_id})",
            identity=_experiment_identity(elabftw_url, experiment_id),
        )

        datasets = {}
        for link in experiment_links:
            for dataset in datasets_for_link(link):
                datasets[dataset.pk] = dataset

        for dataset in sorted(datasets.values(), key=lambda dataset: (dataset.name or "", str(dataset.pk))):
            latest_version = dataset.latest_version
            if not latest_version or not latest_version.version_file_id:
                continue

            directories = [experiment_directory]

            if dataset.folder_id:
                directories.append(_directory_component(dataset.folder, fallback="Folder"))
            else:
                directories.append(DirectoryComponent(name="Drafts", identity="drafts"))

            if dataset.subfolder_id:
                directories += [
                    _directory_component(subfolder, fallback="Subfolder") for subfolder in dataset.subfolder.get_path()
                ]

            export_versions.append(
                ExportVersion(
                    version=latest_version,
                    directories=directories,
                    use_dataset_filename=True,
                    dataset_directory=_directory_component(dataset, fallback="Dataset"),
                    skip_dataset_directory_without_metadata=True,
                ),
            )

    return export_versions


def _simple_metadata(metadata_list) -> list[dict]:
    return [
        {
            "key": metadata.custom_key or (metadata.field.key if metadata.field else None),
            "value": metadata.get_value(),
            "field_type": metadata.field_type,
        }
        for metadata in metadata_list
    ]


def _experiment_details(user, elabftw_url: str, experiment_id: int, title: str) -> dict:
    details = {
        "id": experiment_id,
        "title": title,
        "url": f"{elabftw_url}/experiments.php?mode=view&id={experiment_id}",
        "elabftw_url": elabftw_url,
    }

    connection = ElabftwConnection.objects.filter(user=user, url=elabftw_url).first()

    if not connection:
        details["details_error"] = "Not loaded: the exporting user has no connection to this eLabFTW instance."
        return details

    try:
        details.update(get_experiment_details(connection, experiment_id))
    except ElabftwError as e:
        details["details_error"] = str(e)

    return details


def write_experiment_export(archive, export_versions, export_job, registry) -> None:
    from fdm.exports.services import (
        _build_parent_path,
        _get_export_file_name,
        _write_uploaded_file,
    )

    experiments = OrderedDict()

    for export_version in export_versions:
        parent = _build_parent_path(export_version.directories, registry)
        experiment_identity = export_version.directories[0].identity
        experiment_root = parent[0]
        entry = experiments.setdefault(
            experiment_identity,
            {"root": experiment_root, "files": [], "folders": OrderedDict(), "subfolders": OrderedDict()},
        )

        member_path = None
        if export_job.include_uploads_version_file:
            member_path = registry.member(parent, _get_export_file_name(export_version))
            _write_uploaded_file(archive, export_version.version.version_file, member_path)

        dataset = export_version.version.dataset
        relative_parent = "/".join(parent[1:])

        if dataset.folder_id and dataset.folder_id not in entry["folders"]:
            entry["folders"][dataset.folder_id] = {
                "path": parent[1],
                "name": dataset.folder.name,
                "project": dataset.folder.project.name,
                "metadata": _simple_metadata(dataset.folder.metadata.all()),
            }

        if dataset.subfolder_id:
            for depth, subfolder in enumerate(dataset.subfolder.get_path(), start=2):
                if subfolder.pk not in entry["subfolders"]:
                    entry["subfolders"][subfolder.pk] = {
                        "path": "/".join(parent[1 : depth + 1]),
                        "name": subfolder.name,
                        "metadata": _simple_metadata(subfolder.metadata.all()),
                    }

        version_file = export_version.version.version_file
        entry["files"].append(
            {
                "path": member_path.split("/", 1)[1] if member_path else f"{relative_parent}/{dataset.name}",
                "name": dataset.name,
                "dataset_id": str(dataset.pk),
                "version_id": str(export_version.version.pk),
                "size": version_file.file_size,
                "file_metadata": _simple_metadata(version_file.metadata.all()),
                "metadata": _simple_metadata(export_version.version.metadata.all()),
            },
        )

    if not export_job.include_uploads_version_file_metadata:
        return

    links_by_identity = {}
    for link in ExperimentLink.objects.filter(pk__in=export_job.object_ids):
        links_by_identity.setdefault(_experiment_identity(link.elabftw_url, link.experiment_id), link)

    for identity, entry in experiments.items():
        link = links_by_identity.get(identity)
        payload = {
            "exported_at": timezone.now().isoformat(),
            "experiment": _experiment_details(
                export_job.created_by,
                normalize_elabftw_url(link.elabftw_url),
                link.experiment_id,
                link.title,
            )
            if link
            else None,
            "folders": sorted(entry["folders"].values(), key=lambda item: item["path"]),
            "subfolders": sorted(entry["subfolders"].values(), key=lambda item: item["path"]),
            "files": sorted(entry["files"], key=lambda item: item["path"]),
        }

        file_name = f"metadata.{export_job.metadata_format}"
        archive.writestr(
            registry.member((entry["root"],), file_name),
            _render_payload(payload, export_job.metadata_format),
        )


def _render_payload(payload: dict, metadata_format: str) -> bytes:
    if metadata_format == "csv":
        return _render_csv(payload)

    if metadata_format == "xml":
        from fdm.exports.services import _append_xml_value

        root = ElementTree.Element("evaluation")
        _append_xml_value(root, payload)
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    return json.dumps(payload, indent=2, default=str, ensure_ascii=False).encode("utf-8")


def _csv_value(value):
    return json.dumps(value, default=str, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _render_csv(payload: dict) -> bytes:
    """
    One row per metadata value, which is easy to filter in a spreadsheet or load with pandas.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["level", "path", "key", "value"])

    experiment = payload.get("experiment") or {}
    for key, value in experiment.items():
        writer.writerow(["experiment", "", key, _csv_value(value)])

    for level in ["folders", "subfolders"]:
        for item in payload[level]:
            for metadata in item["metadata"]:
                writer.writerow([level[:-1], item["path"], metadata["key"], _csv_value(metadata["value"])])

    for item in payload["files"]:
        writer.writerow(["file", item["path"], "size", item["size"]])
        for scope in ["file_metadata", "metadata"]:
            for metadata in item[scope]:
                writer.writerow(["file", item["path"], metadata["key"], _csv_value(metadata["value"])])

    return buffer.getvalue().encode("utf-8")
