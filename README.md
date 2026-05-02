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
