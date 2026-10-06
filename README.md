# SportMate – Django Backend

REST API backend for the SportMate Flutter app.

## Quick start

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

python manage.py makemigrations my_app
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

API base URL: `http://localhost:8000/api/`

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/register` | – | Register, returns JWT |
| POST | `/api/auth/login` | – | Login, returns JWT |
| POST | `/api/auth/refresh` | – | Refresh JWT |
| GET  | `/api/me` | ✅ | Current user |
| PATCH| `/api/me` | ✅ | Update profile |
| GET  | `/api/sports` | – | List all sports |
| GET  | `/api/players?sport=&lat=&lng=&radius=` | ✅ | Nearby players |
| GET  | `/api/players/<id>` | ✅ | Player detail |
| POST | `/api/matches/invite` | ✅ | Send match invite |
| GET  | `/api/notifications` | ✅ | User notifications |
| PATCH| `/api/users/availability` | ✅ | Toggle availability + slots |
| GET  | `/api/messages/<user_id>` | ✅ | Chat history |
| POST | `/api/messages` | ✅ | Send message |

Send `Authorization: Bearer <access_token>` on protected routes.

## Player compatibility recommendations

`GET /api/v1/recommendations/players/?sport=<sport_id>&latitude=<lat>&longitude=<lng>&radius=<km>&limit=<n>`

This is a deterministic backend recommendation algorithm, not machine learning.
It first filters in the database by active account, public profile, availability,
the requested sport, radius, and both directions of user blocks. Only the bounded
nearby candidate set is scored.

| Signal | Points | Source |
|---|---:|---|
| Same requested sport | 30 | `UserSport` |
| Skill compatibility | 0–25 | `UserSport.skill_level` difference |
| Distance | 0–20 | SQL Haversine distance |
| Preferred-time overlap | 0–15 | `PlayerProfile.preferred_play_times` |
| Activity | 0 or 10 | completed matches organized by the candidate |

Skill scores are 25 (same), 18 (one level apart), 10 (two), 4 (three), or 0.
Distance thresholds are configurable constants in `my_app/services/player_matching.py`:
0–2 km (20), 2–5 km (17), 5–10 km (12), 10–20 km (6), otherwise 0.
The API returns a privacy-safe player summary, total score, approximate distance,
sport, skill, rating, and matching reasons.

## Court bookings (no payments)

SportMate bookings are reservations only. The backend has no payment model,
gateway, webhooks, payment status, or refund flow.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/bookings/` | Authenticated user's bookings; supports `status`, `date`, `venue`, `court`, `upcoming`, `past` filters. |
| POST | `/api/v1/bookings/` | Creates a confirmed reservation from `venue_id`, `court_id`, `booking_date`, `start_time`, and `end_time`. |
| GET | `/api/v1/bookings/{public_id}/` | Owner-only booking detail. |
| POST | `/api/v1/bookings/{public_id}/cancel/` | Owner/admin cancellation; accepts optional `reason`. |

The server validates active venue/court ownership, future date, venue-local
operating hours, allowed duration, blocked/maintenance periods, and overlap.
It locks the relevant court and rechecks conflicts inside a database transaction.
Prices are server-calculated: `base_price + additional_fees - discount`, never
accepted from a client. Booking status transitions are backend controlled:
`pending → confirmed/cancelled`, `confirmed → cancelled/completed`.

## Court-backed games

The legacy `/matches/` endpoints remain available for existing clients. New
court-backed games use these protected endpoints:

| Method | Endpoint | Description |
|---|---|---|
| GET / POST | `/api/v1/games/` | Discover games or create one. Filters: `sport`, `city`, `venue`, `date`, `skill_level`, `status`, `latitude`, `longitude`, `radius`, `upcoming`. |
| GET / PATCH / DELETE | `/api/v1/games/{public_id}/` | Detail, host-only update, or host/admin cancellation. |
| POST | `/api/v1/games/{public_id}/join/` | Join an eligible open game. |
| POST | `/api/v1/games/{public_id}/leave/` | Leave a game; a host leave cancels it. |
| GET | `/api/v1/games/{public_id}/players/` | Privacy-safe confirmed player list. |

