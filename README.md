# RozLedger Product MVP

RozLedger is an India-focused daily small-business utility. It helps freelancers,
tutors, agencies, consultants and local shops create invoices, UPI collection links,
WhatsApp reminders and daily revenue targets.

## Frontend-only mode

Open this file in a browser:

`C:\Projects\RozLedger\index.html`

Or run a local server from this folder:

```powershell
python -m http.server 5174
```

Then open:

`http://127.0.0.1:5174`

## Django + MySQL mode

The recommended backend stores leads, invoice submissions and affiliate clicks in MySQL.

```powershell
cd C:\Projects\RozLedger\django_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Then open:

`http://127.0.0.1:8000`

## Docker staging mode

Use this on your Ubuntu staging server:

```bash
cd /home/user/RozLedger
cp django_backend/.env.docker.example django_backend/.env.docker
nano django_backend/.env.docker
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build
```

Then open:

`http://YOUR_SERVER_IP:18080`

## What is included

- GST invoice text generator
- UPI payment link and QR code generator
- WhatsApp payment reminder generator
- Daily revenue target calculator
- Pro waitlist lead capture
- Backend API for leads and invoices
- Django admin panel
- MySQL-ready data models
- Affiliate offer placeholders
- Product, backend and frontend options
- 30 long-tail SEO template pages
- 20 Hindi and English short-video scripts

## Before publishing

- Replace `#` offer links with real affiliate or sponsor links.
- Add privacy policy, terms, affiliate disclosure and tax/financial disclaimer.
- Connect analytics and Google Search Console.
- Add Razorpay or another payment provider for paid plans.
- Use an approved WhatsApp Business provider for automatic reminders.

## Useful docs

- `BUSINESS_PLAN.md`
- `PRODUCT_OPTIONS.md`
- `DOCKER_DEPLOY.md`
- `PRODUCTION_DEPLOY.md`
- `VPS_AUDIT_AND_DEPLOYMENT_SAFETY.md`
- `VPS_AUDIT_REPORT_185_193_19_146.md`
- `VIDEO_PLAN.md`
- `django_backend\README.md`

## Architecture

RozLedger's maintained backend is Django + MySQL in `django_backend`.
The Docker entrypoint runs migrations, regenerates SEO sitemap files from `ROZLEDGER_PUBLIC_URL`, collects static files and starts Gunicorn.
