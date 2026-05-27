# RozLedger Handoff

## Current Product

RozLedger is a Django + MySQL product for Indian small businesses. The public frontend is static HTML/CSS/JS served by Django. The backend stores:

- Pro waitlist leads
- Invoice submissions
- Affiliate click events

## Important Paths

- Frontend: `index.html`, `styles.css`, `app.js`
- SEO pages: `pages/`
- SEO/video generator: `build_scripts/generate_content.py`
- Django backend: `django_backend/`
- Docker staging: `docker-compose.yml`
- LAN staging port overlay: `docker-compose.staging.yml`
- VPS behind existing Nginx: `docker-compose.vps-nginx.yml`
- Standalone HTTPS server: `docker-compose.prod.yml` + `Caddyfile`

## Deployment Rules

- Do not commit real `.env` files.
- Use `django_backend/.env.docker.example` as the template.
- On a VPS that already has Nginx on `80/443`, use:

```bash
docker compose -f docker-compose.yml -f docker-compose.vps-nginx.yml up -d --build
```

- On a LAN staging server where `18080` should be reachable from the network, use:

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build
```

- On a clean VPS where RozLedger owns `80/443`, use:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## Public Domain

Primary:

```text
https://rozledger.in
```

Redirect:

```text
http://rozledger.in -> https://rozledger.in
https://www.rozledger.in -> https://rozledger.in
https://rozledger.com -> https://rozledger.in
https://www.rozledger.com -> https://rozledger.in
```

## Before Production Launch

- Add legal pages: Privacy, Terms, Affiliate Disclosure.
- Replace placeholder affiliate links.
- Submit `https://rozledger.in/sitemap.xml` to Google Search Console.
- Change all temporary/admin passwords.