Games use public UUIDs, but sport input remains the existing sport database ID.
Creation and joining are transactional. The service locks the court/game,
validates venue-local hours and configured durations through the booking
service, and rejects any overlap with a pending/confirmed booking or active
game. The host is automatically the first confirmed participant.

`available_slots = max_players - confirmed_players`. Status is backend-owned:
`open`, `almost_full`, and `full` follow capacity (the almost-full threshold is
`GAME_ALMOST_FULL_SLOTS_THRESHOLD`); time then moves a game to `started` and
`completed`. A player needs the sport in their profile and must be within
`GAME_MAX_SKILL_LEVEL_DIFFERENCE` when a skill level is configured. Private
games are not joinable without a future invitation/access record.

## Game recommendations

`GET /api/v1/recommendations/games/` returns deterministic, backend-generated
recommendations for the authenticated user. Optional filters are `sport`,
`latitude`, `longitude`, `radius`, `date`, `skill_level`, and `limit` (1–50,
default 20). If coordinates are omitted, the user's saved location is used; if
no location exists, compatible games are still returned without distance scoring.

The service first filters public, future, active, non-full games in SQL and
excludes games the user hosts or has already joined. It then applies a 100-point
rule-based score: sport (30), skill (20), distance (20), time (15), remaining
capacity (10), and verified host history (5). Results are ordered by score,
distance, start time, then availability. This is not machine learning.

## Conversation chat

New authenticated conversation endpoints are:

| Method | Endpoint | Description |
|---|---|---|
| GET / POST | `/api/v1/conversations/` | List accessible conversations or create/reuse a direct/game conversation. |
| GET | `/api/v1/conversations/{public_id}/` | Conversation summary and unread count. |
| GET / POST | `/api/v1/conversations/{public_id}/messages/` | Paginated chronological messages or a new plain-text message. |
| POST | `/api/v1/conversations/{public_id}/read/` | Set the caller's read cursor. |
| PATCH / DELETE | `/api/v1/messages/{public_id}/` | Sender-only edit or soft delete. |

Direct conversations are unique for a pair of users, and both directions of
`UserBlock` are enforced before creation or sending. Game conversations are
created/reused from confirmed `GamePlayer` records; joining activates chat
membership and leaving deactivates it without deleting historical messages.
The backend, not clients, assigns sender/type/timestamps. Messages are
plain-text, trimmed, capped by `CHAT_MESSAGE_MAX_LENGTH`, and notifications use
the existing notification service.

## Communities

Communities are authenticated, role-controlled groups with public/private
visibility. Core endpoints are `/api/v1/communities/`,
`/api/v1/communities/{public_id}/`, and nested `join`, `leave`, `members`,
`join-requests`, and `posts` routes. Content uses public UUIDs:

- `/api/v1/community-posts/{public_id}/` — retrieve, edit, or soft-delete.
- `/api/v1/community-posts/{public_id}/comments/` — paginated comments/create.
- `/api/v1/community-posts/{public_id}/like/` — idempotent like/unlike.
- `/api/v1/community-comments/{public_id}/` — edit or soft-delete a comment.

The backend derives member and like counts, creates the owner membership
transactionally, routes private joins through pending approval, and enforces
bans. Community discovery reuses the SQL Haversine location service and ranks
communities deterministically by sport relevance, distance, backend activity,
member relevance, and verified membership quality.

## Models (`my_app/models.py`)

- **User** – custom user with email login, location, sports, availability
- **Sport** – sports catalog
- **PlayerProfile** – skill, stats, achievements (1‑to‑1 with User)
- **AvailabilitySlot** – weekly availability
- **MatchInvite** – invitations between players
- **Message** – chat
- **Notification** – in‑app alerts

Distance filtering uses an in‑memory Haversine calculation
(`User.distance_to`). For production scale, switch to PostGIS +
`GeoDjango` with a spatial index.
