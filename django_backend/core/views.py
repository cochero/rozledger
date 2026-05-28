from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from django.conf import settings
from django.core.mail import send_mail
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import AffiliateClick, Invoice, Lead


def clean_text(value: Any, fallback: str = "", max_length: int = 2000) -> str:
    if value is None:
        return fallback
    return str(value).strip()[:max_length]


def json_payload(request: HttpRequest) -> dict[str, Any]:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def notify_lead(lead: Lead) -> bool:
    if not settings.EMAIL_HOST and "smtp" in settings.EMAIL_BACKEND:
        return False

    owner_subject = f"New RozLedger early-access request: {lead.name}"
    owner_message = "\n".join(
        [
            "A new RozLedger Pro early-access request was submitted.",
            "",
            f"Name: {lead.name}",
            f"Email: {lead.email or 'Not provided'}",
            f"Phone/WhatsApp: {lead.phone}",
            f"Business type: {lead.business_type}",
            f"Source: {lead.source}",
        ]
    )

    customer_sent = True
    if lead.email:
        customer_sent = bool(
            send_mail(
                "We received your RozLedger early-access request",
                "\n".join(
                    [
                        f"Hi {lead.name},",
                        "",
                        "Thanks for requesting RozLedger Pro early access.",
                        "We have received your details and will contact you if your business is a fit for the next testing batch.",
                        "",
                        "For urgent help, WhatsApp us at 9516811111.",
                        "",
                        "RozLedger",
                        "Klickevents Infosolutions Private Limited",
                    ]
                ),
                settings.DEFAULT_FROM_EMAIL,
                [lead.email],
                fail_silently=False,
            )
        )

    owner_sent = bool(
        send_mail(
            owner_subject,
            owner_message,
            settings.DEFAULT_FROM_EMAIL,
            [settings.ROZLEDGER_NOTIFY_EMAIL],
            fail_silently=False,
        )
    )
    return owner_sent and customer_sent


def serve_project_file(filename: str, content_type: str | None = None) -> FileResponse:
    path = Path(settings.PROJECT_ROOT) / filename
    if not path.exists() or not path.is_file():
        raise Http404("File not found")
    return FileResponse(path.open("rb"), content_type=content_type)


def absolute_url(request: HttpRequest, path: str) -> str:
    return request.build_absolute_uri(path)


def whatsapp_url(message: str) -> str:
    return f"https://wa.me/919516811111?text={quote_plus(message)}"


@require_GET
def index(request: HttpRequest) -> FileResponse:
    return serve_project_file("index.html", "text/html")


@require_GET
def asset(request: HttpRequest, filename: str) -> FileResponse:
    content_types = {
        "app.js": "text/javascript",
        "styles.css": "text/css",
        "robots.txt": "text/plain",
        "sitemap.xml": "application/xml",
    }
    return serve_project_file(filename, content_types.get(filename))


@require_GET
def content_index(request: HttpRequest) -> FileResponse:
    return serve_project_file("content.html", "text/html")


@require_GET
def privacy(request: HttpRequest) -> FileResponse:
    return serve_project_file("privacy.html", "text/html")


@require_GET
def terms(request: HttpRequest) -> FileResponse:
    return serve_project_file("terms.html", "text/html")


@require_GET
def contact(request: HttpRequest) -> FileResponse:
    return serve_project_file("contact.html", "text/html")


@require_GET
def seo_page(request: HttpRequest, slug: str) -> FileResponse:
    safe_slug = slug.strip().lower()
    if not safe_slug or "/" in safe_slug or ".." in safe_slug:
        raise Http404("Page not found")
    return serve_project_file(f"pages/{safe_slug}/index.html", "text/html")


@require_GET
def invoice_print(request: HttpRequest, token: str) -> HttpResponse:
    try:
        invoice = Invoice.objects.get(public_token=token)
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Invoice | RozLedger</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="print-page">
    <main class="print-invoice">
      <header class="print-header">
        <div>
          <p class="eyebrow">RozLedger invoice</p>
          <h1>{escape(invoice.business_name)}</h1>
        </div>
        <button class="button primary no-print" type="button" onclick="window.print()">Print / Save PDF</button>
      </header>
      <section class="print-meta">
        <div><span>Client</span><strong>{escape(invoice.client_name)}</strong></div>
        <div><span>Service</span><strong>{escape(invoice.service_name)}</strong></div>
        <div><span>GST rate</span><strong>{escape(str(invoice.gst_rate))}%</strong></div>
        <div><span>Total</span><strong>{escape(invoice.total_text)}</strong></div>
      </section>
      <pre class="template-box">{escape(invoice.invoice_text)}</pre>
      <section class="print-actions no-print">
        <a class="button secondary" href="{escape(invoice.upi_link)}">Open UPI link</a>
        <a class="button secondary" href="{escape(whatsapp_url(invoice.invoice_text))}" rel="noopener">Send on WhatsApp</a>
        <a class="button ghost" href="/">Create another invoice</a>
      </section>
      <p class="print-disclaimer">This is a practical invoice helper. Verify tax and legal details with a qualified professional.</p>
    </main>
  </body>
