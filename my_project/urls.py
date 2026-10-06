from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from my_app.views import global_project_homepage
from my_app.views import password_reset_page

urlpatterns = [
    path('', global_project_homepage),
    path('reset-password', password_reset_page),
    path('reset-password/', password_reset_page),
    path('admin/', admin.site.urls),
    # Versioned API for all new integrations.
    path('api/v1/', include('my_app.urls')),
    # Legacy route retained while mobile clients migrate to v1.
    path('api/', include('my_app.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
