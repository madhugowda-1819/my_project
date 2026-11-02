from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.urls import re_path
from my_app.views import global_project_homepage
from my_app.views import password_reset_page
from my_app_admin_portal.urls import web_urlpatterns

urlpatterns = [
    path('', global_project_homepage),
    path('', include((web_urlpatterns, 'admin_portal'), namespace='admin_portal')),
    path('reset-password', password_reset_page),
    path('reset-password/', password_reset_page),
    path('admin/', admin.site.urls),
    # Versioned API for all new integrations.
    path('api/v1/', include('my_app_admin_portal.urls')),
    path('api/v1/', include('my_app_venue_portal.urls')),
    path('api/v1/', include('my_app.urls')),
    # Legacy route retained while mobile clients migrate to v1.
    path('api/', include('my_app_admin_portal.urls')),
    path('api/', include('my_app_venue_portal.urls')),
    path('api/', include('my_app.urls')),
]

# Serve collected assets through Django as a deployment fallback. Nginx or
# WhiteNoise may serve these in production, but the fallback prevents the
# admin portal from rendering as unstyled HTML when that layer is misconfigured.
urlpatterns += [re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT})]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
