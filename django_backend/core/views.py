from __future__ import annotations

import json
import re
from datetime import timedelta
from io import BytesIO
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import AffiliateClick, Client, Invoice, Lead, PaymentGatewayConfig, PlanSubscription


GET_OR_HEAD = ["GET", "HEAD"]
LEAD_ALLOWED_BUSINESS_TYPES = {"Freelancer", "Tutor or coaching", "Agency", "Shop or local service", "Consultant"}
HONEYPOT_FIELDS = ("website", "url", "homepage", "company_website")
SPAM_TEXT_RE = re.compile(
    r"(https?://|www\.|<a\s|</a>|casino|crypto|viagra|loan|backlink|telegram|escort|porn|forex|betting)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
RUPEE_SYMBOL = "₹"


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


def current_account_email(request: HttpRequest) -> str:
    return (request.user.email or request.user.username).lower()


def account_q(request: HttpRequest) -> Q:
    email = current_account_email(request)
    return Q(owner=request.user) | Q(owner_email__iexact=email)


def get_subscription(request: HttpRequest) -> PlanSubscription:
    email = current_account_email(request)
    subscription = PlanSubscription.objects.filter(Q(owner=request.user) | Q(owner_email__iexact=email)).first()
    if subscription is None:
        subscription = PlanSubscription.objects.create(owner=request.user, owner_email=email)
    elif subscription.owner_id is None:
        subscription.owner = request.user
        subscription.save(update_fields=["owner", "updated_at"])
    return subscription


def is_valid_email(value: str) -> bool:
    return "@" in value and "." in value.rsplit("@", 1)[-1]


def client_ip(request: HttpRequest) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def is_rate_limited(request: HttpRequest, scope: str, limit: int, window_seconds: int = 300, identity: str | None = None) -> bool:
    key_identity = identity or client_ip(request)
    key = f"rl:{scope}:{key_identity}"
    added = cache.add(key, 1, timeout=window_seconds)
    if added:
        return False
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
        return False
    return count > limit


def rate_limit_response() -> JsonResponse:
    return JsonResponse({"error": "Too many requests. Please try again in a few minutes."}, status=429)


def same_origin_request(request: HttpRequest) -> bool:
    host = request.get_host().split(":", 1)[0].lower()
    for header in ("HTTP_ORIGIN", "HTTP_REFERER"):
        value = request.META.get(header, "")
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.hostname and parsed.hostname.lower() == host:
            return True
    return False


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def invoice_total_text(amount_before_gst: Decimal, gst_rate: Decimal, include_gst: bool = True) -> str:
    total = amount_before_gst + (amount_before_gst * gst_rate / Decimal("100")) if include_gst else amount_before_gst
    return f"{RUPEE_SYMBOL} {total.quantize(Decimal('0.01'))}"


def build_invoice_text(invoice: Invoice) -> str:
    return "\n".join(
        [
            f"Invoice from {invoice.business_name}",
            invoice.business_address,
            f"Client: {invoice.client_name}",
            invoice.client_address,
            f"Client GSTIN: {invoice.client_gstin}" if invoice.client_gstin else "",
            f"Service: {invoice.service_name}",
            f"Amount: {RUPEE_SYMBOL} {invoice.amount_before_gst}",
            f"GST: {invoice.gst_rate}%" if invoice.include_gst else "GST: Not charged",
            f"Total: {invoice.total_text}",
            f"UPI/payment link: {invoice.upi_link}" if invoice.upi_link else "",
            f"Bank details: {invoice.bank_details}" if invoice.bank_details else "",
            invoice.thank_you_note or "Thank you for your business.",
        ]
    ).strip()


def invoice_logo_html(invoice: Invoice) -> str:
    if not invoice.business_logo:
        return ""
    return f'<img class="invoice-logo" src="/invoice/{escape(invoice.public_token)}/logo/" alt="{escape(invoice.business_name)} logo" />'


def valid_logo_upload(upload) -> str:
    if not upload:
        return ""
    allowed_types = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    if getattr(upload, "content_type", "") not in allowed_types:
        return "Upload a PNG, JPG, WebP or GIF logo."
    if getattr(upload, "size", 0) > 2 * 1024 * 1024:
        return "Logo file must be 2 MB or smaller."
    return ""


def invoice_number(invoice: Invoice) -> str:
    return f"RL-{invoice.created_at:%Y%m}-{invoice.id:05d}"


def invoice_due_date(invoice: Invoice):
    return invoice.created_at + timedelta(days=invoice.due_days)


def invoice_gst_amount(invoice: Invoice) -> Decimal:
    if not invoice.include_gst:
        return Decimal("0")
    return (invoice.amount_before_gst * invoice.gst_rate / Decimal("100")).quantize(Decimal("0.01"))


def money(value: Decimal) -> str:
    return f"{RUPEE_SYMBOL} {value.quantize(Decimal('0.01'))}"


def clean_accent_color(value: Any) -> str:
    color = clean_text(value, "#126b4f", 7)
    return color if HEX_COLOR_RE.match(color) else "#126b4f"


def clean_invoice_template(value: Any) -> str:
    template = clean_text(value, "classic", 20)
    allowed = {choice[0] for choice in Invoice.TEMPLATE_CHOICES}
    return template if template in allowed else "classic"


def invoice_template_options(selected: str) -> str:
    return "".join(
        f'<option value="{escape(value)}" {"selected" if selected == value else ""}>{escape(label)}</option>'
        for value, label in Invoice.TEMPLATE_CHOICES
    )


def csrf_input(request: HttpRequest) -> str:
    return f'<input type="hidden" name="csrfmiddlewaretoken" value="{escape(get_token(request))}" />'


def google_tag() -> str:
    return """<!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-KLPE4CG3TK"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-KLPE4CG3TK');
    </script>"""


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
                        "For urgent help, WhatsApp us at +91 95160 22222.",
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
    return f"https://wa.me/919516022222?text={quote_plus(message)}"


def safe_next_url(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/dashboard/"


def brand_html() -> str:
    return '<a class="brand" href="/" aria-label="RozLedger home"><img class="brand-logo" src="/rozledger-logo.png" alt="RozLedger" /></a>'


def subscription_status_copy(subscription: PlanSubscription) -> tuple[str, str, str]:
    if subscription.is_pro_active:
        expiry_copy = f" Trial expires on {subscription.expires_at:%d %b %Y}." if subscription.expires_at else ""
        return (
            "Pro active",
            f"Your RozLedger Pro access is active. You can use saved clients, invoice history, PDF downloads and payment status tracking.{expiry_copy}",
            "active",
        )
    if subscription.plan == "pro" and subscription.status == "active" and subscription.expires_at and subscription.expires_at <= timezone.now():
        return (
            "Pro expired",
            "Your Pro trial has expired. Contact RozLedger support if you want to continue Pro access.",
            "cancelled",
        )
    if subscription.status == "requested":
        return (
            "Pro requested",
            "Your Pro activation request is waiting for admin approval. We will contact you before any paid activation or payment collection.",
            "requested",
        )
    if subscription.status == "paused":
        return (
            "Pro paused",
            "Your Pro access is paused. Contact support if you want to resume the plan.",
            "paused",
        )
    if subscription.status == "cancelled":
        return (
            "Plan cancelled",
            "Your previous Pro request or plan is cancelled. You can request activation again when needed.",
            "cancelled",
        )
    return (
        "Free plan",
        "You are using the free RozLedger tools. Request Pro when you need saved clients, invoice history and payment tracking.",
        "free",
    )


def page_shell(title: str, body: str, request: HttpRequest | None = None) -> HttpResponse:
    user_link = ""
    if request and request.user.is_authenticated:
        user_link = '<a href="/accounts/logout/">Logout</a>'
    else:
        user_link = '<a href="/accounts/login/">Login</a>'
    dashboard_link = '<a href="/dashboard/">Dashboard</a>' if request and request.user.is_authenticated else ""

    html = f"""<!doctype html>
<html lang="en">
  <head>
    {google_tag()}
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)} | RozLedger</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="account-page">
    <header class="topbar">
      {brand_html()}
      <nav aria-label="Primary navigation">
        <a href="/">Tool</a>
        <a href="/content/">Templates</a>
        <a href="/blog/">Blog</a>
        <a href="/pricing/">Pricing</a>
        {dashboard_link}
        <a href="/contact/">Contact</a>
        {user_link}
      </nav>
    </header>
    {body}
    <a class="whatsapp-float" href="https://wa.me/919516022222" aria-label="Chat with RozLedger on WhatsApp" rel="noopener">
      <span class="whatsapp-icon" aria-hidden="true">W</span>
      <span class="whatsapp-text">WhatsApp</span>
    </a>
    <script>
      document.querySelectorAll('[data-password-toggle]').forEach((button) => {{
        button.addEventListener('click', () => {{
          const input = document.getElementById(button.getAttribute('data-password-toggle'));
          if (!input) return;
          const isPassword = input.type === 'password';
          input.type = isPassword ? 'text' : 'password';
          button.textContent = isPassword ? 'Hide' : 'Show';
          button.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
        }});
      }});
    </script>
  </body>
</html>
"""
    return HttpResponse(html, content_type="text/html")


def auth_form(request: HttpRequest, mode: str, error: str = "") -> HttpResponse:
    is_register = mode == "register"
    title = "Create account" if is_register else "Login"
    action = "/accounts/register/" if is_register else "/accounts/login/"
    next_value = escape(safe_next_url(request.GET.get("next") or request.POST.get("next")))
    alternate = (
        f'Already have an account? <a href="/accounts/login/?next={quote_plus(next_value)}">Login</a>'
        if is_register
        else f'New to RozLedger? <a href="/accounts/register/?next={quote_plus(next_value)}">Create an account</a>'
    )
    name_field = (
        """
        <label>
          Your name
          <input name="name" autocomplete="name" placeholder="Business owner name" />
        </label>
        """
        if is_register
        else ""
    )
    password_id = f"{mode}-password"
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell">
      <section class="account-card">
        <p class="eyebrow">RozLedger account</p>
        <h1>{title}</h1>
        <p class="account-copy">Save invoices and Pro requests to a private dashboard using your email address.</p>
        {error_html}
        <form method="post" action="{action}" class="account-form">
          <input type="hidden" name="csrfmiddlewaretoken" value="{escape(get_token(request))}" />
          <input type="hidden" name="next" value="{next_value}" />
          {name_field}
          <label>
            Email
            <input name="email" type="email" autocomplete="email" placeholder="you@example.com" required />
          </label>
          <label>
            Password
            <span class="password-field">
              <input id="{password_id}" name="password" type="password" autocomplete="{'new-password' if is_register else 'current-password'}" required />
              <button class="password-toggle" type="button" data-password-toggle="{password_id}" aria-label="Show password">Show</button>
            </span>
          </label>
          <button class="button primary" type="submit">{title}</button>
        </form>
        <p class="account-alt">{alternate}</p>
        <p class="account-alt"><a href="/accounts/password-reset/">Forgot password?</a></p>
      </section>
    </main>
    """
    return page_shell(title, body, request)


def password_reset_form(request: HttpRequest, message: str = "", error: str = "") -> HttpResponse:
    message_html = f'<p class="form-success">{escape(message)}</p>' if message else ""
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell">
      <section class="account-card">
        <p class="eyebrow">Account recovery</p>
        <h1>Reset password</h1>
        <p class="account-copy">Enter your account email and we will send a secure reset link.</p>
        {message_html}
        {error_html}
        <form method="post" action="/accounts/password-reset/" class="account-form">
          {csrf_input(request)}
          <label>
            Email
            <input name="email" type="email" autocomplete="email" placeholder="you@example.com" required />
          </label>
          <button class="button primary" type="submit">Send reset link</button>
        </form>
        <p class="account-alt"><a href="/accounts/login/">Back to login</a></p>
      </section>
    </main>
    """
    return page_shell("Reset password", body, request)


def password_confirm_form(request: HttpRequest, uidb64: str, token: str, error: str = "") -> HttpResponse:
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell">
      <section class="account-card">
        <p class="eyebrow">Account recovery</p>
        <h1>Choose new password</h1>
        {error_html}
        <form method="post" action="/accounts/reset/{escape(uidb64)}/{escape(token)}/" class="account-form">
          {csrf_input(request)}
          <label>
            New password
            <span class="password-field">
              <input id="reset-password" name="password" type="password" autocomplete="new-password" required />
              <button class="password-toggle" type="button" data-password-toggle="reset-password" aria-label="Show password">Show</button>
            </span>
          </label>
          <button class="button primary" type="submit">Update password</button>
        </form>
      </section>
    </main>
    """
    return page_shell("Choose new password", body, request)


@require_http_methods(GET_OR_HEAD)
def index(request: HttpRequest) -> FileResponse:
    return serve_project_file("index.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def asset(request: HttpRequest, filename: str) -> FileResponse:
    content_types = {
        "app.js": "text/javascript",
        "styles.css": "text/css",
        "rozledger-logo.png": "image/png",
        "robots.txt": "text/plain",
        "sitemap.xml": "application/xml",
    }
    return serve_project_file(filename, content_types.get(filename))


@require_http_methods(GET_OR_HEAD)
def content_index(request: HttpRequest) -> FileResponse:
    return serve_project_file("content.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def blog_index(request: HttpRequest) -> FileResponse:
    return serve_project_file("blog.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def privacy(request: HttpRequest) -> FileResponse:
    return serve_project_file("privacy.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def terms(request: HttpRequest) -> FileResponse:
    return serve_project_file("terms.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def contact(request: HttpRequest) -> FileResponse:
    return serve_project_file("contact.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def pricing(request: HttpRequest) -> FileResponse:
    return serve_project_file("pricing.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def seo_page(request: HttpRequest, slug: str) -> FileResponse:
    safe_slug = slug.strip().lower()
    if not safe_slug or "/" in safe_slug or ".." in safe_slug:
        raise Http404("Page not found")
    return serve_project_file(f"pages/{safe_slug}/index.html", "text/html")


@require_http_methods(GET_OR_HEAD)
def blog_page(request: HttpRequest, slug: str) -> FileResponse:
    safe_slug = slug.strip().lower()
    if not safe_slug or "/" in safe_slug or ".." in safe_slug:
        raise Http404("Page not found")
    return serve_project_file(f"blog/{safe_slug}/index.html", "text/html")


@require_http_methods(["GET", "POST"])
def register_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("/dashboard/")

    if request.method == "GET":
        return auth_form(request, "register")

    name = clean_text(request.POST.get("name"), max_length=150)
    email = clean_text(request.POST.get("email"), max_length=254).lower()
    password = clean_text(request.POST.get("password"), max_length=256)

    if "@" not in email or len(password) < 8:
        return auth_form(request, "register", "Enter a valid email and a password with at least 8 characters.")
    if User.objects.filter(username=email).exists():
        return auth_form(request, "register", "An account already exists for this email. Please login.")

    user = User.objects.create_user(username=email, email=email, password=password, first_name=name)
    login(request, user)
    return redirect(safe_next_url(request.POST.get("next")))


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(safe_next_url(request.GET.get("next") or request.POST.get("next")))

    if request.method == "GET":
        return auth_form(request, "login")

    email = clean_text(request.POST.get("email"), max_length=254).lower()
    password = clean_text(request.POST.get("password"), max_length=256)
    user = authenticate(request, username=email, password=password)
    if user is None:
        return auth_form(request, "login", "Email or password is incorrect.")

    login(request, user)
    return redirect(safe_next_url(request.POST.get("next")))


@require_GET
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("/")


@require_http_methods(["GET", "POST"])
def password_reset_view(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return password_reset_form(request)

    email = clean_text(request.POST.get("email"), max_length=254).lower()
    if not is_valid_email(email):
        return password_reset_form(request, error="Enter a valid account email.")

    user = User.objects.filter(username=email).first()
    if user:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_url = absolute_url(request, f"/accounts/reset/{uid}/{token}/")
        send_mail(
            "Reset your RozLedger password",
            "\n".join(
                [
                    "Use this secure link to reset your RozLedger password:",
                    reset_url,
                    "",
                    "If you did not request this, you can ignore this email.",
                    "",
                    "RozLedger",
                ]
            ),
            settings.DEFAULT_FROM_EMAIL,
            [user.email or user.username],
            fail_silently=False,
        )

    return password_reset_form(request, message="If an account exists for this email, a reset link has been sent.")


@require_http_methods(["GET", "POST"])
def password_reset_confirm_view(request: HttpRequest, uidb64: str, token: str) -> HttpResponse:
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        return password_confirm_form(request, uidb64, token, "This reset link is invalid or expired.")

    if request.method == "GET":
        return password_confirm_form(request, uidb64, token)

    password = clean_text(request.POST.get("password"), max_length=256)
    if len(password) < 8:
        return password_confirm_form(request, uidb64, token, "Use at least 8 characters.")

    user.set_password(password)
    user.save(update_fields=["password"])
    login(request, user)
    return redirect("/dashboard/")


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    email = current_account_email(request)
    invoices = Invoice.objects.filter(account_q(request))[:20]
    leads = Lead.objects.filter(email__iexact=email)[:10]
    clients = Client.objects.filter(account_q(request))[:20]
    subscription = get_subscription(request)
    payment_gateway = PaymentGatewayConfig.active_razorpay()
    subscription_title, subscription_message, subscription_tone = subscription_status_copy(subscription)
    gateway_message = (
        f"Razorpay {payment_gateway.get_mode_display()} mode is enabled. Online checkout can be connected to this approval flow."
        if payment_gateway
        else "Online payment checkout is disabled. Pro activation is currently handled by admin approval."
    )
    notice = ""
    if request.GET.get("pro") == "requested":
        notice = '<p class="dashboard-notice">Your Pro activation request was saved. Admin approval is pending.</p>'
    if request.GET.get("invoice") == "created":
        notice += '<p class="dashboard-notice">Invoice saved. It is now available in your dashboard.</p>'
    paid_count = Invoice.objects.filter(account_q(request), status="paid").count()
    pending_count = Invoice.objects.filter(account_q(request)).exclude(status="paid").count()

    invoice_rows = []
    for invoice in invoices:
        invoice_rows.append(
            f"""
            <article class="dashboard-card">
              <div>
                <span>Invoice</span>
                <h2>{escape(invoice.client_name)}</h2>
                <p>{escape(invoice.service_name)}<br />{escape(invoice.total_text)} - {invoice.created_at:%d %b %Y}</p>
              </div>
              <div class="dashboard-actions">
                <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/" target="_blank" rel="noopener">Open invoice</a>
                <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/download.pdf">PDF</a>
                <a class="button ghost" href="/dashboard/invoices/{invoice.id}/edit/">Edit</a>
                <a class="button ghost" href="{escape(whatsapp_url(invoice.invoice_text))}" target="_blank" rel="noopener">WhatsApp</a>
                <form method="post" action="/dashboard/invoices/{invoice.id}/status/" class="inline-form">
                  {csrf_input(request)}
                  <input type="hidden" name="status" value="paid" />
                  <button class="button ghost" type="submit">Mark paid</button>
                </form>
              </div>
            </article>
            """
        )
    if not invoice_rows:
        invoice_rows.append(
            """
            <article class="dashboard-card empty-state">
              <span>Invoice</span>
              <h2>No saved invoices yet</h2>
              <p>Create your first invoice directly inside the dashboard.</p>
              <a class="button secondary" href="/dashboard/invoices/new/">Create invoice</a>
            </article>
            """
        )

    lead_rows = []
    for lead in leads:
        email_status = "Confirmation email sent" if lead.notification_sent else "Request saved"
        lead_rows.append(
            f"""
            <article class="dashboard-card">
              <div>
                <span>Pro request</span>
                <h2>{escape(lead.business_type)}</h2>
                <p>{escape(email_status)} - {lead.created_at:%d %b %Y}<br />WhatsApp: {escape(lead.phone)}</p>
              </div>
              <div class="dashboard-actions">
                <a class="button secondary" href="/pro/thanks/{escape(lead.public_token)}/" target="_blank" rel="noopener">View confirmation</a>
              </div>
            </article>
            """
        )
    if not lead_rows:
        lead_rows.append(
            """
            <article class="dashboard-card empty-state">
              <span>Pro request</span>
              <h2>No Pro request yet</h2>
              <p>Request early access from the home page if you want us to contact you about Pro.</p>
              <a class="button secondary" href="/#pro">Request Pro</a>
            </article>
            """
        )

    client_rows = []
    for client in clients:
        details = " / ".join(part for part in [client.email, client.phone, client.gstin] if part)
        client_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>Client</span>
                <h2>{escape(client.name)}</h2>
                <p>{escape(details or 'No contact details saved yet')}</p>
              </div>
            </article>
            """
        )
    if not client_rows:
        client_rows.append(
            """
            <article class="dashboard-card empty-state compact-card">
              <span>Client</span>
              <h2>No clients yet</h2>
              <p>Add a client here or save an invoice to build your client list.</p>
            </article>
            """
        )

    display_name = request.user.first_name or email
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero">
        <p class="eyebrow">Private dashboard</p>
        <h1>Welcome, {escape(display_name)}.</h1>
        <p>Track invoices and RozLedger Pro requests connected to {escape(email)}.</p>
        {notice}
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/invoices/new/">Create invoice</a>
          <a class="button secondary" href="/content/">Browse templates</a>
        </div>
      </section>
      <section class="dashboard-summary" aria-label="Account summary">
        <div><span>Pending invoices</span><strong>{pending_count}</strong></div>
        <div><span>Paid invoices</span><strong>{paid_count}</strong></div>
        <div><span>Saved clients</span><strong>{Client.objects.filter(account_q(request)).count()}</strong></div>
        <div><span>Plan</span><strong>{escape(subscription_title)}</strong></div>
      </section>
      <section class="dashboard-section" id="invoices">
        <div class="section-head">
          <p class="eyebrow">Clients</p>
          <h2>Client records</h2>
        </div>
        <form method="post" action="/dashboard/clients/" class="dashboard-form">
          {csrf_input(request)}
          <label>Name<input name="name" placeholder="Client or company name" required /></label>
          <label>Email<input name="email" type="email" placeholder="client@example.com" /></label>
          <label>Phone<input name="phone" placeholder="Mobile or WhatsApp" /></label>
          <label>GSTIN<input name="gstin" placeholder="Optional GSTIN" /></label>
          <button class="button primary" type="submit">Save client</button>
        </form>
        <div class="dashboard-grid">{''.join(client_rows)}</div>
      </section>
      <section class="dashboard-section">
        <div class="section-head">
          <p class="eyebrow">Saved work</p>
          <h2>Invoices</h2>
        </div>
        <div class="dashboard-actions section-actions">
          <a class="button primary" href="/dashboard/invoices/new/">Create invoice</a>
        </div>
        <div class="dashboard-grid">{''.join(invoice_rows)}</div>
      </section>
      <section class="dashboard-section">
        <div class="section-head">
          <p class="eyebrow">Early access</p>
          <h2>Pro requests</h2>
        </div>
        <div class="dashboard-grid">{''.join(lead_rows)}</div>
      </section>
      <section class="dashboard-section">
        <div class="section-head">
          <p class="eyebrow">Billing</p>
          <h2>RozLedger Pro</h2>
        </div>
        <article class="billing-panel">
          <div>
            <span class="plan-badge plan-{escape(subscription_tone)}">{escape(subscription_title)}</span>
            <h3>Current plan: {escape(subscription.get_plan_display())}</h3>
            <p>{escape(subscription_message)} {escape(gateway_message)}</p>
            <p class="billing-meta">
              Requested: {subscription.requested_at.strftime('%d %b %Y') if subscription.requested_at else 'Not requested yet'}
              {f" / Activated: {subscription.activated_at:%d %b %Y}" if subscription.activated_at else ""}
              {f" / Expires: {subscription.expires_at:%d %b %Y}" if subscription.expires_at else ""}
            </p>
          </div>
          <div class="dashboard-actions">
            {f'<form method="post" action="/dashboard/billing/request-pro/" class="inline-form">{csrf_input(request)}<button class="button primary" type="submit">Request Pro activation</button></form>' if subscription.status in ("free", "paused", "cancelled") else ''}
            <a class="button secondary" href="/dashboard/billing/pro/">View Pro workflow</a>
            {'<a class="button ghost" href="https://wa.me/919516022222?text=Hi%20RozLedger%2C%20please%20help%20with%20my%20Pro%20activation." rel="noopener">Contact support</a>' if subscription.status in ('requested', 'paused', 'cancelled') else ''}
          </div>
        </article>
      </section>
    </main>
    """
    return page_shell("Dashboard", body, request)


@login_required
@require_POST
def create_client(request: HttpRequest) -> HttpResponse:
    email = current_account_email(request)
    name = clean_text(request.POST.get("name"), max_length=180)
    if len(name) < 2:
        return redirect("/dashboard/")

    Client.objects.update_or_create(
        owner=request.user,
        name=name,
        defaults={
            "owner_email": email,
            "email": clean_text(request.POST.get("email"), max_length=254),
            "phone": clean_text(request.POST.get("phone"), max_length=40),
            "gstin": clean_text(request.POST.get("gstin"), max_length=20).upper(),
        },
    )
    return redirect("/dashboard/")


@login_required
@require_http_methods(["GET", "POST"])
def invoice_new(request: HttpRequest) -> HttpResponse:
    error = ""
    values = {
        "template": "classic",
        "accent_color": "#126b4f",
        "business_name": request.user.first_name or "Your business",
        "business_address": "",
        "client_name": "",
        "client_address": "",
        "client_gstin": "",
        "service_name": "",
        "include_gst": "on",
        "amount_before_gst": "",
        "gst_rate": "18",
        "due_days": "7",
        "upi_link": "",
        "bank_details": "",
        "thank_you_note": "Thank you for your business.",
    }

    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        values["template"] = clean_invoice_template(request.POST.get("template"))
        values["accent_color"] = clean_accent_color(request.POST.get("accent_color"))
        amount_before_gst = decimal_value(values["amount_before_gst"])
        include_gst = request.POST.get("include_gst") == "on"
        gst_rate = decimal_value(values["gst_rate"]) if include_gst else Decimal("0")
        due_days_raw = digits_only(values["due_days"])
        due_days = int(due_days_raw or 0)
        logo_upload = request.FILES.get("business_logo")
        logo_error = valid_logo_upload(logo_upload)

        if not values["business_name"] or not values["client_name"] or not values["service_name"]:
            error = "Business name, client name and service are required."
        elif amount_before_gst <= 0:
            error = "Invoice amount must be greater than zero."
        elif logo_error:
            error = logo_error
        else:
            invoice = Invoice.objects.create(
                owner=request.user,
                owner_email=current_account_email(request),
                template=values["template"],
                accent_color=values["accent_color"],
                business_name=values["business_name"],
                business_address=values["business_address"],
                client_name=values["client_name"],
                client_address=values["client_address"],
                client_gstin=values["client_gstin"].upper(),
                service_name=values["service_name"],
                include_gst=include_gst,
                amount_before_gst=amount_before_gst,
                gst_rate=gst_rate,
                due_days=due_days,
                total_text=invoice_total_text(amount_before_gst, gst_rate, include_gst),
                upi_link=values["upi_link"],
                bank_details=values["bank_details"],
                thank_you_note=values["thank_you_note"],
                invoice_text="",
            )
            if logo_upload:
                invoice.business_logo = logo_upload
            invoice.invoice_text = build_invoice_text(invoice)
            invoice.save(update_fields=["business_logo", "invoice_text", "updated_at"])
            Client.objects.get_or_create(
                owner_email=invoice.owner_email,
                name=invoice.client_name,
                defaults={"owner": request.user},
            )
            return redirect(f"/dashboard/?invoice=created#invoices")

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card">
        <p class="eyebrow">Invoice</p>
        <h1>Create invoice</h1>
        <p class="account-copy">Use this dashboard form when you want the invoice saved directly to your account.</p>
        {error_html}
        <form method="post" action="/dashboard/invoices/new/" class="account-form invoice-server-form" enctype="multipart/form-data">
          {csrf_input(request)}
          <label>Professional template<select name="template">{invoice_template_options(values['template'])}</select></label>
          <label>Brand/accent color<input name="accent_color" type="color" value="{escape(values['accent_color'])}" /></label>
          <label>Business name<input name="business_name" value="{escape(values['business_name'])}" required /></label>
          <label>Business logo<input name="business_logo" type="file" accept="image/png,image/jpeg,image/webp,image/gif" /></label>
          <label>Business full address<textarea name="business_address" rows="3">{escape(values['business_address'])}</textarea></label>
          <label>Client name<input name="client_name" value="{escape(values['client_name'])}" required /></label>
          <label>Client full address<textarea name="client_address" rows="3">{escape(values['client_address'])}</textarea></label>
          <label>Client GSTIN<input name="client_gstin" value="{escape(values['client_gstin'])}" placeholder="Optional" /></label>
          <label>Service<input name="service_name" value="{escape(values['service_name'])}" required /></label>
          <label class="checkbox-row"><input name="include_gst" type="checkbox" {'checked' if values['include_gst'] == 'on' else ''} /> Include GST on this invoice</label>
          <label>Amount before GST<input name="amount_before_gst" type="number" min="1" step="0.01" value="{escape(values['amount_before_gst'])}" required /></label>
          <label>GST rate %<input name="gst_rate" type="number" min="0" step="0.01" value="{escape(values['gst_rate'])}" required /></label>
          <label>Due days<input name="due_days" type="number" min="0" step="1" value="{escape(values['due_days'])}" /></label>
          <label class="full-row">UPI/payment link<input name="upi_link" value="{escape(values['upi_link'])}" placeholder="Optional UPI link or payment note" /></label>
          <label class="full-row">Bank information<textarea name="bank_details" rows="4" placeholder="Bank name, account number, IFSC, account holder">{escape(values['bank_details'])}</textarea></label>
          <label class="full-row">Thank you note<textarea name="thank_you_note" rows="3">{escape(values['thank_you_note'])}</textarea></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Save invoice</button>
            <a class="button secondary" href="/dashboard/">Back to dashboard</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Create invoice", body, request)


def owned_invoice(request: HttpRequest, invoice_id: int) -> Invoice:
    try:
        return Invoice.objects.get(Q(id=invoice_id) & account_q(request))
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc


@login_required
@require_http_methods(["GET", "POST"])
def invoice_edit(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    if request.method == "POST":
        amount_before_gst = decimal_value(request.POST.get("amount_before_gst"))
        include_gst = request.POST.get("include_gst") == "on"
        gst_rate = decimal_value(request.POST.get("gst_rate")) if include_gst else Decimal("0")
        logo_upload = request.FILES.get("business_logo")
        logo_error = valid_logo_upload(logo_upload)
        if logo_error:
            return page_shell("Edit invoice", f'<main class="account-shell"><section class="account-card"><p class="form-error">{escape(logo_error)}</p><a class="button secondary" href="/dashboard/invoices/{invoice.id}/edit/">Back to edit invoice</a></section></main>', request)
        if amount_before_gst > 0:
            invoice.template = clean_invoice_template(request.POST.get("template"))
            invoice.accent_color = clean_accent_color(request.POST.get("accent_color"))
            invoice.business_name = clean_text(request.POST.get("business_name"), invoice.business_name, 180)
            invoice.business_address = clean_text(request.POST.get("business_address"))
            invoice.client_name = clean_text(request.POST.get("client_name"), invoice.client_name, 180)
            invoice.client_address = clean_text(request.POST.get("client_address"))
            invoice.client_gstin = clean_text(request.POST.get("client_gstin"), max_length=20).upper()
            invoice.service_name = clean_text(request.POST.get("service_name"), invoice.service_name, 240)
            invoice.include_gst = include_gst
            invoice.amount_before_gst = amount_before_gst
            invoice.gst_rate = gst_rate
            status = clean_text(request.POST.get("status"), invoice.status, 20)
            invoice.status = status if status in dict(Invoice.STATUS_CHOICES) else invoice.status
            invoice.total_text = clean_text(request.POST.get("total_text"), invoice.total_text, 80)
            if not invoice.total_text:
                invoice.total_text = invoice_total_text(amount_before_gst, gst_rate, include_gst)
            invoice.upi_link = clean_text(request.POST.get("upi_link"))
            invoice.bank_details = clean_text(request.POST.get("bank_details"))
            invoice.thank_you_note = clean_text(request.POST.get("thank_you_note"))
            if logo_upload:
                invoice.business_logo = logo_upload
            invoice.invoice_text = clean_text(request.POST.get("invoice_text"), invoice.invoice_text)
            if not invoice.invoice_text:
                invoice.invoice_text = build_invoice_text(invoice)
            invoice.save()
            Client.objects.update_or_create(
                owner=request.user,
                name=invoice.client_name,
                defaults={"owner_email": current_account_email(request)},
            )
        return redirect("/dashboard/")

    status_options = "".join(
        f'<option value="{value}" {"selected" if invoice.status == value else ""}>{label}</option>'
        for value, label in Invoice.STATUS_CHOICES
    )
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card">
        <p class="eyebrow">Invoice</p>
        <h1>Edit invoice</h1>
        <form method="post" class="account-form" enctype="multipart/form-data">
          {csrf_input(request)}
          <label>Professional template<select name="template">{invoice_template_options(invoice.template)}</select></label>
          <label>Brand/accent color<input name="accent_color" type="color" value="{escape(invoice.accent_color)}" /></label>
          <label>Business name<input name="business_name" value="{escape(invoice.business_name)}" /></label>
          <label>Business logo<input name="business_logo" type="file" accept="image/png,image/jpeg,image/webp,image/gif" /></label>
          {f'<p class="form-hint">Current logo is attached. Upload a new file to replace it.</p>' if invoice.business_logo else ''}
          <label>Business full address<textarea name="business_address" rows="3">{escape(invoice.business_address)}</textarea></label>
          <label>Client name<input name="client_name" value="{escape(invoice.client_name)}" /></label>
          <label>Client full address<textarea name="client_address" rows="3">{escape(invoice.client_address)}</textarea></label>
          <label>Client GSTIN<input name="client_gstin" value="{escape(invoice.client_gstin)}" /></label>
          <label>Service<input name="service_name" value="{escape(invoice.service_name)}" /></label>
          <label class="checkbox-row"><input name="include_gst" type="checkbox" {'checked' if invoice.include_gst else ''} /> Include GST on this invoice</label>
          <label>Amount before GST<input name="amount_before_gst" type="number" min="1" step="0.01" value="{invoice.amount_before_gst}" /></label>
          <label>GST rate<input name="gst_rate" type="number" min="0" step="0.01" value="{invoice.gst_rate}" /></label>
          <label>Total text<input name="total_text" value="{escape(invoice.total_text)}" /></label>
          <label>Status<select name="status">{status_options}</select></label>
          <label>UPI/payment link<input name="upi_link" value="{escape(invoice.upi_link)}" /></label>
          <label>Bank information<textarea name="bank_details" rows="4">{escape(invoice.bank_details)}</textarea></label>
          <label>Thank you note<textarea name="thank_you_note" rows="3">{escape(invoice.thank_you_note)}</textarea></label>
          <label>Invoice text<textarea name="invoice_text" rows="9">{escape(invoice.invoice_text)}</textarea></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Save changes</button>
            <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/" target="_blank" rel="noopener">Preview</a>
            <a class="button ghost" href="/dashboard/">Cancel</a>
          </div>
        </form>
        <form method="post" action="/dashboard/invoices/{invoice.id}/delete/" class="danger-form">
          {csrf_input(request)}
          <button class="button ghost" type="submit">Delete invoice</button>
        </form>
      </section>
    </main>
    """
    return page_shell("Edit invoice", body, request)


@login_required
@require_POST
def invoice_status(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    status = clean_text(request.POST.get("status"), max_length=20)
    if status in dict(Invoice.STATUS_CHOICES):
        invoice.status = status
        invoice.save(update_fields=["status", "updated_at"])
    return redirect("/dashboard/")


@login_required
@require_POST
def invoice_delete(request: HttpRequest, invoice_id: int) -> HttpResponse:
    owned_invoice(request, invoice_id).delete()
    return redirect("/dashboard/")


@login_required
@require_http_methods(["GET", "POST"])
def pro_billing(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        return request_pro_activation(request)

    email = current_account_email(request)
    subscription = get_subscription(request)
    title, message, tone = subscription_status_copy(subscription)
    payment_gateway = PaymentGatewayConfig.active_razorpay()
    gateway_message = (
        f"Razorpay {payment_gateway.get_mode_display()} mode is configured, but manual admin approval is still kept as a control step."
        if payment_gateway
        else "Razorpay checkout is not enabled yet. This request will be reviewed and approved manually from Django admin."
    )
    request_button = ""
    if subscription.is_pro_active:
        request_button = '<a class="button primary" href="/dashboard/">Go to dashboard</a>'
    else:
        button_label = "Request Pro activation again" if subscription.status in {"paused", "cancelled"} else "Request Pro activation"
        request_button = f"""
          <form method="post" action="/dashboard/billing/pro/" class="account-form compact-form">
            {csrf_input(request)}
            <button class="button primary" type="submit">{escape(button_label)}</button>
          </form>
        """

    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card">
        <p class="eyebrow">RozLedger Pro</p>
        <h1>Pro activation workflow</h1>
        <p class="account-copy">Request Pro from this page. The RozLedger admin can then approve your account from Django admin. Payment checkout can be attached after Razorpay approval.</p>
        <div class="pro-status-panel">
          <span class="plan-badge plan-{escape(tone)}">{escape(title)}</span>
          <h2>{escape(title)}</h2>
          <p>{escape(message)}</p>
          <p>{escape(gateway_message)}</p>
          <p class="billing-meta">
            Account: {escape(email)}<br />
            Requested: {subscription.requested_at.strftime('%d %b %Y') if subscription.requested_at else 'Not requested yet'}
            {f"<br />Activated: {subscription.activated_at:%d %b %Y}" if subscription.activated_at else ""}
            {f"<br />Expires: {subscription.expires_at:%d %b %Y}" if subscription.expires_at else ""}
          </p>
        </div>
        <div class="pro-workflow-grid">
          <article>
            <span>1</span>
            <h2>Customer requests Pro</h2>
            <p>The request is stored against the logged-in email address.</p>
          </article>
          <article>
            <span>2</span>
            <h2>Admin reviews</h2>
            <p>Admin checks the customer and approves Pro from Django admin.</p>
          </article>
          <article>
            <span>3</span>
            <h2>Dashboard updates</h2>
            <p>The customer dashboard changes from requested to active automatically.</p>
          </article>
        </div>
        <div class="dashboard-actions">
          {request_button}
          <a class="button secondary" href="/dashboard/">Back to dashboard</a>
          <a class="button ghost" href="https://wa.me/919516022222?text=Hi%20RozLedger%2C%20I%20need%20help%20with%20Pro%20activation." rel="noopener">WhatsApp support</a>
        </div>
      </section>
    </main>
    """
    return page_shell("Pro activation", body, request)


@login_required
@require_POST
def request_pro_activation(request: HttpRequest) -> HttpResponse:
    email = current_account_email(request)
    payment_gateway = PaymentGatewayConfig.active_razorpay()
    subscription = get_subscription(request)
    subscription.plan = "pro"
    subscription.status = "requested"
    subscription.requested_at = timezone.now()
    subscription.paused_at = None
    subscription.cancelled_at = None
    subscription.save(update_fields=["plan", "status", "requested_at", "paused_at", "cancelled_at", "updated_at"])
    send_mail(
        "RozLedger Pro activation requested",
        "\n".join(
            [
                f"Pro activation requested by {email}.",
                f"Payment gateway: {'Razorpay enabled' if payment_gateway else 'disabled'}",
            ]
        ),
        settings.DEFAULT_FROM_EMAIL,
        [settings.ROZLEDGER_NOTIFY_EMAIL],
        fail_silently=True,
    )
    return redirect("/dashboard/?pro=requested")


@require_http_methods(GET_OR_HEAD)
def invoice_print(request: HttpRequest, token: str) -> HttpResponse:
    try:
        invoice = Invoice.objects.get(public_token=token)
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc

    gst_amount = invoice_gst_amount(invoice)
    due_date = invoice_due_date(invoice)
    template_class = f"invoice-template-{clean_invoice_template(invoice.template)}"
    accent_color = clean_accent_color(invoice.accent_color)
    html = f"""<!doctype html>
<html lang="en">
  <head>
    {google_tag()}
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Invoice | RozLedger</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="print-page">
    <main class="print-invoice {template_class}" style="--invoice-accent: {escape(accent_color)};">
      <header class="invoice-document-header">
        <div class="invoice-brand-block">
          {invoice_logo_html(invoice)}
          <div>
            <p class="invoice-kicker">Tax invoice</p>
            <h1>{escape(invoice.business_name)}</h1>
            <p>{escape(invoice.business_address).replace(chr(10), '<br />')}</p>
          </div>
        </div>
        <aside class="invoice-number-card">
          <strong>{escape(invoice_number(invoice))}</strong>
          <span>Invoice date</span>
          <p>{invoice.created_at:%d %b %Y}</p>
          <span>Due date</span>
          <p>{due_date:%d %b %Y}</p>
          <span>Status</span>
          <p>{escape(invoice.get_status_display())}</p>
        </aside>
      </header>
      <section class="invoice-address-grid">
        <article>
          <span>Seller</span>
          <strong>{escape(invoice.business_name)}</strong>
          <p>{escape(invoice.business_address).replace(chr(10), '<br />')}</p>
        </article>
        <article>
          <span>Bill to</span>
          <strong>{escape(invoice.client_name)}</strong>
          <p>{escape(invoice.client_address).replace(chr(10), '<br />')}</p>
          {f'<p>GSTIN: {escape(invoice.client_gstin)}</p>' if invoice.client_gstin else ''}
        </article>
      </section>
      <table class="invoice-line-table">
        <thead>
          <tr>
            <th>Description</th>
            <th>Amount</th>
            <th>GST</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <strong>{escape(invoice.service_name)}</strong>
              <span>{escape(invoice.client_name)}</span>
            </td>
            <td>{escape(money(invoice.amount_before_gst))}</td>
            <td>{escape(money(gst_amount)) if invoice.include_gst else 'Not charged'}</td>
            <td>{escape(invoice.total_text)}</td>
          </tr>
        </tbody>
      </table>
      <section class="invoice-total-panel">
        <div>
          <span>Subtotal</span>
          <strong>{escape(money(invoice.amount_before_gst))}</strong>
        </div>
        <div>
          <span>{f'GST @ {invoice.gst_rate}%' if invoice.include_gst else 'GST'}</span>
          <strong>{escape(money(gst_amount)) if invoice.include_gst else 'Not charged'}</strong>
        </div>
        <div class="grand-total">
          <span>Amount payable</span>
          <strong>{escape(invoice.total_text)}</strong>
        </div>
      </section>
      <section class="invoice-payment-grid">
        {f'<article><span>Bank information</span><p>{escape(invoice.bank_details).replace(chr(10), "<br />")}</p></article>' if invoice.bank_details else ''}
        {f'<article><span>UPI / payment link</span><p>{escape(invoice.upi_link)}</p></article>' if invoice.upi_link else ''}
      </section>
      {f'<p class="thank-you-note">{escape(invoice.thank_you_note)}</p>' if invoice.thank_you_note else ''}
      <section class="print-actions no-print">
        <button class="button primary" type="button" onclick="window.print()">Print / Save PDF</button>
        {f'<a class="button secondary" href="{escape(invoice.upi_link)}">Open UPI link</a>' if invoice.upi_link else ''}
        <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/download.pdf">Download PDF</a>
        <a class="button secondary" href="{escape(whatsapp_url(invoice.invoice_text))}" rel="noopener">Send on WhatsApp</a>
        <a class="button ghost" href="/dashboard/">Dashboard</a>
      </section>
      <p class="print-disclaimer">Generated by RozLedger. Verify tax and legal details with a qualified professional.</p>
    </main>
  </body>
</html>
"""
    return HttpResponse(html, content_type="text/html")


@require_GET
def invoice_logo(request: HttpRequest, token: str) -> HttpResponse:
    try:
        invoice = Invoice.objects.get(public_token=token)
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc
    if not invoice.business_logo:
        raise Http404("Logo not found")
    response = FileResponse(invoice.business_logo.open("rb"))
    content_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(Path(invoice.business_logo.name).suffix.lower(), "application/octet-stream")
    response["Content-Type"] = content_type
    return response


@require_GET
def invoice_pdf(request: HttpRequest, token: str) -> HttpResponse:
    try:
        invoice = Invoice.objects.get(public_token=token)
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    gst_amount = invoice_gst_amount(invoice)
    due_date = invoice_due_date(invoice)
    accent = colors.HexColor(clean_accent_color(invoice.accent_color))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=34, leftMargin=34, topMargin=34, bottomMargin=34)
    styles = getSampleStyleSheet()
    regular_font = "Helvetica"
    bold_font = "Helvetica-Bold"
    font_candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ]
    for regular_path, bold_path in font_candidates:
        if Path(regular_path).exists() and Path(bold_path).exists():
            pdfmetrics.registerFont(TTFont("RozLedgerSans", regular_path))
            pdfmetrics.registerFont(TTFont("RozLedgerSans-Bold", bold_path))
            regular_font = "RozLedgerSans"
            bold_font = "RozLedgerSans-Bold"
            break

    body_style = ParagraphStyle("InvoiceBody", parent=styles["BodyText"], fontName=regular_font, fontSize=9.5, leading=13)
    small_style = ParagraphStyle("InvoiceSmall", parent=body_style, fontSize=8.5, leading=11, textColor=colors.HexColor("#5b6964"))
    label_style = ParagraphStyle("InvoiceLabel", parent=small_style, fontName=bold_font, textColor=colors.white)
    heading_style = ParagraphStyle("InvoiceHeading", parent=styles["Heading1"], fontName=bold_font, fontSize=18, leading=22, spaceAfter=4)
    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=small_style,
        fontName=bold_font,
        fontSize=9,
        leading=11,
        textColor=accent,
    )
    right_style = ParagraphStyle("InvoiceRight", parent=body_style, alignment=TA_RIGHT)
    right_bold_style = ParagraphStyle("InvoiceRightBold", parent=right_style, fontName=bold_font)
    meta_value_style = ParagraphStyle("InvoiceMetaValue", parent=right_bold_style, fontSize=8.6, leading=11)
    amount_style = ParagraphStyle("InvoiceAmount", parent=right_style, fontSize=8.8, leading=11)
    amount_bold_style = ParagraphStyle("InvoiceAmountBold", parent=right_bold_style, fontSize=8.8, leading=11)

    def para(value: str, style=body_style) -> Paragraph:
        return Paragraph(escape(value or "").replace("\n", "<br/>"), style)

    def total_display() -> str:
        text = (invoice.total_text or "").strip()
        if text.lower().startswith("rs"):
            return f"{RUPEE_SYMBOL} {text[2:].strip()}"
        if text.startswith(RUPEE_SYMBOL):
            return text
        try:
            return money(Decimal(text))
        except (InvalidOperation, ValueError):
            return text

    story = [
        Table([[""]], colWidths=[480], rowHeights=[5], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)])),
        Spacer(1, 18),
    ]
    header_items = [Paragraph("TAX INVOICE", title_style), Paragraph(escape(invoice.business_name), heading_style)]
    if invoice.business_address:
        header_items.append(para(invoice.business_address, small_style))
    meta_table = Table(
        [
            [para("Invoice no.", small_style), para(invoice_number(invoice), meta_value_style)],
            [para("Invoice date", small_style), para(f"{invoice.created_at:%d %b %Y}", meta_value_style)],
            [para("Due date", small_style), para(f"{due_date:%d %b %Y}", meta_value_style)],
            [para("Status", small_style), para(invoice.get_status_display(), meta_value_style)],
        ],
        colWidths=[66, 104],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7f5")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                ("PADDING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        ),
    )
    if invoice.business_logo:
        try:
            ImageReader(invoice.business_logo.path).getRGBData()
            logo = Image(invoice.business_logo.path)
            logo._restrictSize(90, 58)
            header_items.insert(0, logo)
        except Exception:
            pass
    story.append(
        Table(
            [[header_items, meta_table]],
            colWidths=[290, 190],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 0),
                    ("LEFTPADDING", (1, 0), (1, 0), 18),
                ]
            ),
        )
    )
    story.append(Spacer(1, 22))
    story.append(
        Table(
            [
                [Paragraph("Bill to", label_style), Paragraph("Seller", label_style)],
                [
                    para("\n".join(part for part in [invoice.client_name, invoice.client_address, f"GSTIN: {invoice.client_gstin}" if invoice.client_gstin else ""] if part)),
                    para("\n".join(part for part in [invoice.business_name, invoice.business_address] if part)),
                ],
            ],
            colWidths=[240, 240],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("PADDING", (0, 0), (-1, -1), 8),
                ]
            ),
        )
    )
    story.append(Spacer(1, 18))
    story.append(
        Table(
            [
                [Paragraph("Description", label_style), Paragraph("Amount", label_style), Paragraph("GST", label_style), Paragraph("Total", label_style)],
                [
                    para(f"{invoice.service_name}\n{invoice.client_name}"),
                    para(money(invoice.amount_before_gst), amount_style),
                    para(money(gst_amount) if invoice.include_gst else "Not charged", amount_style),
                    para(total_display(), amount_bold_style),
                ],
            ],
            colWidths=[235, 88, 72, 85],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("PADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            ),
        )
    )
    story.append(Spacer(1, 10))
    story.append(
        Table(
            [
                [para("Subtotal", right_style), para(money(invoice.amount_before_gst), amount_bold_style)],
                [para(f"GST @ {invoice.gst_rate}%" if invoice.include_gst else "GST", right_style), para(money(gst_amount) if invoice.include_gst else "Not charged", amount_bold_style)],
                [para("Amount payable", right_bold_style), para(total_display(), amount_bold_style)],
            ],
            colWidths=[330, 150],
            style=TableStyle(
                [
                    ("LINEABOVE", (0, -1), (-1, -1), 1.2, accent),
                    ("PADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            ),
        )
    )
    if invoice.bank_details:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Bank information", heading_style))
        story.append(para(invoice.bank_details))
    if invoice.thank_you_note:
        story.append(Spacer(1, 12))
        story.append(para(invoice.thank_you_note))
    story.append(Spacer(1, 16))
    story.append(Paragraph("Verify tax and legal details with a qualified professional.", styles["Italic"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Invoice generated by RozLedger - www.rozledger.in", small_style))
    doc.build(story)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="rozledger-invoice-{invoice.id}.pdf"'
    return response


@require_http_methods(GET_OR_HEAD)
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
    {google_tag()}
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


def lead_from_payload(payload: dict[str, Any], request: HttpRequest, require_same_origin: bool = True) -> tuple[Lead | None, dict[str, str]]:
    name = clean_text(payload.get("name"), max_length=160)
    email = clean_text(payload.get("email"), max_length=254).lower()
    phone = clean_text(payload.get("phone"), max_length=40)
    phone_digits = digits_only(phone)
    business_type = clean_text(payload.get("business_type"), "Unknown", 80)
    source = clean_text(payload.get("source"), "website", 80)
    landing_path = clean_text(payload.get("landing_path"), max_length=300)
    referrer = clean_text(payload.get("referrer"), max_length=1000)
    utm_source = clean_text(payload.get("utm_source"), max_length=120)
    utm_medium = clean_text(payload.get("utm_medium"), max_length=120)
    utm_campaign = clean_text(payload.get("utm_campaign"), max_length=160)

    errors = {}
    if any(clean_text(payload.get(field), max_length=300) for field in HONEYPOT_FIELDS):
        errors["request"] = "Request could not be saved."
    if require_same_origin and not same_origin_request(request):
        errors["request"] = "Please submit the form from rozledger.in."
    if len(name) < 2:
        errors["name"] = "Name is required."
    if not EMAIL_RE.match(email):
        errors["email"] = "A valid email is required."
    if len(phone_digits) < 10 or len(phone_digits) > 15:
        errors["phone"] = "A valid phone or WhatsApp number is required."
    if business_type not in LEAD_ALLOWED_BUSINESS_TYPES:
        errors["business_type"] = "Choose a valid business type."
    if SPAM_TEXT_RE.search(" ".join([name, phone, business_type, source, utm_source, utm_medium, utm_campaign])):
        errors["request"] = "Request could not be saved."
    if email and Lead.objects.filter(email__iexact=email, created_at__gte=timezone.now() - timedelta(hours=24)).exists():
        errors["email"] = "A request for this email is already saved."
    if phone_digits and Lead.objects.filter(phone_digits=phone_digits, created_at__gte=timezone.now() - timedelta(hours=24)).exists():
        errors["phone"] = "A request for this phone number is already saved."
    if errors:
        return None, errors

    lead = Lead.objects.create(
        name=name,
        email=email,
        phone=phone,
        phone_digits=phone_digits,
        business_type=business_type,
        source=source,
        landing_path=landing_path,
        referrer=referrer,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        ip_address=client_ip(request) if client_ip(request) != "unknown" else None,
        user_agent=clean_text(request.META.get("HTTP_USER_AGENT"), max_length=300),
    )

    try:
        notification_sent = notify_lead(lead)
    except Exception:
        notification_sent = False

    if notification_sent:
        lead.notification_sent = True
        lead.save(update_fields=["notification_sent"])

    return lead, {}


@csrf_exempt
@require_POST
def create_lead(request: HttpRequest) -> JsonResponse:
    if is_rate_limited(request, "lead", limit=4):
        return rate_limit_response()
    payload = json_payload(request)
    email_identity = clean_text(payload.get("email"), max_length=254).lower()
    phone_identity = digits_only(clean_text(payload.get("phone"), max_length=40))
    if email_identity and is_rate_limited(request, "lead_email", limit=2, window_seconds=86400, identity=email_identity):
        return JsonResponse({"error": "Duplicate lead request.", "fields": {"email": "A request for this email is already saved."}}, status=400)
    if phone_identity and is_rate_limited(request, "lead_phone", limit=2, window_seconds=86400, identity=phone_identity):
        return JsonResponse({"error": "Duplicate lead request.", "fields": {"phone": "A request for this phone number is already saved."}}, status=400)
    lead, errors = lead_from_payload(payload, request)
    if lead is None:
        return JsonResponse({"error": "Name, email and phone are required.", "fields": errors}, status=400)

    thanks_path = f"/pro/thanks/{lead.public_token}/"
    follow_up = (
        f"Hi RozLedger, I requested Pro early access. "
        f"My name is {lead.name}, business type is {lead.business_type}, phone is {lead.phone}."
    )
    return JsonResponse(
        {
            "ok": True,
            "id": lead.id,
            "notification_sent": lead.notification_sent,
            "thanks_url": absolute_url(request, thanks_path),
            "whatsapp_url": whatsapp_url(follow_up),
        },
        status=201,
    )


@csrf_exempt
@require_POST
def create_lead_form(request: HttpRequest) -> HttpResponse:
    if is_rate_limited(request, "lead_form", limit=4):
        body = """
        <main class="article-shell">
          <article class="article">
            <p class="eyebrow">Please wait</p>
            <h1>Too many requests.</h1>
            <p class="article-lead">Please try again in a few minutes, or contact us on WhatsApp if this is urgent.</p>
            <div class="article-actions">
              <a class="button primary" href="/#pro">Back to Pro request</a>
              <a class="button secondary" href="https://wa.me/919516022222" rel="noopener">WhatsApp support</a>
            </div>
          </article>
        </main>
        """
        return page_shell("Too many requests", body, request)
    payload = {key: value for key, value in request.POST.items()}
    payload.setdefault("source", "website_form")
    payload.setdefault("landing_path", request.META.get("HTTP_REFERER", ""))
    email_identity = clean_text(payload.get("email"), max_length=254).lower()
    phone_identity = digits_only(clean_text(payload.get("phone"), max_length=40))
    if email_identity and is_rate_limited(request, "lead_email", limit=2, window_seconds=86400, identity=email_identity):
        errors = {"email": "A request for this email is already saved."}
        lead = None
    elif phone_identity and is_rate_limited(request, "lead_phone", limit=2, window_seconds=86400, identity=phone_identity):
        errors = {"phone": "A request for this phone number is already saved."}
        lead = None
    else:
        lead, errors = lead_from_payload(payload, request, require_same_origin=False)
    if lead is None:
        body = f"""
        <main class="article-shell">
          <article class="article">
            <p class="eyebrow">Request not saved</p>
            <h1>Please check your details.</h1>
            <p class="article-lead">{escape(' '.join(errors.values()))}</p>
            <div class="article-actions">
              <a class="button primary" href="/#pro">Back to Pro request</a>
              <a class="button secondary" href="https://wa.me/919516022222" rel="noopener">WhatsApp support</a>
            </div>
          </article>
        </main>
        """
        return page_shell("Request not saved", body, request)
    return redirect(f"/pro/thanks/{lead.public_token}/")


@csrf_exempt
@require_POST
def create_invoice(request: HttpRequest) -> JsonResponse:
    invoice_identity = f"user:{request.user.id}" if request.user.is_authenticated else None
    if is_rate_limited(request, "invoice", limit=60 if request.user.is_authenticated else 20, identity=invoice_identity):
        return rate_limit_response()
    payload = json_payload(request)
    amount_before_gst = decimal_value(payload.get("amount_before_gst"))
    include_gst = bool(payload.get("include_gst", True))
    gst_rate = decimal_value(payload.get("gst_rate")) if include_gst else Decimal("0")
    owner_email = clean_text(payload.get("owner_email"), max_length=254).lower()
    if request.user.is_authenticated and request.user.email:
        owner_email = request.user.email.lower()
    if owner_email and "@" not in owner_email:
        return JsonResponse({"error": "Enter a valid email to save this invoice."}, status=400)

    if amount_before_gst <= 0:
        return JsonResponse({"error": "Invoice amount must be greater than zero."}, status=400)

    invoice = Invoice.objects.create(
        owner=request.user if request.user.is_authenticated else None,
        owner_email=owner_email,
        template=clean_invoice_template(payload.get("template")),
        accent_color=clean_accent_color(payload.get("accent_color")),
        business_name=clean_text(payload.get("business_name"), "Your business", 180),
        business_address=clean_text(payload.get("business_address")),
        client_name=clean_text(payload.get("client_name"), "Client", 180),
        client_address=clean_text(payload.get("client_address")),
        client_gstin=clean_text(payload.get("client_gstin"), max_length=20).upper(),
        service_name=clean_text(payload.get("service_name"), "Service", 240),
        include_gst=include_gst,
        amount_before_gst=amount_before_gst,
        gst_rate=gst_rate,
        due_days=max(int(payload.get("due_days") or 0), 0),
        total_text=clean_text(payload.get("total_text"), invoice_total_text(amount_before_gst, gst_rate, include_gst), 80),
        upi_link=clean_text(payload.get("upi_link")),
        bank_details=clean_text(payload.get("bank_details")),
        thank_you_note=clean_text(payload.get("thank_you_note"), "Thank you for your business."),
        invoice_text=clean_text(payload.get("invoice_text")),
    )
    if not invoice.invoice_text:
        invoice.invoice_text = build_invoice_text(invoice)
        invoice.save(update_fields=["invoice_text", "updated_at"])
    if owner_email and invoice.client_name:
        client_defaults = {"owner": request.user} if request.user.is_authenticated else {}
        Client.objects.get_or_create(owner_email=owner_email, name=invoice.client_name, defaults=client_defaults)

    print_path = f"/invoice/{invoice.public_token}/"
    return JsonResponse(
        {
            "ok": True,
            "id": invoice.id,
            "print_url": absolute_url(request, print_path),
            "whatsapp_url": whatsapp_url(invoice.invoice_text),
            "dashboard_url": absolute_url(request, "/dashboard/"),
        },
        status=201,
    )


@csrf_exempt
@require_POST
def affiliate_click(request: HttpRequest) -> JsonResponse:
    if is_rate_limited(request, "affiliate", limit=30):
        return rate_limit_response()
    payload = json_payload(request)
    click = AffiliateClick.objects.create(
        offer_name=clean_text(payload.get("offer_name"), "unknown", 160),
        destination_url=clean_text(payload.get("destination_url"), max_length=1000),
    )

    return JsonResponse({"ok": True, "id": click.id}, status=201)
