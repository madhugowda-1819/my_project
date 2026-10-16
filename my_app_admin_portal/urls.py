"""URLs owned by the SportMate platform-administration portal.

They are included under both ``/api/v1/`` and the legacy ``/api/`` prefix by
the project URL configuration, so existing clients keep their public URLs.
"""
from django.urls import path

from . import views


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
