# Deploy and operations notes

No secrets belong in this file. Real values live only in the server's `.env`.

## Backend release steps (OCI)

```bash
cd <backend checkout>
git pull
source <venv>/bin/activate
pip install -r requirements.txt
python manage.py migrate            # applies catalog 0018 (site content + delivery slots)
python manage.py collectstatic --noinput
python manage.py check --deploy
sudo systemctl restart gunicorn     # use your actual service name
sudo nginx -t && sudo systemctl reload nginx
```

## Uploaded product photos (`/media/`)

Admins can now upload product photos. Django saves them under `MEDIA_ROOT` (`<backend>/media/products/`)
and only serves them itself when `DEBUG` is on, so nginx must serve `/media/`:

```nginx
server {
    server_name api.pinkbakes.com;
    server_tokens off;                 # hide the nginx version in headers and error pages
    client_max_body_size 6m;           # upload cap is 5 MB (PRODUCT_IMAGE_MAX_BYTES) plus form overhead

    location /media/ {
        alias /path/to/pinkbakes_backend/media/;   # must be readable by the nginx user
        expires 30d;
        add_header Cache-Control "public";
        add_header X-Content-Type-Options nosniff;
    }

    location /static/ {
        alias /path/to/pinkbakes_backend/staticfiles/;
        expires 30d;
    }

    location / {
        proxy_pass http://unix:/run/gunicorn.sock;   # or 127.0.0.1:8000
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`server_tokens off;` can also go once in the `http {}` block of `/etc/nginx/nginx.conf`.
Make sure the gunicorn user can write to `media/products/` (for example `mkdir -p media/products` and give the service user ownership).
Back up `media/` along with the database.

## Gunicorn

A reasonable starting point for a small VM:

```bash
gunicorn pinkbakes_backend.wsgi:application \
  --bind unix:/run/gunicorn.sock \
  --workers 3 --threads 2 \
  --timeout 30 --graceful-timeout 30 \
  --max-requests 1000 --max-requests-jitter 100 \
  --access-logfile - --error-logfile -
```

- Workers: about `2 x CPU cores + 1`, and fewer on a 1 GB VM.
- `--max-requests` recycles workers to limit memory creep.
- Database connections are persistent: `DB_CONN_MAX_AGE` defaults to 600 seconds, and `CONN_HEALTH_CHECKS` is on, so a stale connection is replaced instead of raising errors.
  Keep `workers x threads` below the database's connection limit.

## Frontend (Firebase Hosting)

```bash
cd pinksBakes
npm ci
npm run build        # also fetches https://api.pinkbakes.com/sitemap.xml into dist/sitemap.xml
                     # (falls back to public/sitemap.xml; set SITEMAP_SKIP_FETCH=1 to skip)
firebase deploy --only hosting
```

The new security headers are in `firebase.json`.
After deploying, check a page in the browser console for Content-Security-Policy errors.
If a new third-party script, font or API host is added later, add it to the CSP there.

### www to apex redirect (console step, cannot be done in firebase.json)

1. Open the Firebase console, go to Hosting, and click **Add custom domain**.
2. Enter `www.pinkbakes.com` and tick **Redirect www.pinkbakes.com to an existing website**, choosing `pinkbakes.com`.
3. Add the DNS records Firebase shows at your DNS provider. If `www` already points at Firebase, Firebase only re-verifies.
4. Once the certificate is active, `curl -I https://www.pinkbakes.com` should return `301` with `location: https://pinkbakes.com/`.

## Notification environment variables (Email / SMS / WhatsApp)

All are optional. When a channel is unconfigured it is skipped and logged, and nothing fails.

| Channel | Variables | Notes |
|---|---|---|
| Email | `EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_USE_TLS` (true), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `NOTIFICATION_EMAIL_ENABLED` (true) | If `EMAIL_HOST` is empty, mail goes to the console backend (not delivered). |
| SMS | `SMS_PROVIDER=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | All three Twilio values are required. |
| WhatsApp | `WHATSAPP_API_URL`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | The adapter is still a stub: it logs and skips even when configured. Sending needs a provider implementation (for example, the Meta Cloud API). |

Related variables: `FRONTEND_URL` / `PUBLIC_SITE_URL` (links in messages) and `PUBLIC_CONTACT_PHONE`.

## Products API pagination

`GET /api/catalog/products/` returns a plain JSON array (up to `limit`, max 200), which is what the storefront uses.
Any request with `?page=` (including `page=1`) returns `{count, page, page_size, total_pages, results}`.
Both shapes are intentional and stable, and the frontend's `asListResponse` accepts either.
