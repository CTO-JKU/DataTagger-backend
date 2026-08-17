from django.forms import Textarea

__all__ = ["EasyMDEWidget"]


class EasyMDEWidget(Textarea):
    """Textarea widget that loads a self-hosted EasyMDE markdown editor."""

    class Media:
        css = {
            "all": ("core/vendor/easymde/easymde.min.css",),
        }
        js = (
            "core/vendor/easymde/easymde.min.js",
            "core/js/easymde_init.js",
        )

    def __init__(self, attrs=None):
        default_attrs = {"class": "easymde-editor"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)
