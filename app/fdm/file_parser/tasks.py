import logging
import uuid

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from fdm._celery import app
from fdm.core.helpers import set_request_for_user
from fdm.file_parser.models import *
from fdm.file_parser.registry import get_configurable_parser_types, get_parser_definition
from fdm.uploads.models import UploadsVersionFile

logger = logging.getLogger(__name__)


def prepare_uploaded_file_processing(file: UploadsVersionFile) -> None:
    for parser_type in [
        FileParser.Type.MIME_TYPE,
        FileParser.Type.CHECKSUM_SHA256,
        FileParser.Type.FILE_INFORMATION,
    ]:
        FileParser.objects.get_or_create(
            parser_type=parser_type,
            file=file,
            configuration=None,
        )
    for configuration in get_configured_file_parsers_for_file(file):
        FileParser.objects.get_or_create(
            parser_type=configuration.parser_type,
            file=file,
            configuration=configuration,
            defaults={"configuration_snapshot": configuration.get_snapshot()},
        )


def dispatch_uploaded_file_processing(file_id: str) -> None:
    try:
        process_uploaded_file.apply_async((file_id,))
        logger.info("Uploaded File Processing dispatched file_id=%s", file_id)
    except Exception:
        logger.exception("Could not dispatch Uploaded File Processing for file '%s'", file_id)


@shared_task
def reconcile_uploaded_file_processing() -> None:
    now = timezone.now()
    legacy_cutoff = now - timezone.timedelta(seconds=settings.UPLOADED_FILE_PROCESSING_LEGACY_GRACE_SECONDS)
    files = (
        UploadsVersionFile.objects.filter(
            Q(
                status=UploadsVersionFile.Status.SCHEDULED,
                processing_lease_token__isnull=True,
            )
            | Q(
                status__in=[UploadsVersionFile.Status.SCHEDULED, UploadsVersionFile.Status.IN_PROGRESS],
                processing_lease_expires_at__lte=now,
            )
            | Q(
                status=UploadsVersionFile.Status.IN_PROGRESS,
                processing_lease_token__isnull=True,
                last_modification_date__lte=legacy_cutoff,
            ),
        )
        .exclude(uploaded_file=None)
        .distinct()
    )

    for file in files:
        UploadsVersionFile.objects.filter(pk=file.pk).update(
            status=UploadsVersionFile.Status.SCHEDULED,
            processing_lease_token=None,
            processing_lease_expires_at=None,
            processing_heartbeat_at=None,
        )
        logger.info("Uploaded File Processing lease recovered file_id=%s", file.pk)
        dispatch_uploaded_file_processing(str(file.pk))


@shared_task
def process_uploaded_file(file_id: str) -> None:
    started_at = timezone.now()
    claim = _claim_uploaded_file(file_id)
    if not claim:
        logger.info("Uploaded File Processing claim skipped file_id=%s", file_id)
        return
    file, lease_token = claim
    logger.info("Uploaded File Processing claim acquired file_id=%s lease_token=%s", file.pk, lease_token)

    set_request_for_file_user(file)

    parser_specs = [
        (FileParser.Type.MIME_TYPE, MimeTypeParser, {"set_metadata": True}),
        (
            FileParser.Type.CHECKSUM_SHA256,
            ChecksumParser,
            {"algorithm": FileParser.Type.CHECKSUM_SHA256, "set_metadata": True},
        ),
        (FileParser.Type.FILE_INFORMATION, FileInformationParser, {"set_metadata": True}),
    ]
    has_error = False
    for parser_type, parser_class, parser_args in parser_specs:
        has_error = _run_file_parser(file, parser_type, parser_class, parser_args) or has_error
        if not _renew_processing_lease(file.pk, lease_token):
            return

    if ExifParser(file).check_eligible_mime_type():
        has_error = _run_file_parser(file, FileParser.Type.EXIF_DATA, ExifParser, {"set_metadata": True}) or has_error
        if not _renew_processing_lease(file.pk, lease_token):
            return

    mime_failed = file.file_parser.filter(
        parser_type=FileParser.Type.MIME_TYPE,
        status=FileParser.Status.ERROR,
    ).exists()
    if not mime_failed:
        configured_tasks = file.file_parser.exclude(configuration_snapshot={}).order_by("creation_date")
        for configured_task in configured_tasks:
            parser_definition = get_parser_definition(configured_task.parser_type)
            if parser_definition and not parser_definition.is_eligible(file):
                configured_task.delete()
                continue

            has_error = _run_configured_parser(configured_task) or has_error
            if not _renew_processing_lease(file.pk, lease_token):
                return

    final_status = UploadsVersionFile.Status.ERROR if has_error else UploadsVersionFile.Status.FINISHED
    updated = UploadsVersionFile.objects.filter(pk=file.pk, processing_lease_token=lease_token).update(
        status=final_status,
        processing_lease_token=None,
        processing_lease_expires_at=None,
        processing_heartbeat_at=None,
    )
    if updated:
        logger.info(
            "Uploaded File Processing finished file_id=%s status=%s duration_seconds=%.3f",
            file.pk,
            final_status,
            (timezone.now() - started_at).total_seconds(),
        )
    else:
        logger.warning("Uploaded File Processing lease lost before completion file_id=%s", file.pk)


