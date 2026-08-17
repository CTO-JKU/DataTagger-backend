from adminsortable2.admin import SortableAdminMixin, SortableStackedInline
from django import forms
from django.contrib import admin

from fdm.core.widgets import EasyMDEWidget
from fdm.faq.models import FAQ, FAQCategory

__all__ = [
    "FAQAdmin",
    "FAQCategoryAdmin",
]


class FAQForm(forms.ModelForm):
    class Meta:
        model = FAQ
        fields = "__all__"
        widgets = {
            "answer": EasyMDEWidget,
        }


@admin.register(FAQ)
class FAQAdmin(SortableAdminMixin, admin.ModelAdmin):
    form = FAQForm
    list_display = [
        "question",
        "category",
        "published",
        "order",
    ]

    list_filter = [
        "category",
        "published",
    ]

    search_fields = [
        "pk",
        "question",
        "slug",
        "answer",
        "category__pk",
        "category__name",
        "category__slug",
    ]

    prepopulated_fields = {
        "slug": [
            "question",
        ],
    }

    class Media:
        css = {
            "screen": [
                "cms/admin/custom.css",
            ],
        }


class FAQStackedInline(SortableStackedInline):
    model = FAQ

    prepopulated_fields = {
        "slug": [
            "question",
        ],
    }


@admin.register(FAQCategory)
class FAQCategoryAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = [
        "name",
        "published",
        "order",
    ]

    list_filter = [
        "published",
    ]

    search_fields = [
        "pk",
        "name",
        "slug",
    ]

    inlines = [
        FAQStackedInline,
    ]

    prepopulated_fields = {
        "slug": [
            "name",
        ],
    }

    class Media:
        css = {
            "screen": [
                "cms/admin/custom.css",
            ],
        }
