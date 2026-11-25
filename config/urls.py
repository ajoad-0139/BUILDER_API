"""URL configuration for the project."""

from django.contrib import admin
from django.urls import path, include, re_path
from sites.exceptions import not_found
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('sites.urls')),
    re_path(r"^api/", not_found),
]

handler404 = "sites.exceptions.not_found"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)