def _claim_uploaded_file(file_id: str) -> tuple[UploadsVersionFile, uuid.UUID] | None:
    with transaction.atomic():
        try:
            file = UploadsVersionFile.objects.select_for_update().get(pk=file_id)
        except UploadsVersionFile.DoesNotExist:
            return None

        now = timezone.now()
        if file.status not in [UploadsVersionFile.Status.SCHEDULED, UploadsVersionFile.Status.IN_PROGRESS]:
            return None
        if file.processing_lease_token and file.processing_lease_expires_at and file.processing_lease_expires_at > now:
            return None

        lease_token = uuid.uuid4()
        file.status = UploadsVersionFile.Status.IN_PROGRESS
        file.processing_lease_token = lease_token
        file.processing_heartbeat_at = now
        file.processing_lease_expires_at = now + timezone.timedelta(
            seconds=settings.UPLOADED_FILE_PROCESSING_LEASE_SECONDS,
        )
        file.save(
            update_fields=[
                "status",
                "processing_lease_token",
                "processing_heartbeat_at",
                "processing_lease_expires_at",
            ],
        )
        return file, lease_token


def _renew_processing_lease(file_id: str, lease_token: uuid.UUID) -> bool:
    now = timezone.now()
    return bool(
        UploadsVersionFile.objects.filter(pk=file_id, processing_lease_token=lease_token).update(
            processing_heartbeat_at=now,
            processing_lease_expires_at=now
            + timezone.timedelta(seconds=settings.UPLOADED_FILE_PROCESSING_LEASE_SECONDS),
        ),
    )


def _run_file_parser(file: UploadsVersionFile, parser_type: str, parser_class: type, parser_args: dict) -> bool:
    parser_task, _ = FileParser.objects.get_or_create(
        parser_type=parser_type,
        file=file,
        configuration=None,
    )
    if parser_task.status == FileParser.Status.FINISHED:
        return False
    if parser_task.status == FileParser.Status.ERROR:
        return True

    parser_task.status = FileParser.Status.IN_PROGRESS
    parser_task.save(update_fields=["status"])
    try:
        parser_class(file).parse(**parser_args)
        parser_task.status = FileParser.Status.FINISHED
        logger.info("Uploaded File Processing parser finished file_id=%s parser_type=%s", file.pk, parser_type)
        return False
    except Exception:
        parser_task.status = FileParser.Status.ERROR
        logger.exception("Parser '%s' failed for uploaded version file '%s'", parser_type, file.pk)
        return True
    finally:
        parser_task.save(update_fields=["status"])


