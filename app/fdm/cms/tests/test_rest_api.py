import pytest
from django.urls import reverse
from rest_framework import status

from fdm.cms.models.models import Content
from fdm.core.helpers import set_request_for_user

text_de_markdown = """
# Test Deutsch

bla bla

1. eins
2. zwei
3. drei
4. vier
5. fünf
""".strip()

wanted_text_de_html = """
<h1>Test Deutsch</h1><br/><p>bla bla</p><br/><ol><br/><li>eins</li><br/><li>zwei</li><br/><li>drei</li><br/><li>vier</li><br/><li>fünf</li><br/></ol>
""".strip()

text_en_markdown = """
# Test English

bla bla bla

1. one
2. two
3. three
4. four
""".strip()

wanted_text_en_html = """
<h1>Test English</h1><br/><p>bla bla bla</p><br/><ol><br/><li>one</li><br/><li>two</li><br/><li>three</li><br/><li>four</li><br/></ol>
""".strip()


@pytest.mark.django_db
class TestCMSAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.content_1 = Content.objects.get(
            slug="terms-of-use",
        )
        self.content_1.published = True
        self.content_1.text_de = text_de_markdown
        self.content_1.text_en = text_en_markdown
        self.content_1.save()

        self.content_2 = Content.objects.get(
            slug="privacy-policy",
        )
        self.content_2.published = True
        self.content_2.text_de = text_de_markdown + "\n### h3"
        self.content_2.text_en = text_en_markdown + "\n### h3"
        self.content_2.save()

        self.content_3 = Content.objects.get(
            slug="accessibility",
        )
        self.content_3.text_de = text_de_markdown
        self.content_3.text_en = text_en_markdown
        self.content_3.save()

    def test_read_published_list(self, client, initial_users):
        url = reverse("cms-slugs")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data["slugs"]) == {"terms-of-use", "privacy-policy"}
        assert len(response.data["slugs"]) == 2

    def test_read_published_content_detail(self, client, initial_users):
        url = reverse(
            "cms-detail",
            kwargs={
                "slug": self.content_1.slug,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["text_de_html"] == wanted_text_de_html
        assert response.data["text_en_html"] == wanted_text_en_html

        url = reverse(
            "cms-detail",
            kwargs={
                "slug": self.content_2.slug,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["text_de_html"] == wanted_text_de_html + "<br/><h3>h3</h3>"
        assert response.data["text_en_html"] == wanted_text_en_html + "<br/><h3>h3</h3>"

    def test_cannot_read_non_published_content_detail(self, client, initial_users):
        url = reverse(
            "cms-detail",
            kwargs={
                "slug": self.content_3.slug,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND
