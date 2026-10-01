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
