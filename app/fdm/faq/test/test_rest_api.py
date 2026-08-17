import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.faq.models import *

User = get_user_model()


@pytest.mark.django_db
class TestFAQCategoryAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.faq_category_1 = FAQCategory.objects.create(
            name="Category 1",
            published=True,
            order=1,
        )

        self.faq_category_2 = FAQCategory.objects.create(
            name="Category 2",
            published=True,
            order=2,
        )

        self.faq_category_3 = FAQCategory.objects.create(
            name="Category 3",
            published=False,
            order=3,
        )

        self.faq_1 = FAQ.objects.create(
            category=self.faq_category_2,
            question="Question 1",
            answer="Answer 1",
            published=True,
            order=1,
        )

        self.faq_2 = FAQ.objects.create(
            category=self.faq_category_2,
            question="Question 2",
            answer="Answer 2",
            published=True,
            order=2,
        )

        self.faq_3 = FAQ.objects.create(
            category=self.faq_category_2,
            question="Question 3",
            answer="Answer 3",
            published=False,
            order=3,
        )

    def test_read_faq_category_list(self, client):
        """
        Ensure we can read the FAQ category list.
        """
        url = reverse("faq-category-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert response.data[0]["name"] == "Category 1"
        assert response.data[0]["order"] == 1
        assert response.data[1]["name"] == "Category 2"
        assert response.data[1]["order"] == 2

    def test_read_faq_category_details(self, client):
        """
        Ensure we can read the FAQ category details.
        """
        url = reverse(
            "faq-category-detail",
            kwargs={
                "pk": self.faq_category_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Category 1"
        assert response.data["slug"] == "category-1"
        assert len(response.data["faq"]) == 0

    def test_read_faq_category_details_with_faq(self, client):
        """
        Ensure we can read the FAQ category details with FAQ attached.
        """
        url = reverse(
            "faq-category-detail",
            kwargs={
                "pk": self.faq_category_2.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Category 2"
        assert response.data["slug"] == "category-2"

        assert len(response.data["faq"]) == 2
        assert response.data["faq"][0]["question"] == "Question 1"
        assert response.data["faq"][0]["slug"] == "question-1"
        assert response.data["faq"][0]["answer"] == "Answer 1"
        assert response.data["faq"][1]["question"] == "Question 2"
        assert response.data["faq"][1]["slug"] == "question-2"
        assert response.data["faq"][1]["answer"] == "Answer 2"

    def test_read_unpublished_faq_category_details(self, client):
        """
        Ensure we can't read the details of an unpublished FAQ category.
        """
        url = reverse(
            "faq-category-detail",
            kwargs={
                "pk": self.faq_category_3.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestFAQAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.faq_category_1 = FAQCategory.objects.create(
            name="Category 1",
            published=True,
            order=1,
        )

        self.faq_category_2 = FAQCategory.objects.create(
            name="Category 2",
            published=True,
            order=2,
        )

        self.faq_category_3 = FAQCategory.objects.create(
            name="Category 3",
            published=False,
            order=3,
        )

        self.faq_1 = FAQ.objects.create(
            category=self.faq_category_2,
            question="Question 1",
            answer="Answer 1",
            published=True,
            order=1,
        )

        self.faq_2 = FAQ.objects.create(
            category=self.faq_category_2,
            question="Question 2",
            answer="Answer 2",
            published=True,
            order=2,
        )

        self.faq_3 = FAQ.objects.create(
            category=self.faq_category_2,
            question="Question 3",
            answer="Answer 3",
            published=False,
            order=3,
        )

        self.faq_4 = FAQ.objects.create(
            question="Question 4",
            answer="Answer 4",
            published=True,
            order=4,
        )

    def test_read_faq_list(self, client):
        """
        Ensure we can read the FAQ list.
        """
        url = reverse("faq-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert response.data[0]["question"] == "Question 1"
        assert response.data[0]["order"] == 1
        assert response.data[0]["category"]["pk"] == str(self.faq_category_2.pk)
        assert response.data[1]["question"] == "Question 2"
        assert response.data[1]["order"] == 2
        assert response.data[1]["category"]["pk"] == str(self.faq_category_2.pk)
        assert response.data[2]["question"] == "Question 4"
        assert response.data[2]["order"] == 4
        assert response.data[2]["category"] is None

    def test_read_faq_details(self, client):
        """
        Ensure we can read the FAQ details.
        """
        url = reverse(
            "faq-detail",
            kwargs={
                "pk": self.faq_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["question"] == "Question 1"
        assert response.data["slug"] == "question-1"
        assert response.data["answer"] == "Answer 1"
        assert response.data["category"]["pk"] == str(self.faq_category_2.pk)

    def test_read_faq_category_details_without_category(self, client):
        """
        Ensure we can read the FAQ details with no category attached.
        """
        url = reverse(
            "faq-detail",
            kwargs={
                "pk": self.faq_4.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["question"] == "Question 4"
        assert response.data["slug"] == "question-4"
        assert response.data["answer"] == "Answer 4"
        assert response.data["category"] is None

    def test_read_unpublished_faq_details(self, client):
        """
        Ensure we can't read the details of an unpublished FAQ.
        """
        url = reverse(
            "faq-detail",
            kwargs={
                "pk": self.faq_3.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND
