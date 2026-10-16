"""URLs owned by the venue-owner portal."""
from django.urls import path

from . import views


urlpatterns = [
    path('auth/register/venue-owner/', views.VenueOwnerRegisterView.as_view()),
    path('users/me/venue-owner-applications/', views.VenueOwnerApplicationView.as_view()),
    path('venue-owner/dashboard/', views.VenueOwnerDashboardView.as_view()),
    path('venue-owner/venues/', views.VenueOwnerVenuesView.as_view()),
    path('venue-owner/venues/<uuid:public_id>/', views.VenueOwnerVenueDetailView.as_view()),
    path('venue-owner/venues/<uuid:public_id>/courts/', views.VenueOwnerCourtsView.as_view()),
    path('venue-owner/courts/<uuid:public_id>/', views.VenueOwnerCourtDetailView.as_view()),
    path('venue-owner/venues/<uuid:venue_id>/courts/<uuid:court_id>/blocked-periods/', views.VenueOwnerBlockedPeriodsView.as_view()),
    path('venue-owner/blocked-periods/<uuid:public_id>/', views.VenueOwnerBlockedPeriodDetailView.as_view()),
    path('venue-owner/bookings/', views.VenueOwnerBookingsView.as_view()),
]
