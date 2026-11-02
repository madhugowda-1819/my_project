"""URLs owned by the SportMate platform-administration portal.

They are included under both ``/api/v1/`` and the legacy ``/api/`` prefix by
the project URL configuration, so existing clients keep their public URLs.
"""
from django.urls import path

from . import views
from . import web_views

web_urlpatterns = [
    path('admin-portal/login/', web_views.login_view, name='login'),
    path('admin-portal/logout/', web_views.logout_view, name='logout'),
    path('admin-portal/dashboard/', web_views.dashboard, name='dashboard'),
    path('admin-portal/users/', web_views.users, name='users'),
    path('admin-portal/venue-owners/', web_views.venue_owners, name='venue_owners'),
    path('admin-portal/venues/', web_views.venues, name='venues'),
    path('admin-portal/venues/<uuid:public_id>/toggle/', web_views.venue_toggle, name='venue_toggle'),
    path('admin-portal/reports/', web_views.reports, name='reports'),
    path('admin-portal/colleges/', web_views.colleges, name='colleges'),
    path('admin-portal/disputes/', web_views.disputes, name='disputes'),
    path('admin-portal/audit/', web_views.audit, name='audit'),
]


urlpatterns = [
    path('reports/', views.ReportListCreateView.as_view()),
    path('reports/<uuid:public_id>/', views.ReportDetailView.as_view()),
    path('admin/dashboard/', views.AdminDashboardView.as_view()),
    path('admin/venues/', views.AdminVenueManagementView.as_view()),
    path('admin/venues/<uuid:public_id>/', views.AdminVenueManagementView.as_view()),
    path('admin/users/', views.AdminUsersView.as_view()),
    path('admin/access/users/', views.AccessManagementView.as_view()),
    path('admin/access/users/<uuid:public_id>/', views.AccessManagementView.as_view()),
    path('admin/venue-owner-applications/', views.VenueOwnerApplicationReviewView.as_view()),
    path('admin/venue-owner-applications/<uuid:public_id>/', views.VenueOwnerApplicationReviewView.as_view()),
    path('admin/colleges/<uuid:public_id>/verification/', views.CollegeVerificationView.as_view()),
    path('admin/college-result-disputes/', views.AdminCollegeResultDisputesView.as_view()),
    path('admin/college-result-disputes/<uuid:public_id>/resolve/', views.CollegeResultDisputeResolveView.as_view()),
    path('admin/reports/', views.AdminReportsView.as_view()),
    path('admin/moderation/actions/', views.AdminModerationActionsView.as_view()),
    path('admin/statistics/', views.AdminStatisticsView.as_view()),
]
