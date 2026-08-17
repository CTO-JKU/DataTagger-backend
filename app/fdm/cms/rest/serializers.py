import re

from django.utils.safestring import mark_safe
from rest_framework import serializers

from fdm.cms.models.models import Content
from fdm.core.utils.markdown import render_markdown

__all__ = [
    "ContentSerializer",
    "ContentPageListSerializer",
]


def replace_newline_characters(value):
    # This regex matches \n not preceded by \\
    pattern = r"(?<!\\)\n"
    return re.sub(pattern, "<br/>", value)


class ContentSerializer(serializers.ModelSerializer):
    text_de_html = serializers.SerializerMethodField()

    @staticmethod
    def get_text_de_html(obj):
        return mark_safe(replace_newline_characters(render_markdown(obj.text_de)))

    text_en_html = serializers.SerializerMethodField()

    @staticmethod
    def get_text_en_html(obj):
        return mark_safe(replace_newline_characters(render_markdown(obj.text_en)))

    class Meta:
        model = Content
        fields = (
            "name",
            "slug",
            "published",
            "text_de_html",
            "text_en_html",
            "creation_date",
            "created_by",
            "last_modification_date",
            "last_modified_by",
        )


class ContentPageListSerializer(serializers.Serializer):
    slugs = serializers.ListField(child=serializers.SlugField())
