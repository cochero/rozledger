from __future__ import annotations

import json
from io import BytesIO
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import AffiliateClick, Client, Invoice, Lead, PlanSubscription


GET_OR_HEAD = ["GET", "HEAD"]


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


def is_valid_email(value: str) -> bool:
    return "@" in value and "." in value.rsplit("@", 1)[-1]


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


def safe_next_url(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/dashboard/"


def page_shell(title: str, body: str, request: HttpRequest | None = None) -> HttpResponse:
    user_link = ""
    if request and request.user.is_authenticated:
        user_link = '<a href="/accounts/logout/">Logout</a>'
    else:
        user_link = '<a href="/accounts/login/">Login</a>'

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
      <a class="brand" href="/" aria-label="RozLedger home"><span class="brand-mark">R</span><span>RozLedger</span></a>
      <nav aria-label="Primary navigation">
        <a href="/">Tool</a>
        <a href="/content/">Templates</a>
        <a href="/dashboard/">Dashboard</a>
        <a href="/contact/">Contact</a>
        {user_link}
      </nav>
    </header>
    {body}
    <a class="whatsapp-float" href="https://wa.me/919516811111" aria-label="Chat with RozLedger on WhatsApp" rel="noopener">
      <span class="whatsapp-icon" aria-hidden="true">W</span>
      <span class="whatsapp-text">WhatsApp</span>
    </a>
  </body>
</html>
"""
    return HttpResponse(html, content_type="text/html")


def auth_form(request: HttpRequest, mode: str, error: str = "") -> HttpResponse:
    is_register = mode == "register"
    title = "Create account" if is_register else "Login"
    action = "/accounts/register/" if is_register else "/accounts/login/"
    alternate = (
        'Already have an account? <a href="/accounts/login/">Login</a>'
        if is_register
        else 'New to RozLedger? <a href="/accounts/register/">Create an account</a>'
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
    next_value = escape(safe_next_url(request.GET.get("next") or request.POST.get("next")))
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
            <input name="password" type="password" autocomplete="current-password" required />
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
            <input name="password" type="password" autocomplete="new-password" required />
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
        "robots.txt": "text/plain",
        "sitemap.xml": "application/xml",
    }
    return serve_project_file(filename, content_types.get(filename))


@require_http_methods(GET_OR_HEAD)
def content_index(request: HttpRequest) -> FileResponse:
    return serve_project_file("content.html", "text/html")


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
def seo_page(request: HttpRequest, slug: str) -> FileResponse:
    safe_slug = slug.strip().lower()
    if not safe_slug or "/" in safe_slug or ".." in safe_slug:
        raise Http404("Page not found")
    return serve_project_file(f"pages/{safe_slug}/index.html", "text/html")


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
    invoices = Invoice.objects.filter(owner_email__iexact=email)[:20]
    leads = Lead.objects.filter(email__iexact=email)[:10]
    clients = Client.objects.filter(owner_email__iexact=email)[:20]
    subscription, _ = PlanSubscription.objects.get_or_create(owner_email=email)
    paid_count = Invoice.objects.filter(owner_email__iexact=email, status="paid").count()
    pending_count = Invoice.objects.filter(owner_email__iexact=email).exclude(status="paid").count()

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
              <p>Create an invoice from the main tool and use this account email to see it here.</p>
              <a class="button secondary" href="/#tool-panel">Create invoice</a>
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
        <div class="hero-actions">
          <a class="button primary" href="/#tool-panel">Create invoice</a>
          <a class="button secondary" href="/content/">Browse templates</a>
        </div>
      </section>
      <section class="dashboard-summary" aria-label="Account summary">
        <div><span>Pending invoices</span><strong>{pending_count}</strong></div>
        <div><span>Paid invoices</span><strong>{paid_count}</strong></div>
        <div><span>Saved clients</span><strong>{Client.objects.filter(owner_email__iexact=email).count()}</strong></div>
        <div><span>Plan</span><strong>{escape(subscription.get_plan_display())}</strong></div>
      </section>
      <section class="dashboard-section">
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
            <h3>Current plan: {escape(subscription.get_plan_display())}</h3>
            <p>Status: {escape(subscription.get_status_display())}. Online payment checkout will be connected after Razorpay or Stripe credentials are added.</p>
          </div>
          <form method="post" action="/dashboard/billing/request-pro/">
            {csrf_input(request)}
            <button class="button primary" type="submit">Request Pro activation</button>
          </form>
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
        owner_email=email,
        name=name,
        defaults={
            "email": clean_text(request.POST.get("email"), max_length=254),
            "phone": clean_text(request.POST.get("phone"), max_length=40),
            "gstin": clean_text(request.POST.get("gstin"), max_length=20).upper(),
        },
    )
    return redirect("/dashboard/")


def owned_invoice(request: HttpRequest, invoice_id: int) -> Invoice:
    try:
        return Invoice.objects.get(id=invoice_id, owner_email__iexact=current_account_email(request))
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc


@login_required
@require_http_methods(["GET", "POST"])
def invoice_edit(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    if request.method == "POST":
        amount_before_gst = decimal_value(request.POST.get("amount_before_gst"))
        gst_rate = decimal_value(request.POST.get("gst_rate"))
        if amount_before_gst > 0:
            invoice.business_name = clean_text(request.POST.get("business_name"), invoice.business_name, 180)
            invoice.client_name = clean_text(request.POST.get("client_name"), invoice.client_name, 180)
            invoice.service_name = clean_text(request.POST.get("service_name"), invoice.service_name, 240)
            invoice.amount_before_gst = amount_before_gst
            invoice.gst_rate = gst_rate
            status = clean_text(request.POST.get("status"), invoice.status, 20)
            invoice.status = status if status in dict(Invoice.STATUS_CHOICES) else invoice.status
            invoice.total_text = clean_text(request.POST.get("total_text"), invoice.total_text, 80)
            invoice.invoice_text = clean_text(request.POST.get("invoice_text"), invoice.invoice_text)
            invoice.save()
            Client.objects.update_or_create(owner_email=current_account_email(request), name=invoice.client_name)
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
        <form method="post" class="account-form">
          {csrf_input(request)}
          <label>Business name<input name="business_name" value="{escape(invoice.business_name)}" /></label>
          <label>Client name<input name="client_name" value="{escape(invoice.client_name)}" /></label>
          <label>Service<input name="service_name" value="{escape(invoice.service_name)}" /></label>
          <label>Amount before GST<input name="amount_before_gst" type="number" min="1" step="0.01" value="{invoice.amount_before_gst}" /></label>
          <label>GST rate<input name="gst_rate" type="number" min="0" step="0.01" value="{invoice.gst_rate}" /></label>
          <label>Total text<input name="total_text" value="{escape(invoice.total_text)}" /></label>
          <label>Status<select name="status">{status_options}</select></label>
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
@require_POST
def request_pro_activation(request: HttpRequest) -> HttpResponse:
    email = current_account_email(request)
    subscription, _ = PlanSubscription.objects.get_or_create(owner_email=email)
    subscription.plan = "pro"
    subscription.status = "requested"
    subscription.requested_at = timezone.now()
    subscription.save()
    send_mail(
        "RozLedger Pro activation requested",
        f"Pro activation requested by {email}.",
        settings.DEFAULT_FROM_EMAIL,
        [settings.ROZLEDGER_NOTIFY_EMAIL],
        fail_silently=True,
    )
    return redirect("/dashboard/")


@require_http_methods(GET_OR_HEAD)
def invoice_print(request: HttpRequest, token: str) -> HttpResponse:
    try:
        invoice = Invoice.objects.get(public_token=token)
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc

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
        <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/download.pdf">Download PDF</a>
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
def invoice_pdf(request: HttpRequest, token: str) -> HttpResponse:
    try:
        invoice = Invoice.objects.get(public_token=token)
    except Invoice.DoesNotExist as exc:
        raise Http404("Invoice not found") from exc

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=42, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("RozLedger Invoice", styles["Title"]),
        Spacer(1, 16),
        Paragraph(escape(invoice.business_name), styles["Heading1"]),
        Spacer(1, 12),
        Table(
            [
                ["Client", invoice.client_name],
                ["Service", invoice.service_name],
                ["GST rate", f"{invoice.gst_rate}%"],
                ["Total", invoice.total_text],
                ["Status", invoice.get_status_display()],
            ],
            colWidths=[110, 360],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7f5")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("PADDING", (0, 0), (-1, -1), 8),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ]
            ),
        ),
        Spacer(1, 18),
    ]
    for line in invoice.invoice_text.splitlines():
        story.append(Paragraph(escape(line) or "&nbsp;", styles["BodyText"]))
        story.append(Spacer(1, 4))
    story.append(Spacer(1, 16))
    story.append(Paragraph("Verify tax and legal details with a qualified professional.", styles["Italic"]))
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
    owner_email = clean_text(payload.get("owner_email"), max_length=254).lower()
    if request.user.is_authenticated and request.user.email:
        owner_email = request.user.email.lower()
    if owner_email and "@" not in owner_email:
        return JsonResponse({"error": "Enter a valid email to save this invoice."}, status=400)

    if amount_before_gst <= 0:
        return JsonResponse({"error": "Invoice amount must be greater than zero."}, status=400)

    invoice = Invoice.objects.create(
        owner_email=owner_email,
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
    if owner_email and invoice.client_name:
        Client.objects.get_or_create(owner_email=owner_email, name=invoice.client_name)

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
    payload = json_payload(request)
    click = AffiliateClick.objects.create(
        offer_name=clean_text(payload.get("offer_name"), "unknown", 160),
        destination_url=clean_text(payload.get("destination_url"), max_length=1000),
    )

    return JsonResponse({"ok": True, "id": click.id}, status=201)
