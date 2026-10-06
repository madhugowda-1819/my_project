# Production operations

Run deployments with `DJANGO_DEBUG=false`, HTTPS enabled, a non-default
`DJANGO_SECRET_KEY`, and a separate `JWT_SIGNING_KEY`. Keep `.env` outside
source control and use the environment template only as a key reference.

Before each migration, take a database-provider snapshot or logical backup.
For PostgreSQL, a typical operator command is:

```text
pg_dump --format=custom --file=sportmate-before-migrate.dump "$DATABASE_URL"
```

Then deploy in this order:

```text
python manage.py check --deploy
python manage.py migrate
python manage.py collectstatic --noinput
```

Verify `GET /api/v1/health/` returns `{"status":"ok"}` after restarting the
application. Restore tests must be performed against a separate environment;
never run database-reset test commands against production.

Enable `DJANGO_SECURE_SSL_REDIRECT=true` and HSTS only after the reverse proxy
correctly forwards HTTPS through `X-Forwarded-Proto`.
