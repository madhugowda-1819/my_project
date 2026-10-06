from django.urls import path
from . import views

urlpatterns = [
    path('', views.api_root_landing),
    path('health/', views.health_check),
    path('search/', views.GlobalSearchView.as_view()),
    path('search/suggestions/', views.SearchSuggestionsView.as_view()),
    # ---------------- REPORTS & PLATFORM MODERATION ----------------
    path('reports/', views.ReportListCreateView.as_view()),
    path('reports/<uuid:public_id>/', views.ReportDetailView.as_view()),
    path('admin/dashboard/', views.AdminDashboardView.as_view()),
    path('admin/users/', views.AdminUsersView.as_view()),
    path('admin/reports/', views.AdminReportsView.as_view()),
    path('admin/moderation/actions/', views.AdminModerationActionsView.as_view()),
    path('admin/statistics/', views.AdminStatisticsView.as_view()),
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
    path('users/me/achievements/', views.UserAchievementsView.as_view()),
    path('users/me/rewards/', views.MyRewardsView.as_view()),
    path('users/me/level/', views.MyLevelView.as_view()),
    path('users/me/progress/', views.MyProgressView.as_view()),
    path('me/', views.MeView.as_view()),  # legacy

    # Legacy alias for venue discovery. Keep `/grounds/` reserved for the
    # dedicated ground endpoint below, which accepts `lat` and `lng`.
    path('venues/nearby/', views.VenueNearbyView.as_view()),

    # ---------------- PLAYERS ----------------
    path('players/', views.PlayerListView.as_view()),        # ✅ list first
    path('players/nearby/', views.NearbyPlayerView.as_view()),
    # Existing deterministic compatibility matcher, retained as a Phase 18 provider.
    path('recommendations/players/', views.PlayerRecommendationView.as_view()),
    path('players/<int:pk>/', views.PlayerDetailView.as_view()),
    path('players/<uuid:public_id>/ratings/', views.PlayerRatingsView.as_view()),
    path('players/<uuid:public_id>/achievements/', views.UserAchievementsView.as_view()),
    path('players/<uuid:public_id>/statistics/', views.PlayerStatisticsView.as_view()),
    path('players/<uuid:public_id>/statistics/<int:sport_id>/', views.PlayerStatisticsView.as_view()),
    path('ratings/<uuid:public_id>/', views.RatingDetailView.as_view()),
    path('leaderboards/', views.LeaderboardView.as_view()),

    # ---------------- SPORTS ----------------
    path('sports/', views.SportListView.as_view()),
    path('sports/<int:pk>/', views.SportDetailView.as_view()),
    path('achievements/', views.AchievementListView.as_view()),
    path('achievements/<uuid:public_id>/', views.AchievementDetailView.as_view()),

    # ---------------- GROUNDS ----------------
    path('grounds/', views.GroundListView.as_view()),
    path('grounds/live/', views.LiveGroundListView.as_view()),

    # ---------------- MATCHES ----------------
    path('matches/', views.MatchListView.as_view()),
    path('matches/create/', views.CreateMatchView.as_view()),
    path('matches/<int:match_id>/join/', views.JoinMatchView.as_view()),

    # ---------------- COURT-BACKED GAMES ----------------
    path('games/', views.GameListCreateView.as_view()),
    path('games/<uuid:public_id>/', views.GameDetailView.as_view()),
    path('games/<uuid:public_id>/join/', views.GameJoinView.as_view()),
    path('games/<uuid:public_id>/leave/', views.GameLeaveView.as_view()),
    path('games/<uuid:public_id>/players/', views.GamePlayersView.as_view()),
    path('recommendations/', views.RecommendationOverviewView.as_view()),
    path('recommendations/games/', views.GameRecommendationView.as_view()),
    path('recommendations/venues/', views.VenueRecommendationView.as_view()),
    path('recommendations/communities/', views.CommunityRecommendationView.as_view()),
    path('recommendations/events/', views.EventRecommendationView.as_view()),
    path('recommendations/tournaments/', views.TournamentRecommendationView.as_view()),
    path('events/', views.EventListCreateView.as_view()),
    path('events/<uuid:public_id>/', views.EventDetailView.as_view()),
    path('events/<uuid:public_id>/register/', views.EventRegistrationView.as_view()),
    path('events/<uuid:public_id>/leave/', views.EventLeaveView.as_view()),
    path('events/<uuid:public_id>/cancel/', views.EventCancelView.as_view()),
    path('events/<uuid:public_id>/participants/', views.EventParticipantsView.as_view()),
    path('events/<uuid:public_id>/participants/<int:user_id>/remove/', views.EventParticipantRemoveView.as_view()),
    path('tournaments/', views.TournamentListCreateView.as_view()),
    path('tournaments/<uuid:public_id>/', views.TournamentDetailView.as_view()),
    path('tournaments/<uuid:public_id>/cancel/', views.TournamentCancelView.as_view()),
    path('tournaments/<uuid:public_id>/teams/', views.TournamentTeamsView.as_view()),
    path('tournaments/<uuid:public_id>/matches/', views.TournamentMatchesView.as_view()),
    path('tournaments/<uuid:public_id>/generate-bracket/', views.TournamentBracketView.as_view()),
    path('tournaments/<uuid:public_id>/matches/<uuid:match_id>/result/', views.TournamentResultView.as_view()),
    path('tournaments/<uuid:public_id>/standings/', views.TournamentStandingsView.as_view()),

    # ---------------- COMMUNITIES ----------------
    path('communities/', views.CommunityListCreateView.as_view()),
    path('communities/<uuid:public_id>/', views.CommunityDetailView.as_view()),
    path('communities/<uuid:public_id>/join/', views.CommunityJoinLeaveView.as_view(), {'action': 'join'}),
    path('communities/<uuid:public_id>/leave/', views.CommunityJoinLeaveView.as_view(), {'action': 'leave'}),
    path('communities/<uuid:public_id>/members/', views.CommunityMembersView.as_view()),
    path('communities/<uuid:public_id>/join-requests/', views.CommunityRequestsView.as_view()),
    path('communities/<uuid:public_id>/join-requests/<int:user_id>/approve/', views.CommunityRequestsView.as_view(), {'action': 'approve'}),
    path('communities/<uuid:public_id>/join-requests/<int:user_id>/reject/', views.CommunityRequestsView.as_view(), {'action': 'reject'}),
    path('communities/<uuid:public_id>/posts/', views.CommunityPostsView.as_view()),
    path('community-posts/<uuid:public_id>/', views.CommunityPostDetailView.as_view()),
    path('community-posts/<uuid:public_id>/comments/', views.CommunityCommentsView.as_view()),
    path('community-posts/<uuid:public_id>/like/', views.CommunityPostLikeView.as_view()),
    path('community-comments/<uuid:public_id>/', views.CommunityCommentDetailView.as_view()),
    path('communities/<uuid:public_id>/members/<int:user_id>/remove/', views.CommunityModerationView.as_view(), {'action': 'remove'}),
    path('communities/<uuid:public_id>/members/<int:user_id>/ban/', views.CommunityModerationView.as_view(), {'action': 'ban'}),

    # ---------------- CONVERSATIONS ----------------
    path('conversations/', views.ConversationListCreateView.as_view()),
    path('conversations/<uuid:public_id>/', views.ConversationDetailView.as_view()),
    path('conversations/<uuid:public_id>/messages/', views.ConversationMessagesView.as_view()),
    path('conversations/<uuid:public_id>/read/', views.ConversationReadView.as_view()),
    path('messages/<uuid:public_id>/', views.ConversationMessageDetailView.as_view()),

    # ---------------- NOTIFICATIONS ----------------
    path('notifications/read-all/', views.NotificationReadAllView.as_view()),
    path('notifications/unread-count/', views.NotificationUnreadCountView.as_view()),
    path('notifications/preferences/', views.NotificationPreferencesView.as_view()),
    path('notifications/devices/', views.NotificationDevicesView.as_view()),
    path('notifications/devices/<uuid:public_id>/', views.NotificationDeviceDetailView.as_view()),
    path('notifications/<uuid:public_id>/read/', views.NotificationReadView.as_view()),
    path('notifications/<uuid:public_id>/', views.NotificationDetailView.as_view()),
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