def _run_configured_parser(parser_task: FileParser) -> bool:
    if parser_task.status == FileParser.Status.FINISHED:
        return False
    if parser_task.status == FileParser.Status.ERROR:
        return True

    parser_task.status = FileParser.Status.IN_PROGRESS
    parser_task.save(update_fields=["status"])
    try:
        parser_definition = get_parser_definition(parser_task.parser_type)
        if not parser_definition:
            raise NotImplementedError(f"Parser '{parser_task.parser_type}' is not available.")

        snapshot = parser_task.configuration_snapshot
        parser_definition.runner_class(parser_task.file).parse(
            config=snapshot.get("config", {}),
            output_metadata_key=snapshot.get("output_metadata_key"),
            set_metadata=True,
        )
        parser_task.status = FileParser.Status.FINISHED
        logger.info(
            "Uploaded File Processing configured parser finished file_id=%s parser_type=%s",
            parser_task.file_id,
            parser_task.parser_type,
        )
        return False
    except Exception:
        parser_task.status = FileParser.Status.ERROR
        logger.exception(
            "Configured parser '%s' failed for uploaded version file '%s'",
            parser_task.parser_type,
            parser_task.file_id,
        )
        return True
    finally:
        parser_task.save(update_fields=["status"])


def set_request_for_file_user(file: UploadsVersionFile) -> None:
    user = file.last_modified_by or file.created_by or get_user_model().objects.order_by("pk").first()
    if user:
        set_request_for_user(user)


def clear_parser_task(parser_type: str, file: any, configuration: FileParserConfiguration | None = None) -> None:
    queryset = FileParser.objects.filter(
        parser_type=parser_type,
        file=file,
    )

    if configuration:
        queryset = queryset.filter(configuration=configuration)
    else:
        queryset = queryset.filter(configuration__isnull=True)

    queryset.delete()


def add_parser_task(
    parser_type: str,
    file: any,
    configuration: FileParserConfiguration | None = None,
    configuration_snapshot: dict | None = None,
) -> None:
    # Clear old parser task if the exact same one is being rescheduled
    clear_parser_task(
        parser_type=parser_type,
        file=file,
        configuration=configuration,
    )

    # Create a new parser task to avoid having old errors lying around
    FileParser.objects.create(
        parser_type=parser_type,
        file=file,
        configuration=configuration,
        configuration_snapshot=configuration_snapshot or {},
        status=FileParser.Status.SCHEDULED,
    )


@shared_task
def determine_tasks_for_files():
    files = UploadsVersionFile.objects.filter(
        status=UploadsVersionFile.Status.SCHEDULED,
    ).exclude(
        uploaded_file=None,
    )

    for file in files:
        # set request user to user that last updated the file so last_modified_by is automatically set correctly
        set_request_for_file_user(file)
        check_tasks_for_file(file)
        file.status = UploadsVersionFile.Status.IN_PROGRESS
        file.save()


def check_tasks_for_file(file: UploadsVersionFile):
    check_calculate_checksum(file, FileParser.Type.CHECKSUM_SHA256)
    check_extract_exif_data(file)
    check_extract_file_information(file)
    check_configured_file_parsers(file)


@shared_task
def update_status_for_files():
    files = UploadsVersionFile.objects.filter(
        status=UploadsVersionFile.Status.IN_PROGRESS,
    )

    for file in files:
        # set request user to user that last updated the file so last_modified_by is automatically set correctly
        set_request_for_file_user(file)
        total_tasks_for_file = FileParser.objects.filter(
            file=file,
        )

        failed_tasks_for_file = total_tasks_for_file.filter(
            status=FileParser.Status.ERROR,
        )

        finished_tasks_for_file = total_tasks_for_file.filter(
            status=FileParser.Status.FINISHED,
        )

        if failed_tasks_for_file.count():
            file.status = UploadsVersionFile.Status.ERROR
            file.save()
        elif total_tasks_for_file.count() == finished_tasks_for_file.count():
            file.status = UploadsVersionFile.Status.FINISHED
            file.save()


