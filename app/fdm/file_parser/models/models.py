import csv
import hashlib
import logging
import uuid

import magic
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from PIL import Image
from PIL.ExifTags import TAGS

from fdm.core.helpers import clean_assigned_content_type
from fdm.core.models import BaseModel, TimestampMixin
from fdm.folders.models import Folder
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import Metadata
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersionFile

__all__ = [
    "FileParser",
    "FileParserConfiguration",
    "ChecksumParser",
    "CsvValueParser",
    "ExifParser",
    "MimeTypeParser",
    "FileInformationParser",
    "validate_csv_value_config",
    "validate_csv_extraction_rules_config",
]

logger = logging.getLogger(__name__)


RESERVED_FILE_METADATA_KEYS = [
    "CHECKSUM_SHA256",
    "FILE_NAME",
    "FILE_RELATIVE_PATH",
    "FILE_SIZE",
    "MIME_TYPE",
    "ORIGINAL_FILE_NAME",
    "ORIGINAL_FILE_PATH",
]


def _validate_non_negative_int(config: dict, key: str):
    value = config.get(key)

    if not isinstance(value, int) or value < 0:
        raise ValidationError({key: _("This value must be a non-negative integer.")})


def _validate_csv_cell_reference(config: dict, field_name: str) -> None:
    value = config.get(field_name)

    if not isinstance(value, dict):
        raise ValidationError({field_name: _("This value must be a CSV cell reference object.")})

    _validate_non_negative_int(value, "row")
    _validate_non_negative_int(value, "column")


def validate_csv_value_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValidationError({"config": _("CSV parser config must be an object.")})

    mode = config.get("mode")
    if mode not in ["coordinates", "header"]:
        raise ValidationError({"mode": _("CSV parser mode must be 'coordinates' or 'header'.")})

    _validate_non_negative_int(config, "row_index")

    delimiter = config.get("delimiter", None)
    if delimiter is not None and (not isinstance(delimiter, str) or len(delimiter) != 1):
        raise ValidationError({"delimiter": _("CSV delimiter must be a single character.")})

    encoding = config.get("encoding", None)
    if encoding is not None and not isinstance(encoding, str):
        raise ValidationError({"encoding": _("CSV encoding must be a string.")})

    if mode == "coordinates":
        _validate_non_negative_int(config, "column_index")
    elif mode == "header":
        header = config.get("header")
        if not isinstance(header, str) or not header:
            raise ValidationError({"header": _("CSV header must be a non-empty string.")})

        _validate_non_negative_int(config, "header_row_index")


def validate_csv_extraction_rules_config(config: list[dict]) -> None:
    if not isinstance(config, list):
        raise ValidationError({"config": _("CSV parser config must be a list of extraction rules.")})

    if not config:
        raise ValidationError({"config": _("CSV parser config must contain at least one extraction rule.")})

    for index, extraction_rule in enumerate(config):
        if not isinstance(extraction_rule, dict):
            raise ValidationError({"config": _(f"CSV extraction rule at index {index} must be an object.")})

        _validate_non_negative_int(extraction_rule, "row")
        _validate_non_negative_int(extraction_rule, "column")

        key = extraction_rule.get("key")
        if isinstance(key, str):
            if not key:
                raise ValidationError({"key": _("CSV extraction rule key must be a non-empty string.")})
        elif isinstance(key, dict):
            _validate_csv_cell_reference(extraction_rule, "key")
        else:
            raise ValidationError(
                {"key": _("CSV extraction rule key must be a string or a CSV cell reference object.")},
            )


