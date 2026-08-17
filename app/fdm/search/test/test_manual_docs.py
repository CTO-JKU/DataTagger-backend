from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db


def test_manual_search_docs_match_runtime_field_catalogue():
    from fdm.search.services import SearchFieldCatalog

    repo_root = Path(__file__).resolve().parents[4]
    manual_docs = (repo_root / "docs" / "manual_docs" / "search.md").read_text()

    for field_id in SearchFieldCatalog.field_ids():
        assert f"`{field_id}`" in manual_docs, f"{field_id} is missing from manual search docs"

    for legacy_field in ("status", "metadata_upgrade_status", "version_name", "version_id"):
        assert f"`{legacy_field}`" not in manual_docs


def test_manual_search_docs_explain_versionless_metadata_completeness():
    repo_root = Path(__file__).resolve().parents[4]
    manual_docs = " ".join((repo_root / "docs" / "manual_docs" / "search.md").read_text().split())

    assert "versionless datasets" in manual_docs
    assert "`metadata_is_complete is true`" in manual_docs
    assert "`metadata_is_complete is false`" in manual_docs
    assert "`metadata_is_complete is_not true`" in manual_docs
    assert "`metadata_is_complete is_not false`" in manual_docs