def check_calculate_checksum(file: UploadsVersionFile, parser_type: str):
    # set request user to user that last updated the file so last_modified_by is automatically set correctly
    set_request_for_file_user(file)

    add_parser_task(
        parser_type=parser_type,
        file=file,
    )

    logger.info(f"File '{file}' needs '{parser_type}' task")


@shared_task
def calculate_checksum(parser_type: str):
    generic_parser_task(
        parser_type=parser_type,
        parser_class=ChecksumParser,
        parser_args=dict(
            algorithm=parser_type,
            set_metadata=True,
        ),
    )


def check_extract_exif_data(file: UploadsVersionFile):
    parser_type = FileParser.Type.EXIF_DATA

    if ExifParser(file).check_eligible_mime_type():
        # set request user to user that last updated the file so last_modified_by is automatically set correctly
        set_request_for_file_user(file)

        add_parser_task(
            parser_type=parser_type,
            file=file,
        )

        logger.info(f"File '{file}' needs '{parser_type}' task")


@shared_task
def extract_exif_data():
    generic_parser_task(
        parser_type=FileParser.Type.EXIF_DATA,
        parser_class=ExifParser,
        parser_args=dict(
            set_metadata=True,
        ),
    )


def check_extract_file_information(file: UploadsVersionFile):
    parser_type = FileParser.Type.FILE_INFORMATION

    # set request user to user that last updated the file so last_modified_by is automatically set correctly
    set_request_for_file_user(file)

    add_parser_task(
        parser_type=parser_type,
        file=file,
    )

    logger.info(f"File '{file}' needs '{parser_type}' task")


def check_configured_file_parsers(file: UploadsVersionFile):
    configs = get_configured_file_parsers_for_file(file)

    for config in configs:
        schedule_configured_file_parser(file=file, config=config)


def get_configured_file_parsers_for_file(file: UploadsVersionFile) -> list[FileParserConfiguration]:
    uploads_version = (
        file.uploads_versions.select_related(
            "dataset",
            "dataset__folder",
            "dataset__folder__project",
        )
        .order_by("creation_date")
        .first()
    )

    if not uploads_version or not uploads_version.dataset:
        return []

    dataset = uploads_version.dataset
    folder = dataset.folder
    project = folder.project if folder else None
    scopes = []

    if project:
        scopes.append(project)
    if folder:
        scopes.append(folder)
    scopes.append(dataset)

    configs = []
    for scope in scopes:
        scope_configs = FileParserConfiguration.objects.filter(
            assigned_to_content_type=scope.get_content_type(),
            assigned_to_object_id=scope.pk,
            enabled=True,
        ).order_by("creation_date")

        configs.extend(scope_configs)

    return configs


def schedule_configured_file_parser(
    file: UploadsVersionFile,
    config: FileParserConfiguration,
    retry_errors: bool = False,
) -> dict:
    parser_definition = get_parser_definition(config.parser_type)
    if not parser_definition or not parser_definition.is_eligible(file):
        return {
            "configuration_id": str(config.pk),
            "parser_type": config.parser_type,
            "action": "ineligible",
            "status": None,
        }

    finished_task = FileParser.objects.filter(
        parser_type=config.parser_type,
        file=file,
        configuration=config,
        status=FileParser.Status.FINISHED,
    ).first()
    if finished_task:
        return serialize_parser_processing_action(finished_task, "already_finished")

    existing_task = (
        FileParser.objects.filter(
            parser_type=config.parser_type,
            file=file,
            configuration=config,
        )
        .exclude(status=FileParser.Status.FINISHED)
        .order_by("-creation_date")
        .first()
    )
    if existing_task:
        if retry_errors and existing_task.status == FileParser.Status.ERROR:
            existing_task.status = FileParser.Status.SCHEDULED
            existing_task.configuration_snapshot = config.get_snapshot()
            existing_task.save()
            return serialize_parser_processing_action(existing_task, "retried")

        return serialize_parser_processing_action(existing_task, "already_scheduled")

    # set request user to user that last updated the file so last_modified_by is automatically set correctly
    set_request_for_file_user(file)

    task = FileParser.objects.create(
        parser_type=config.parser_type,
        file=file,
        configuration=config,
        configuration_snapshot=config.get_snapshot(),
        status=FileParser.Status.SCHEDULED,
    )

    logger.info(f"File '{file}' needs configured '{config.parser_type}' task")

    return serialize_parser_processing_action(task, "scheduled")