class FileParserConfiguration(BaseModel, TimestampMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    parser_type = models.CharField(
        max_length=64,
        choices=[
            ("CSV_VALUE", _("CSV value")),
        ],
    )

    assigned_to_content_type = models.ForeignKey(
        ContentType,
        blank=False,
        null=False,
        on_delete=models.CASCADE,
    )

    assigned_to_object_id = models.UUIDField(
        blank=False,
        null=False,
    )

    assigned_to_content_object = GenericForeignKey(
        ct_field="assigned_to_content_type",
        fk_field="assigned_to_object_id",
    )

    enabled = models.BooleanField(
        default=True,
    )

    output_metadata_key = models.CharField(
        max_length=128,
        blank=True,
        null=True,
    )

    config = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "assigned_to_content_type",
                    "assigned_to_object_id",
                ],
            ),
        ]

    def clean(self):
        clean_assigned_content_type(
            assigned_to_content_type=self.assigned_to_content_type,
            assigned_to_object_id=self.assigned_to_object_id,
            allowed_content_models=[
                Project,
                Folder,
                UploadsDataset,
            ],
        )

        self.output_metadata_key = self.output_metadata_key.upper() if self.output_metadata_key else None

        if self.output_metadata_key and (
            self.output_metadata_key in RESERVED_FILE_METADATA_KEYS or self.output_metadata_key.startswith("EXIF_")
        ):
            raise ValidationError({"output_metadata_key": _("This metadata key is reserved by built-in parsers.")})

        from fdm.file_parser.registry import get_parser_definition

        parser_definition = get_parser_definition(self.parser_type)
        if parser_definition is None:
            raise ValidationError({"parser_type": _("This parser type is not available.")})

        if self.output_metadata_key:
            validate_csv_value_config(self.config)
        else:
            parser_definition.validate_config(self.config)

    def save(self, *args, **kwargs):
        self.output_metadata_key = self.output_metadata_key.upper() if self.output_metadata_key else None
        super().save(*args, **kwargs)

    def get_snapshot(self) -> dict:
        return {
            "configuration_id": str(self.pk),
            "parser_type": self.parser_type,
            "output_metadata_key": self.output_metadata_key,
            "config": self.config,
            "assigned_to_content_type": str(self.assigned_to_content_type),
            "assigned_to_object_id": str(self.assigned_to_object_id),
        }

    def __str__(self):
        return f"{self.parser_type} for {self.assigned_to_content_type} ({self.assigned_to_object_id})"


class FileParser(BaseModel, TimestampMixin):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", _("Scheduled")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        ERROR = "ERROR", _("Error")
        FINISHED = "FINISHED", _("Finished")

    class Type(models.TextChoices):
        CHECKSUM_SHA256 = "CHECKSUM_SHA256", _("Checksum SHA256")
        CSV_VALUE = "CSV_VALUE", _("CSV value")
        EXIF_DATA = "EXIF_DATA", _("EXIF data")
        MIME_TYPE = "MIME_TYPE", _("MIME type")
        FILE_INFORMATION = "FILE_INFORMATION", _("File information")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    parser_type = models.CharField(
        max_length=64,
        choices=Type.choices,
    )

    file = models.ForeignKey(
        UploadsVersionFile,
        related_name="file_parser",
        on_delete=models.CASCADE,
    )

    configuration = models.ForeignKey(
        FileParserConfiguration,
        blank=True,
        null=True,
        related_name="file_parser",
        on_delete=models.SET_NULL,
    )

    configuration_snapshot = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    def __str__(self):
        return f"{self.parser_type} for {self.file}"


