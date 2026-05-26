# RozLedger Product Options

## What the product sells

RozLedger sells convenience to Indian small businesses.

The free website gives daily tools:

- GST invoice calculation
- UPI payment link and QR code
- WhatsApp payment reminder
- Daily revenue target calculator

The paid product can sell:

- Saved customers
- Invoice history
- Branded PDF invoices
- Recurring payment reminders
- Payment tracking
- Export reports
- Done-for-you setup for local businesses

## Recommended launch version

Use the Django/MySQL build:

- Frontend: static HTML, CSS and JavaScript
- Backend: Django and MySQL
- Hosting: Render, Railway, PythonAnywhere, DigitalOcean or a small VPS
- Payment: Razorpay Payment Links first, full subscription later
- WhatsApp: manual copy first, approved WhatsApp Business API later

This is the fastest practical version because you can collect leads and validate demand before building a heavy SaaS.

## Frontend options

### Option 1: Static HTML MVP

Best for launch in 1 to 3 days.

Pros:

- Very fast
- Cheap hosting
- Good for SEO landing pages
- Easy to edit

Cons:

- No logged-in dashboard unless backend is added
- More manual work for advanced app features

Use this now.

### Option 2: React or Next.js app

Best after users start returning.

Pros:

- Better app dashboard
- Easier account flows
- Better for subscriptions and saved data
- Can generate SEO pages with templates

Cons:

- More build time
- More hosting and maintenance complexity

Use this after the first 50 to 100 active users.

### Option 3: WordPress plus embedded tool

Best if you want to publish many SEO articles quickly.

Pros:

- Easy blog publishing
- Many SEO plugins
- Easy affiliate link management

Cons:

- Less clean as a real SaaS app
- Needs plugin security maintenance

Use this only if content publishing is your main strength.

## Backend options

### Option 1: Django plus MySQL

Best for the current MVP and the long-term product.

Pros:

- Django admin panel
- Strong data models
- Good for user accounts and subscriptions
- MySQL works well for production hosting

Cons:

- More setup than a static site
- Needs a MySQL database

This has been added in `django_backend`.

### Option 2: Supabase

Best for a quick hosted SaaS prototype.

Pros:

- Hosted database
- Authentication
- File storage
- Good free tier

Cons:

- Some vendor lock-in
- Still needs frontend integration

Use this when you want login, saved invoices and cloud storage quickly.

### Option 3: Flask plus SQLite

Best only for quick local experiments.

Pros:

- Simple
- Easy to run locally
- Good for prototypes

Cons:

- Not ideal for the final SaaS product
- No built-in admin panel

Keep the old Flask version only as a reference.

## Features to build next

1. Admin page to view leads and invoices.
2. PDF invoice download.
3. Real QR generation without third-party image endpoint.
4. Razorpay payment collection.
5. Login and saved customer list.
6. WhatsApp reminder scheduling.
7. SEO page generator for invoice and payment templates.

## Revenue path

Start with three offers:

- Free tool to attract daily users.
- Pro waitlist at Rs 299/month.
- Done-for-you setup at Rs 999 to Rs 2,999.

Then add affiliate links:

- Business credit cards
- Current accounts
- Accounting software
- Payment gateways
- GST filing services
- Business loans
