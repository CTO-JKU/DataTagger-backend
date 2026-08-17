import pytest

from fdm.core.helpers import format_storage_size, get_metadata_value_file_size


@pytest.mark.parametrize(
    "metadata_value, expected",
    [
        ("not-a-dict", 0),
        ({"value": None}, 0),
        ({"value": "not-a-number"}, 0),
        ({"value": 123}, 123),
    ],
)
@pytest.mark.django_db
def test_get_metadata_value_file_size(metadata_value, expected):
    assert get_metadata_value_file_size(metadata_value) == expected


@pytest.mark.parametrize(
    "storage_size, expected",
    [
        (None, "0 Byte"),
        ("not-a-number", "0 Byte"),
        (-5, "0 Byte"),
        (0, "0 Byte"),
        (12.7, "12 Byte"),
        (1024, "1.00 KB"),
        (1536, "1.50 KB"),
        (1024**2 - 1, "1024.00 KB"),
        (1024**2, "1.00 MB"),
        (1024**3, "1.00 GB"),
        (1024**4, "1.00 TB"),
        (1536 * 1024**2, "1.50 GB"),
    ],
)
@pytest.mark.django_db
def test_format_storage_size(storage_size, expected):
    assert format_storage_size(storage_size) == expected
