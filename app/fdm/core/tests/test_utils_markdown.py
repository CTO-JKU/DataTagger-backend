import pytest

from fdm.core.utils.markdown import render_markdown


@pytest.mark.django_db
class TestRenderMarkdown:
    def test_normal_markdown_renders(self):
        """Smoke test: basic markdown produces HTML."""
        result = render_markdown("**bold** and _italic_")
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result

    @pytest.mark.parametrize(
        "malformed",
        [
            # CVE-2025-69534: malformed HTML-like sequences must not crash the parser
            "<a<b>",
            "<p <a>",
            "text < text",
            "</",
            "<\n>",
            "<<",
            "<a href='x' <b>",
        ],
    )
    def test_malformed_html_does_not_raise(self, malformed):
        """
        CVE-2025-69534 regression: attacker-controlled Markdown with malformed
        HTML-like sequences must not cause an unhandled AssertionError (DoS).
        """
        result = render_markdown(malformed)
        assert isinstance(result, str)