class ChecksumParser:
    file: UploadsVersionFile

    def __init__(self, file: UploadsVersionFile):
        self.file = file

    def parse(self, algorithm: str = FileParser.Type.CHECKSUM_SHA256, set_metadata: bool = False):
        if algorithm == FileParser.Type.CHECKSUM_SHA256:
            hasher = hashlib.sha256()
        else:
            raise NotImplementedError(_("Hash algorithm not implemented."))

        try:
            checksum = self._calculate_checksum(hasher)

            if set_metadata:
                self.set_metadata(
                    algorithm=algorithm,
                    checksum=checksum,
                )

            return checksum
        except Exception as e:
            logger.error(f"Could not retrieve checksum from file {self.file}: {e}")
            return None

    def _calculate_checksum(self, hasher) -> str:
        with self.file.uploaded_file.open("rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def set_metadata(self, algorithm: str, checksum: str):
        Metadata.objects.filter(
            assigned_to_content_type=self.file.get_content_type(),
            assigned_to_object_id=self.file.pk,
            custom_key=algorithm,
        ).delete()

        set_metadata(
            assigned_to_content_type=self.file.get_content_type(),
            assigned_to_object_id=self.file.pk,
            custom_key=algorithm,
            value=checksum,
            read_only=True,
        )


class CsvValueParser:
    file: UploadsVersionFile

    allowed_mime_types = [
        "application/csv",
        "text/csv",
    ]

    def __init__(self, file: UploadsVersionFile):
        self.file = file

    def parse(self, config: list[dict] | dict, output_metadata_key: str | None = None, set_metadata: bool = False):
        if output_metadata_key:
            validate_csv_value_config(config)
            value = self._get_value(config=config)
            extracted_metadata = [
                {
                    "key": output_metadata_key,
                    "value": value,
                },
            ]
        else:
            value = None
            validate_csv_extraction_rules_config(config)
            extracted_metadata = self._get_extraction_rule_values(config=config)

        if set_metadata:
            self.set_metadata(
                extracted_metadata=extracted_metadata,
                replace_existing=bool(output_metadata_key),
            )

        return value if output_metadata_key else extracted_metadata

    def get_mime_type(self) -> str:
        try:
            return magic.from_file(self.file.uploaded_file.path, mime=True)
        except Exception as e:
            logger.error(f"Could not retrieve MIME type from file {self.file}: {e}")
            return ""

    def get_stored_mime_type(self) -> str:
        metadata = (
            Metadata.objects.filter(
                assigned_to_content_type=self.file.get_content_type(),
                assigned_to_object_id=self.file.pk,
                custom_key="MIME_TYPE",
            )
            .order_by("-creation_date")
            .first()
        )

        return metadata.get_value() if metadata else ""

    def check_eligible_file(self) -> bool:
        return self.get_stored_mime_type() in self.allowed_mime_types

    def _get_value(self, config: dict):
        rows = self._get_rows(config=config)
        mode = config["mode"]

        if mode == "coordinates":
            row_index = config["row_index"]
            column_index = config["column_index"]
        else:
            header_row_index = config["header_row_index"]
            header = config["header"]
            row_index = header_row_index + 1 + config["row_index"]

            try:
                column_index = rows[header_row_index].index(header)
            except IndexError:
                raise ValidationError({"header_row_index": _("CSV header row does not exist.")})
            except ValueError:
                raise ValidationError({"header": _("CSV header does not exist.")})

        try:
            return rows[row_index][column_index]
        except IndexError:
            raise ValidationError({"config": _("CSV cell does not exist.")})

    def _get_cell_value(self, rows: list[list[str]], row_index: int, column_index: int):
        try:
            return rows[row_index][column_index]
        except IndexError:
            raise ValidationError({"config": _("CSV cell does not exist.")})

    def _get_extraction_rule_values(self, config: list[dict]) -> list[dict]:
        rows = self._get_rows(config={})
        extracted_metadata = []

        for extraction_rule in config:
            key_config = extraction_rule["key"]
            if isinstance(key_config, dict):
                custom_key = self._get_cell_value(
                    rows=rows,
                    row_index=key_config["row"],
                    column_index=key_config["column"],
                )
            else:
                custom_key = key_config

            extracted_metadata.append(
                {
                    "key": custom_key,
                    "value": self._get_cell_value(
                        rows=rows,
                        row_index=extraction_rule["row"],
                        column_index=extraction_rule["column"],
                    ),
                }
            )

        return extracted_metadata

    def _get_rows(self, config: dict) -> list[list[str]]:
        encoding = config.get("encoding") or "utf-8-sig"

        with self.file.uploaded_file.open("rb") as f:
            content = f.read().decode(encoding)

        delimiter = config.get("delimiter")
        if not delimiter:
            try:
                dialect = csv.Sniffer().sniff(content[:2048])
            except csv.Error:
                dialect = csv.excel
            return list(csv.reader(content.splitlines(), dialect=dialect))

        return list(csv.reader(content.splitlines(), delimiter=delimiter))

    def set_metadata(self, extracted_metadata: list[dict], replace_existing: bool = False):
        with transaction.atomic():
            for item in extracted_metadata:
                custom_key = item["key"].upper()

                if replace_existing:
                    Metadata.objects.filter(
                        assigned_to_content_type=self.file.get_content_type(),
                        assigned_to_object_id=self.file.pk,
                        custom_key=custom_key,
                    ).delete()

                set_metadata(
                    assigned_to_content_type=self.file.get_content_type(),
                    assigned_to_object_id=self.file.pk,
                    custom_key=custom_key,
                    value=item["value"],
                    read_only=True,
                )


class ExifParser:
    file: UploadsVersionFile

    allowed_mime_types = [
        "image/heic",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
    ]

    def __init__(self, file: UploadsVersionFile):
        self.file = file

    def parse(self, set_metadata: bool = False):
        if not self.check_eligible_mime_type():
            return {}

        exif_data = self._get_exif_data()

        if set_metadata:
            self.set_metadata(
                exif_data=exif_data,
            )

        return exif_data

    def get_mime_type(self) -> str:
        try:
            return magic.from_file(self.file.uploaded_file.path, mime=True)
        except Exception as e:
            logger.error(f"Could not retrieve MIME type from file {self.file}: {e}")
            return ""

    def check_eligible_mime_type(self) -> bool:
        return self.get_mime_type() in self.allowed_mime_types

    def _get_exif_data(self) -> dict:
        try:
            with Image.open(self.file.uploaded_file.path) as image_file:
                exif_data = image_file.getexif()

                exif_table = {}
                for exif_key, exif_value in exif_data.items():
                    exif_tag = TAGS.get(exif_key)
                    exif_table[exif_tag] = exif_value

                return exif_table
        except Exception as e:
            logger.error(f"Could not retrieve EXIF data from file {self.file}: {e}")
            return {}

    def set_metadata(self, exif_data: dict):
        Metadata.objects.filter(
            assigned_to_content_type=self.file.get_content_type(),
            assigned_to_object_id=self.file.pk,
            custom_key__startswith="EXIF_",
        ).delete()

        for item_key, item_value in exif_data.items():
            set_metadata(
                assigned_to_content_type=self.file.get_content_type(),
                assigned_to_object_id=self.file.pk,
                custom_key=f"EXIF_{item_key.upper()}",
                value=item_value,
                read_only=True,
            )


class MimeTypeParser:
    file: UploadsVersionFile

    def __init__(self, file: UploadsVersionFile):
        self.file = file

    def parse(self, set_metadata: bool = False):
        mime_type = self.get_mime_type()

        if set_metadata:
            self.set_metadata(
                mime_type=mime_type,
            )

        return mime_type

    def get_mime_type(self) -> str:
        return magic.from_file(self.file.uploaded_file.path, mime=True)

    def set_metadata(self, mime_type: str):
        Metadata.objects.filter(
            assigned_to_content_type=self.file.get_content_type(),
            assigned_to_object_id=self.file.pk,
            custom_key="MIME_TYPE",
        ).delete()

        set_metadata(
            assigned_to_content_type=self.file.get_content_type(),
            assigned_to_object_id=self.file.pk,
            custom_key="MIME_TYPE",
            value=mime_type,
            read_only=True,
        )


class FileInformationParser:
    file: UploadsVersionFile
    FILE_SIZE = "FILE_SIZE"

    def __init__(self, file: UploadsVersionFile):
        self.file = file

    def parse(self, set_metadata: bool = False):
        information = dict(
            file_name=self.file.name,
            file_size=self.file.file_size,
            file_relative_path=self.file.relative_path,
        )

        if set_metadata:
            self.set_metadata(
                information=information,
            )

        return information

    def set_metadata(self, information: dict):
        recalculate_storage_sizes = False
        custom_keys_to_delete = [k.upper() for k in list(information.keys())]

        Metadata.objects.filter(
            assigned_to_content_type=self.file.get_content_type(),
            assigned_to_object_id=self.file.pk,
            custom_key__in=custom_keys_to_delete,
        ).delete()

        if self.FILE_SIZE in custom_keys_to_delete:
            recalculate_storage_sizes = True

        for key, value in information.items():
            custom_key = key.upper()

            set_metadata(
                assigned_to_content_type=self.file.get_content_type(),
                assigned_to_object_id=self.file.pk,
                custom_key=custom_key,
                value=value,
                read_only=True,
            )

            if custom_key == self.FILE_SIZE:
                recalculate_storage_sizes = True

        if recalculate_storage_sizes:
            from fdm.uploads.models import UploadsVersion
            from fdm.uploads.tasks import schedule_recompute_folder_storage_size, schedule_recompute_user_storage_size

            if self.file.created_by:
                schedule_recompute_user_storage_size(str(self.file.created_by.pk))

            folder_ids = (
                UploadsVersion.objects.filter(
                    version_file=self.file,
                )
                .values_list("dataset__folder_id", flat=True)
                .distinct()
            )

            for folder_id in folder_ids:
                if folder_id:
                    schedule_recompute_folder_storage_size(str(folder_id))
