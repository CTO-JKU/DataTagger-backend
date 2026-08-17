from io import BytesIO

import pytest
from PIL import Image


@pytest.mark.django_db
class TestPillowSecurity:
    def test_pillow_minimum_safe_version(self):
        """
        CVE-2026-25990: out-of-bounds write when loading a specially crafted PSD
        image. Fixed in Pillow 12.1.1. Verify the installed version is safe.
        """
        version = tuple(int(x) for x in Image.__version__.split(".")[:3])
        assert version >= (12, 1, 1), (
            f"Pillow {Image.__version__} is vulnerable to CVE-2026-25990 "
            "(out-of-bounds write via crafted PSD); upgrade to >= 12.1.1"
        )
