from django.urls import path
from . import views

urlpatterns = [
    path('', views.api_root_landing),
    # ---------------- AUTH ----------------
    path('auth/register/', views.RegisterView.as_view()),
    path('auth/login/', views.login_view),
    path('auth/logout/', views.LogoutView.as_view()),
    path('auth/token/refresh/', views.AccountTokenRefreshView.as_view()),
    path('auth/refresh/', views.AccountTokenRefreshView.as_view()),  # legacy

    # ---------------- USER ----------------
    path('users/me/', views.MeView.as_view()),
    path('users/me/sports/', views.MySportsView.as_view()),
    path('users/me/player-profile/', views.MyPlayerProfileView.as_view()),
    path('me/', views.MeView.as_view()),  # legacy

    # Legacy aliases now use database-backed nearby search.
    path('players/', views.NearbyPlayerView.as_view()),
    path('grounds/', views.NearbyVenueView.as_view()),
    path('venues/nearby/', views.VenueNearbyView.as_view()),

    # ---------------- PLAYERS ----------------
    path('players/', views.PlayerListView.as_view()),        # ✅ list first
    path('players/nearby/', views.NearbyPlayerView.as_view()),
    path('players/<int:pk>/', views.PlayerDetailView.as_view()),

    # ---------------- SPORTS ----------------
    path('sports/', views.SportListView.as_view()),
    path('sports/<int:pk>/', views.SportDetailView.as_view()),

    # ---------------- GROUNDS ----------------
    path('grounds/', views.GroundListView.as_view()),
    path('venues/nearby/', views.NearbyVenueView.as_view()),
    path('venues/nearby/', views.VenueNearbyView.as_view()),
    path('venues/', views.VenueListView.as_view()),
    path('venues/search/', views.VenueSearchView.as_view()),
    path('venues/<uuid:public_id>/', views.VenueDetailView.as_view()),
    path('venues/<uuid:public_id>/availability/', views.VenueAvailabilityView.as_view()),
    path('venues/reviews/', views.VenueReviewView.as_view()),
    path('venues/reviews/<uuid:public_id>/', views.VenueReviewDetailView.as_view()),
    path('bookings/', views.BookingListCreateView.as_view()),
    path('bookings/quote/', views.BookingQuoteView.as_view()),
    path('bookings/<uuid:public_id>/', views.BookingDetailView.as_view()),
    path('bookings/<uuid:public_id>/cancel/', views.BookingCancelView.as_view()),
    path('recommendations/players/', views.PlayerRecommendationView.as_view()),

    # ---------------- MATCHES ----------------
    path('matches/', views.MatchListView.as_view()),
    path('matches/create/', views.CreateMatchView.as_view()),
    path('matches/<int:match_id>/join/', views.JoinMatchView.as_view()),

    # ---------------- NOTIFICATIONS ----------------
    path('notifications/', views.NotificationListView.as_view()),

    # ---------------- AVAILABILITY ----------------
    path('users/availability/', views.AvailabilityView.as_view()),

    # ---------------- MESSAGES ----------------
    path('messages/', views.MessageCreateView.as_view()),
    path('messages/unread/', views.UnreadMessageCountView.as_view()),
    path('messages/<int:user_id>/', views.MessageThreadView.as_view()),

    #------------------- forgot password -------------------
    path('auth/forgot-password/', views.forgot_password),
    path('auth/reset-password/', views.reset_password),
    path('forgot-password/', views.forgot_password),  # legacy
    path('reset-password/<uidb64>/<token>/', views.reset_password),  # legacy
    path('change-password/', views.change_password),

    path('ai/matches/', views.AiMatchView.as_view()),
]
