"""fdm URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.10/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('$', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from fdm.core.utils.routers import get_api_router
from fdm.users.rest.views import (
    ExtendedResetPasswordConfirm,
    ExtendedResetPasswordRequestToken,
    ExtendedResetPasswordValidate,
)

__all__ = [
    "router",
    "urlpatterns",
]

router = get_api_router()


urlpatterns = [
    # Admin panel
    path("admin/", admin.site.urls),
    # Include apps
    path("", include("fdm.core.urls")),
    path("", include("fdm.dashboard.urls")),
    path("", include("fdm.dbsettings.urls")),
    path("", include("fdm.exports.urls")),
    path("", include("fdm.faq.urls")),
    path("", include("fdm.file_parser.urls")),
    path("", include("fdm.folders.urls")),
    path("", include("fdm.metadata.urls")),
    path("", include("fdm.projects.urls")),
    path("", include("fdm.search.urls")),
    path("", include("fdm.uploads.urls")),
    path("", include("fdm.users.urls")),
    path("", include("fdm.shibboleth.urls")),
    path("", include("fdm.cms.urls")),
    path("", include("fdm.storages.urls")),
    path("", include("fdm.approval_queue.urls")),
    # API
    path("api/v1/", include(router.urls)),
    re_path(
        r"^api/v1/uploads-dataset/(?P<dataset_pk>[\w-]+)/",
        include("fdm.rest_framework_tus.urls", namespace="tus"),
    ),
    path(
        "api/v1/reset-password/",
        ExtendedResetPasswordRequestToken.as_view(),
        name="reset-password-request",
    ),
    path(
        "api/v1/reset-password/confirm/",
        ExtendedResetPasswordConfirm.as_view(),
        name="reset-password-confirm",
    ),
    path(
        "api/v1/reset-password/validate/",
        ExtendedResetPasswordValidate.as_view(),
        name="reset-password-validate",
    ),
    # OpenAPI 3
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Waffle (feature flipper) - adds <url>>/waffle_status route - provides statuses of all switches, flags and samples
    path("", include("waffle.urls")),
]

# serve media root for uploaded files
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# serve debug toolbar (only if installed)
if "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
    ]
