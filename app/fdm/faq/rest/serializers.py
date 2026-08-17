import re

from django.utils.safestring import mark_safe
from rest_framework import serializers

from fdm.core.rest.base import BaseModelSerializer
from fdm.core.utils.markdown import render_markdown
from fdm.faq.models import *

__all__ = [
    "FilterPublishedFAQListSerializer",
    "PublishedFAQListSerializer",
    "FAQCategoryMinimalSerializer",
    "FAQMinimalSerializer",
    "FAQCategorySerializer",
    "FAQSerializer",
]


def replace_newline_characters(value):
    # This regex matches \n not preceded by \\
    pattern = r"(?<!\\)\n"
    return re.sub(pattern, "<br/>", value)


class FilterPublishedFAQListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        data = data.exclude(
            published=False,
        ).order_by(
            "order",
        )
        return super().to_representation(data)


class PublishedFAQListSerializer(BaseModelSerializer):
    answer_html = serializers.SerializerMethodField()

    @staticmethod
    def get_answer_html(obj):
        return mark_safe(replace_newline_characters(render_markdown(obj.answer)))

    class Meta:
        model = FAQ
        list_serializer_class = FilterPublishedFAQListSerializer
        fields = [
            "pk",
            "question",
            "slug",
            "answer",
            "answer_html",
            "order",
        ]


class FAQCategoryMinimalSerializer(BaseModelSerializer):
    class Meta:
        model = FAQCategory
        fields = [
            "pk",
            "name",
            "slug",
        ]


class FAQMinimalSerializer(BaseModelSerializer):
    answer_html = serializers.SerializerMethodField()

    @staticmethod
    def get_answer_html(obj):
        return mark_safe(replace_newline_characters(render_markdown(obj.answer)))

    class Meta:
        model = FAQ
        fields = [
            "pk",
            "question",
            "slug",
            "answer",
            "answer_html",
            "order",
        ]


class FAQCategorySerializer(BaseModelSerializer):
    faq = PublishedFAQListSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = FAQCategory
        fields = [
            "pk",
            "name",
            "slug",
            "faq",
            "order",
        ]


class FAQSerializer(BaseModelSerializer):
    answer_html = serializers.SerializerMethodField()

    @staticmethod
    def get_answer_html(obj):
        return mark_safe(replace_newline_characters(render_markdown(obj.answer)))

    category = FAQCategoryMinimalSerializer(
        allow_null=True,
        read_only=True,
    )

    class Meta:
        model = FAQ
        fields = [
            "pk",
            "question",
            "slug",
            "answer",
            "answer_html",
            "category",
            "order",
        ]
