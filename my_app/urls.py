from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    # ---------------- AUTH ----------------
    path('auth/register/', views.RegisterView.as_view()),
    path('auth/login/', views.login_view),
    path('auth/logout/', views.LogoutView.as_view()),
    path('auth/refresh/', TokenRefreshView.as_view()),

    # ---------------- USER ----------------
    path('me/', views.MeView.as_view()),

    # ---------------- PLAYERS ----------------
    path('players/', views.PlayerListView.as_view()),        # ✅ list first
    path('players/<int:pk>/', views.PlayerDetailView.as_view()),

    # ---------------- SPORTS ----------------
    path('sports/', views.SportListView.as_view()),

    # ---------------- GROUNDS ----------------
    path('grounds/', views.GroundListView.as_view()),

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
    path('forgot-password/', views.forgot_password),
    path('reset-password/<uidb64>/<token>/', views.reset_password),
    path('change-password/', views.change_password),
]