</html>
"""
    return HttpResponse(html, content_type="text/html")


@require_GET
def lead_thanks(request: HttpRequest, token: str) -> HttpResponse:
    try:
        lead = Lead.objects.get(public_token=token)
    except Lead.DoesNotExist as exc:
        raise Http404("Request not found") from exc

    message = (
        f"Hi RozLedger, I requested Pro early access. "
        f"My name is {lead.name}, business type is {lead.business_type}, phone is {lead.phone}."
    )
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Early Access Request Received | RozLedger</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="content-page">
    <main class="article-shell">
      <article class="article">
        <p class="eyebrow">Request received</p>
        <h1>Thanks, {escape(lead.name)}.</h1>
        <p class="article-lead">Your RozLedger Pro early-access request has been saved. We will contact you when the next testing batch is ready.</p>
        <section>
          <h2>Your details</h2>
          <p>Email: {escape(lead.email)}<br />Phone/WhatsApp: {escape(lead.phone)}<br />Business type: {escape(lead.business_type)}</p>
        </section>
        <div class="article-actions">
          <a class="button primary" href="{escape(whatsapp_url(message))}" rel="noopener">Message us on WhatsApp</a>
          <a class="button secondary" href="/">Back to RozLedger</a>
        </div>
      </article>
    </main>
  </body>
</html>
"""
    return HttpResponse(html, content_type="text/html")


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "ok": True,
            "backend": "django",
            "database": "mysql",
        }
    )


@require_GET
def options(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "recommended": {
                "frontend": "Static HTML/CSS/JS now, Next.js later",
                "backend": "Django with MySQL",
                "admin": "/admin/",
            },
            "frontend": [
                "Static HTML MVP",
                "Next.js SaaS app",
                "WordPress content engine plus embedded tool",
            ],
            "backend": [
                "Django plus MySQL",
                "Django REST Framework when APIs grow",
                "Celery plus Redis for scheduled reminders",
            ],
        }
    )


@csrf_exempt
@require_POST
def create_lead(request: HttpRequest) -> JsonResponse:
    payload = json_payload(request)
    name = clean_text(payload.get("name"), max_length=160)
    email = clean_text(payload.get("email"), max_length=254)
    phone = clean_text(payload.get("phone"), max_length=40)
    business_type = clean_text(payload.get("business_type"), "Unknown", 80)
    source = clean_text(payload.get("source"), "website", 80)

    if len(name) < 2 or len(phone) < 8 or "@" not in email:
        return JsonResponse({"error": "Name, email and phone are required."}, status=400)

    lead = Lead.objects.create(
        name=name,
        email=email,
        phone=phone,
        business_type=business_type,
        source=source,
    )

    notification_sent = False
    try:
        notification_sent = notify_lead(lead)
    except Exception:
        notification_sent = False

    if notification_sent:
        lead.notification_sent = True
        lead.save(update_fields=["notification_sent"])

    thanks_path = f"/pro/thanks/{lead.public_token}/"
    follow_up = (
        f"Hi RozLedger, I requested Pro early access. "
        f"My name is {lead.name}, business type is {lead.business_type}, phone is {lead.phone}."
    )
    return JsonResponse(
        {
            "ok": True,
            "id": lead.id,
            "notification_sent": notification_sent,
            "thanks_url": absolute_url(request, thanks_path),
            "whatsapp_url": whatsapp_url(follow_up),
        },
        status=201,
    )


@csrf_exempt
@require_POST
def create_invoice(request: HttpRequest) -> JsonResponse:
    payload = json_payload(request)
    amount_before_gst = decimal_value(payload.get("amount_before_gst"))
    gst_rate = decimal_value(payload.get("gst_rate"))

    if amount_before_gst <= 0:
        return JsonResponse({"error": "Invoice amount must be greater than zero."}, status=400)

    invoice = Invoice.objects.create(
        business_name=clean_text(payload.get("business_name"), "Your business", 180),
        client_name=clean_text(payload.get("client_name"), "Client", 180),
        service_name=clean_text(payload.get("service_name"), "Service", 240),
        amount_before_gst=amount_before_gst,
        gst_rate=gst_rate,
        due_days=max(int(payload.get("due_days") or 0), 0),
        total_text=clean_text(payload.get("total_text"), max_length=80),
        upi_link=clean_text(payload.get("upi_link")),
        invoice_text=clean_text(payload.get("invoice_text")),
    )

    print_path = f"/invoice/{invoice.public_token}/"
    return JsonResponse(
        {
            "ok": True,
            "id": invoice.id,
            "print_url": absolute_url(request, print_path),
            "whatsapp_url": whatsapp_url(invoice.invoice_text),
        },
        status=201,
    )


@csrf_exempt
@require_POST
def affiliate_click(request: HttpRequest) -> JsonResponse:
    payload = json_payload(request)
    click = AffiliateClick.objects.create(
        offer_name=clean_text(payload.get("offer_name"), "unknown", 160),
        destination_url=clean_text(payload.get("destination_url"), max_length=1000),
    )

    return JsonResponse({"ok": True, "id": click.id}, status=201)
