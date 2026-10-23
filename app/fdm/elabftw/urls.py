from fdm.core.utils.routers import get_api_router

from .rest.views import *

__all__ = [
    "router",
    "urlpatterns",
]

router = get_api_router()

router.register(r"elabftw", ElabftwViewSet, basename="elabftw")
router.register(r"experiment-link", ExperimentLinkViewSet, basename="experiment-link")

urlpatterns = []
