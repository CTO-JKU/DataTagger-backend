from fdm.core.utils.routers import get_api_router

from .rest.views import *

__all__ = [
    "router",
    "urlpatterns",
]

router = get_api_router()

router.register(r"file-parser-configuration", FileParserConfigurationViewSet, basename="file-parser-configuration")


urlpatterns = []
