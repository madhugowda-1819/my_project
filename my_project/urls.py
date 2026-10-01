from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    # Versioned API for all new integrations.
    path('api/v1/', include('my_app.urls')),
    # Legacy route retained while mobile clients migrate to v1.
    path('api/', include('my_app.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
