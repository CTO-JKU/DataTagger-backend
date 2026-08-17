from fdm.core.utils.routers import get_api_router
from fdm.exports.rest.views import *

__all__ = [
    "router",
    "urlpatterns",
]


router = get_api_router()

router.register(r"export", ExportJobViewSet, basename="export")

router.register(r"metadata-template-export", MetadataTemplateExportJobViewSet, basename="metadata-template-export")

urlpatterns = []
