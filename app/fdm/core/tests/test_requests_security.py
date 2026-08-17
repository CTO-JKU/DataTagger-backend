import os
import tempfile
import zipfile

import pytest
from requests.utils import extract_zipped_paths


@pytest.mark.django_db
class TestRequestsExtractZippedPaths:
    def test_extract_zipped_paths_uses_nondeterministic_location(self, tmp_path):
        """
        CVE-2026-25645 regression: extract_zipped_paths must write to a
        non-deterministic temp location. The vulnerable pattern (requests
        < 2.33.0) is:

            extracted = os.path.join(tempfile.gettempdir(), filename)

        This is fully predictable — a local attacker can pre-create a
        malicious file at that path, which then gets loaded instead of the
        legitimate one. Fixed in requests 2.33.0 by extracting to a
        non-deterministic subdirectory.
        """
        member_name = "testcert.pem"
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(member_name, b"test certificate content")

        # Construct a virtual path that looks like a member inside the zip.
        # extract_zipped_paths walks up the path until it finds the archive.
        virtual_path = str(zip_path) + os.sep + member_name

        extracted = extract_zipped_paths(virtual_path)

        predictable = os.path.join(tempfile.gettempdir(), member_name)
        assert extracted != predictable, (
            f"extract_zipped_paths returned the predictable path {extracted!r}. "
            "This is vulnerable to CVE-2026-25645 (temp file pre-creation attack). "
            "Upgrade to requests >= 2.33.0."
        )
