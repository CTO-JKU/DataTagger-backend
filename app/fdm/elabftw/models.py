import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from fdm.core.models import BaseModel, ByUserMixin, TimestampMixin

__all__ = [
    "normalize_elabftw_url",
    "get_experiment_url",
    "ElabftwConnection",
    "ExperimentLink",
]


def normalize_elabftw_url(url: str) -> str:
    """
    Accepts the address of an eLabFTW instance as users usually copy it (with trailing slashes, the API path or a page
    like experiments.php) and returns its base URL.
    """
    url = (url or "").strip()

    for suffix in ["/api/v2", "/api/v1", "/experiments.php", "/database.php", "/index.php", "/login.php"]:
        position = url.find(suffix)
        if position > 0:
            url = url[:position]

    return url.rstrip("/")


def get_experiment_url(elabftw_url: str, experiment_id: int) -> str:
    return f"{normalize_elabftw_url(elabftw_url)}/experiments.php?mode=view&id={experiment_id}"


def validate_elabftw_url(value: str):
    if not value.startswith(("https://", "http://")) or len(value.split("://", 1)[1]) == 0:
        raise ValidationError(_("Please enter the address of your eLabFTW, starting with https://"))


class ElabftwConnection(BaseModel, TimestampMixin):
    """
    The eLabFTW instance and personal API key of a user. The API key is never sent back to the frontend.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="elabftw_connection",
        on_delete=models.CASCADE,
    )

    url = models.CharField(
        max_length=500,
        validators=[validate_elabftw_url],
    )

    api_key = models.CharField(
        max_length=255,
    )

    verify_tls = models.BooleanField(
        default=True,
    )

    def __str__(self):
        return f"{self.user} → {self.url}"

    def clean(self):
        self.url = normalize_elabftw_url(self.url)
        self.api_key = (self.api_key or "").strip()


class ExperimentLink(BaseModel, TimestampMixin, ByUserMixin):
    """
    Links a folder, subfolder or file (dataset) to an experiment in eLabFTW.
    """

    class TargetType(models.TextChoices):
        FOLDER = "folder", _("Folder")
        SUBFOLDER = "subfolder", _("Subfolder")
        DATASET = "dataset", _("File")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    target_type = models.CharField(
        max_length=16,
        choices=TargetType.choices,
    )

    target_id = models.UUIDField()

    elabftw_url = models.CharField(
        max_length=500,
    )

    experiment_id = models.PositiveIntegerField()

    title = models.CharField(
        max_length=500,
        blank=True,
    )

    class Meta:
        ordering = [
            "creation_date",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["target_type", "target_id", "elabftw_url", "experiment_id"],
                name="unique_experiment_link_per_target",
                violation_error_message=_("This experiment is already linked here."),
            ),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"{self.get_target_type_display()} {self.target_id} → eLabFTW #{self.experiment_id}"

    @property
    def experiment_url(self) -> str:
        return get_experiment_url(self.elabftw_url, self.experiment_id)