def serialize_parser_processing_action(task: FileParser, action: str) -> dict:
    return {
        "pk": str(task.pk),
        "configuration_id": str(task.configuration_id) if task.configuration_id else None,
        "parser_type": task.parser_type,
        "status": task.status,
        "action": action,
    }


def schedule_configured_file_parsers_for_file(file: UploadsVersionFile, retry_errors: bool = False) -> list[dict]:
    return [
        schedule_configured_file_parser(
            file=file,
            config=config,
            retry_errors=retry_errors,
        )
        for config in get_configured_file_parsers_for_file(file)
    ]


def schedule_existing_dataset_files_for_configuration(config: FileParserConfiguration) -> list[dict]:
    from fdm.uploads.models import UploadsDataset

    if config.assigned_to_content_type != UploadsDataset.get_content_type():
        return []

    files = UploadsVersionFile.objects.filter(
        uploads_versions__dataset_id=config.assigned_to_object_id,
    ).distinct()

    return [schedule_configured_file_parser(file=file, config=config) for file in files]


@shared_task
def extract_file_information():
    generic_parser_task(
        parser_type=FileParser.Type.FILE_INFORMATION,
        parser_class=FileInformationParser,
        parser_args=dict(
            set_metadata=True,
        ),
    )


def generic_parser_task(parser_type, parser_class, parser_args):
    tasks = FileParser.objects.filter(
        parser_type=parser_type,
        status=FileParser.Status.SCHEDULED,
    )[: settings.CELERY_MAX_FILE_PARSER_TASKS_PER_INTERVAL]

    for task in tasks:
        # set request user to user that last updated the file so last_modified_by is automatically set correctly
        set_request_for_file_user(task.file)

        task.status = FileParser.Status.IN_PROGRESS
        task.save()

        try:
            parser_class(task.file).parse(**parser_args)

            task.status = FileParser.Status.FINISHED

            logger.info(f"Task '{parser_type}' for file '{task.file}' succeeded")
        except Exception as e:
            task.status = FileParser.Status.ERROR

            task.file.status = UploadsVersionFile.Status.ERROR
            task.file.save()

            logger.error(f"Task '{parser_type}' for file '{task.file}' failed: {e}")
        finally:
            task.save()


@shared_task
def run_configured_file_parsers():
    tasks = FileParser.objects.filter(
        parser_type__in=get_configurable_parser_types(),
        status=FileParser.Status.SCHEDULED,
    )[: settings.CELERY_MAX_FILE_PARSER_TASKS_PER_INTERVAL]

    for task in tasks:
        # set request user to user that last updated the file so last_modified_by is automatically set correctly
        set_request_for_file_user(task.file)

        task.status = FileParser.Status.IN_PROGRESS
        task.save()

        try:
            parser_definition = get_parser_definition(task.parser_type)
            if not parser_definition:
                raise NotImplementedError(f"Parser '{task.parser_type}' is not available.")

            snapshot = task.configuration_snapshot
            if not snapshot and task.configuration_id:
                snapshot = task.configuration.get_snapshot()

            parser_definition.runner_class(task.file).parse(
                config=snapshot.get("config", {}),
                output_metadata_key=snapshot.get("output_metadata_key"),
                set_metadata=True,
            )

            task.status = FileParser.Status.FINISHED

            logger.info(f"Configured task '{task.parser_type}' for file '{task.file}' succeeded")
        except Exception as e:
            task.status = FileParser.Status.ERROR

            logger.error(f"Configured task '{task.parser_type}' for file '{task.file}' failed: {e}")
        finally:
            task.save()
