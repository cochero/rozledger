from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.http import FileResponse, Http404, HttpRequest, JsonResponse
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

    return JsonResponse({"ok": True, "id": lead.id, "notification_sent": notification_sent}, status=201)


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

    return JsonResponse({"ok": True, "id": invoice.id}, status=201)


@csrf_exempt
@require_POST
def affiliate_click(request: HttpRequest) -> JsonResponse:
    payload = json_payload(request)
    click = AffiliateClick.objects.create(
        offer_name=clean_text(payload.get("offer_name"), "unknown", 160),
        destination_url=clean_text(payload.get("destination_url"), max_length=1000),
    )

    return JsonResponse({"ok": True, "id": click.id}, status=201)
