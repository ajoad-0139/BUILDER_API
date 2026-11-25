"""URL configuration for the project."""

from django.contrib import admin
from django.urls import path, include, re_path
from sites.exceptions import not_found
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import (SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('sites.urls')),

    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    
    re_path(r"^api/", not_found),
]

handler404 = "sites.exceptions.not_found"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)