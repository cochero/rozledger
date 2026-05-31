# RozLedger Engineering Review

Last updated: 2026-05-31

## Current Architecture

RozLedger is a Django 5 application backed by MySQL in Docker. Public marketing, SEO content, lead capture, account login, customer dashboard, invoice creation, invoice PDF generation, manual Pro activation, encrypted Razorpay configuration and admin operations live in the Django app. Static pages and assets are served through Django/WhiteNoise, with Nginx on the VPS proxying HTTPS traffic to the app container.

## File Structure

- `django_backend/rozledger/`: Django project settings and URL routing.
- `django_backend/core/`: domain models, admin screens, views and regression tests.
- `django_backend/core/migrations/`: schema history for leads, invoices, clients, subscriptions and payment configuration.
- `ops/`: VPS audit, Nginx config and MySQL backup helper.
- `pages/`, `blog/`, `videos/`: SEO landing pages, blog pages and short video scripts.
- Root HTML/CSS/JS files: public product experience and static assets used by the Django app.

## Database Schema Notes

- `Lead`: public Pro/early-access requests, attribution and notification status.
- `Client`: saved customer records. New records are owned by a Django user, with `owner_email` retained for migration compatibility.
- `Invoice`: generated invoices, public token links, status tracking and user ownership.
- `PlanSubscription`: free/requested/active/paused/cancelled Pro status, now tied to a Django user where available.
- `PaymentGatewayConfig`: encrypted Razorpay credentials and enable/disable controls.
- `AffiliateClick`: click tracking for future partner offers.

## API And Web Endpoints

- Public: `/`, `/pricing/`, `/contact/`, `/blog/`, `/pages/<slug>/`, `/blog/<slug>/`.
- Account: `/accounts/register/`, `/accounts/login/`, `/accounts/password-reset/`, `/dashboard/`.
- Invoice: `/api/invoices`, `/invoice/<token>/`, `/invoice/<token>/download.pdf`.
- Leads: `/api/leads`, `/pro/request/`, `/pro/thanks/<token>/`.
- Pro workflow: `/dashboard/billing/pro/`, `/dashboard/billing/request-pro/`.
- Admin: `/admin/`.
- Health: `/api/health`.

## Production Readiness Completed

- Added owner foreign keys for clients, invoices and subscriptions so customer data is not only keyed by email strings.
- Added data migration to backfill ownership from existing user emails.
- Added owner-aware dashboard and invoice access checks.
- Added public POST rate limiting for leads, invoice creation and affiliate click endpoints.
- Added configurable production security flags for SSL redirect, secure cookies, HSTS and frame protection.
- Added local SQLite test mode through `DJANGO_DATABASE_ENGINE=sqlite`.
- Added regression tests for public pages, signup, invoice creation, invoice PDF, ownership isolation, Pro request workflow, lead validation and rate limiting.
- Added a MySQL backup helper at `ops/backup_mysql.sh`.

## Remaining Scale Work

- Move public API rate limiting from local Django cache to Redis or an edge WAF before high traffic.
- Split the large `core/views.py` into focused modules or Django class-based views as the product grows.
- Add Celery plus Redis for scheduled reminders, email retries and future WhatsApp workflows.
- Add Razorpay order creation, payment verification and webhook-based subscription activation after Razorpay approval.
- Add database backup automation through cron/systemd and verify restore drills.
- Add uptime monitoring, error tracking and structured app logs.
- Add indexes after real usage data shows slow queries.
- Move static content management to templates/CMS once non-engineers need to edit it frequently.
