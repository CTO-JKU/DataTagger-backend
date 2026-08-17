import json

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Folder
from fdm.metadata.choice_values import canonicalize_choice_field
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.models import (
    Metadata,
    MetadataTag,
    MetadataTemplate,
    MetadataTemplateContainer,
    MetadataTemplateField,
)
from fdm.projects.models import Project

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("field_type", "value", "expected_value"),
    [
        (MetadataFieldType.SELECTION, "  Mixed Case  ", "Mixed Case"),
        (MetadataFieldType.MULTI_SELECTION, ["  First", "Second  "], ["First", "Second"]),
        (MetadataFieldType.TAG, ["  First", "Second  "], ["First", "Second"]),
        (MetadataFieldType.SELECTION, None, None),
        (MetadataFieldType.MULTI_SELECTION, [], []),
    ],
)
def test_canonicalize_choice_field_trims_values_and_preserves_case(field_type, value, expected_value):
    canonical_value, canonical_config = canonicalize_choice_field(
        field_type=field_type,
        value=value,
        config={"options": ["  First", "Second  ", "Mixed Case"]},
    )

    assert canonical_value == expected_value
    assert canonical_config == {"options": ["First", "Second", "Mixed Case"]}


def test_canonicalize_choice_field_keeps_case_variants_distinct():
    value, config = canonicalize_choice_field(
        field_type=MetadataFieldType.MULTI_SELECTION,
        value=[" VALUE ", "value"],
        config={"options": [" VALUE ", "value"]},
    )

    assert value == ["VALUE", "value"]
    assert config["options"] == ["VALUE", "value"]


@pytest.mark.parametrize(
    ("value", "config"),
    [
        ("   ", {"options": ["valid"]}),
        (["valid", "   "], {"options": ["valid"]}),
        ("valid", {"options": ["valid", "   "]}),
    ],
)
def test_canonicalize_choice_field_rejects_empty_canonical_values(value, config):
    with pytest.raises(ValidationError, match="must not be empty"):
        canonicalize_choice_field(
            field_type=(MetadataFieldType.MULTI_SELECTION if isinstance(value, list) else MetadataFieldType.SELECTION),
            value=value,
            config=config,
        )


@pytest.mark.parametrize(
    ("value", "config"),
    [
        ([" first", "first "], {"options": ["first", "second"]}),
        ("first", {"options": [" first", "first "]}),
    ],
)
def test_canonicalize_choice_field_rejects_trim_created_collisions(value, config):
    with pytest.raises(ValidationError, match="same value after trimming"):
        canonicalize_choice_field(
            field_type=(MetadataFieldType.MULTI_SELECTION if isinstance(value, list) else MetadataFieldType.SELECTION),
            value=value,
            config=config,
        )


def test_canonicalize_choice_field_normalizes_options_and_selection_together():
    value, config = canonicalize_choice_field(
        field_type=MetadataFieldType.SELECTION,
        value=" selected ",
        config={"options": [" selected ", "Other"]},
    )

    assert value == "selected"
    assert config == {"options": ["selected", "Other"]}


def test_template_create_api_canonicalizes_options_and_default_together(client):
    response = client.post(
        reverse("metadata-template-container-list"),
        {
            "name": "Canonical choices",
            "metadata_template_fields": [
                {
                    "custom_key": "status",
                    "field_type": MetadataFieldType.SELECTION,
                    "value": "  Mixed Case  ",
                    "config": {"options": ["  Mixed Case  ", "Other"]},
                },
                {
                    "custom_key": "labels",
                    "field_type": MetadataFieldType.MULTI_SELECTION,
                    "value": [" First ", "Second  "],
                    "config": {"options": [" First ", "Second  "]},
                },
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    status_field = MetadataTemplateField.objects.get(custom_key="status")
    labels_field = MetadataTemplateField.objects.get(custom_key="labels")
    assert status_field.get_value() == "Mixed Case"
    assert status_field.config == {"options": ["Mixed Case", "Other"]}
    assert labels_field.get_value() == ["First", "Second"]
    assert labels_field.config == {"options": ["First", "Second"]}


def test_template_create_api_rejects_trim_created_option_collision(client):
    response = client.post(
        reverse("metadata-template-container-list"),
        {
            "name": "Invalid choices",
            "metadata_template_fields": [
                {
                    "custom_key": "status",
                    "field_type": MetadataFieldType.SELECTION,
                    "value": "Choice",
                    "config": {"options": [" Choice", "Choice "]},
                },
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "same value after trimming" in response.data["metadata_template_fields"]


def test_template_version_api_uses_the_canonicalization_contract(client):
    container = MetadataTemplateContainer.objects.create(name="Versioned choices")
    MetadataTemplate.objects.create(metadata_template_container=container, name="v1")

    response = client.post(
        reverse("metadata-template-container-version", kwargs={"pk": container.pk}),
        {
            "name": "v2",
            "metadata_template_fields": [
                {
                    "custom_key": "status",
                    "field_type": MetadataFieldType.SELECTION,
                    "value": "  Open  ",
                    "config": {"options": ["  Open  ", "Closed"]},
                },
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    field = MetadataTemplateField.objects.get(metadata_template=response.data["pk"])
    assert field.get_value() == "Open"
    assert field.config == {"options": ["Open", "Closed"]}


def test_template_import_uses_the_canonicalization_contract(client):
    payload = {
        "name": "Imported choices",
        "metadata_template_fields": [
            {
                "custom_key": "labels",
                "field_type": MetadataFieldType.TAG,
                "value": [" First ", "Second  "],
                "config": {"options": [" First ", "Second  "]},
            },
        ],
    }
    uploaded_file = SimpleUploadedFile(
        "choices.json",
        json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )

    response = client.post(reverse("metadata-template-container-import"), {"file": uploaded_file})

    assert response.status_code == status.HTTP_201_CREATED
    field = MetadataTemplateField.objects.get(metadata_template=response.data["latest_metadata_template"]["pk"])
    assert field.get_value() == ["First", "Second"]
    assert field.config == {"options": ["First", "Second"]}


def test_direct_metadata_write_and_explicit_legacy_resave_use_the_contract():
    metadata = Metadata.objects.create(
        custom_key="status",
        field_type=MetadataFieldType.SELECTION,
        value={"value": " Current "},
        config={"options": [" Current "]},
    )

    assert metadata.get_value() == "Current"
    assert metadata.config == {"options": ["Current"]}

    Metadata.objects.filter(pk=metadata.pk).update(
        value={"value": " Legacy "},
        config={"options": [" Legacy "]},
    )
    metadata.refresh_from_db()
    assert metadata.get_value() == " Legacy "
    assert metadata.config == {"options": [" Legacy "]}

    metadata.save()
    metadata.refresh_from_db()
    assert metadata.get_value() == "Legacy"
    assert metadata.config == {"options": ["Legacy"]}


def test_folder_tag_write_uses_the_same_literal_contract(initial_users):
    set_request_for_user(initial_users["user_1"])
    project = Project.objects.create(name="Project", description={})
    folder = Folder.objects.create(project=project, name="Folder", description={})

    tag = MetadataTag.objects.create(folder=folder, value="  Mixed Case  ")

    assert tag.value == "Mixed Case"

    tag.value = "  Edited Value  "
    tag.save()
    tag.refresh_from_db()
    assert tag.value == "Edited Value"

    with pytest.raises(ValidationError, match="must not be empty"):
        MetadataTag.objects.create(folder=folder, value="   ")
