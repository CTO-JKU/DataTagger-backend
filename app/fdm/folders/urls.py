from fdm.core.utils.routers import get_api_router

from .rest.views import *

__all__ = [
    "router",
    "urlpatterns",
]

router = get_api_router()

router.register(r"folder", FolderViewSet, basename="folder")
router.register(r"folder-permission", FolderPermissionViewSet, basename="folder-permission")
router.register(r"subfolder", SubfolderViewSet, basename="subfolder")

urlpatterns = []
