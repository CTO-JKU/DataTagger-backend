from fdm.core.utils.routers import get_api_router

from .rest.views import *

__all__ = [
    "router",
    "urlpatterns",
]

router = get_api_router()

router.register(r"metadata-field", MetadataFieldViewSet, basename="metadata-field")

router.register(r"metadata", MetadataViewSet, basename="metadata")

router.register(
    r"metadata-template-container",
    MetadataTemplateContainerViewSet,
    basename="metadata-template-container",
)

router.register(r"metadata-template", MetadataTemplateViewSet, basename="metadata-template")

router.register(r"metadata-template-field", MetadataTemplateFieldViewSet, basename="metadata-template-field")

router.register(r"metadata-tag", MetadataTagViewSet, basename="metadata-tag")

urlpatterns = []
