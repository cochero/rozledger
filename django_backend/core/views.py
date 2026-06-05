from __future__ import annotations

import json
import re
import secrets
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

from .models import Account, AffiliateClick, AuditLog, BusinessProfile, Client, Invoice, InvoiceLineItem, JournalEntry, JournalLine, Lead, PaymentGatewayConfig, PaymentReceipt, PlanSubscription, VendorBill


GET_OR_HEAD = ["GET", "HEAD"]
LEAD_ALLOWED_BUSINESS_TYPES = {"Freelancer", "Tutor or coaching", "Agency", "Shop or local service", "Consultant"}
HONEYPOT_FIELDS = ("website", "url", "homepage", "company_website")
SPAM_TEXT_RE = re.compile(
    r"(https?://|www\.|<a\s|</a>|casino|crypto|viagra|loan|backlink|telegram|escort|porn|forex|betting)",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
RUPEE_SYMBOL = "\u20b9"
FREE_MONTHLY_INVOICE_LIMIT = 5
PAID_MONTHLY_INVOICE_LIMIT = 100
DEFAULT_CHART = [
    ("1000", "Cash on hand", "asset", "debit"),
    ("1010", "Bank account", "asset", "debit"),
    ("1100", "Accounts receivable", "asset", "debit"),
    ("1200", "Tax receivable", "asset", "debit"),
    ("2000", "Accounts payable", "liability", "credit"),
    ("2100", "Sales tax / GST payable", "liability", "credit"),
    ("3000", "Owner equity", "equity", "credit"),
    ("4000", "Service income", "revenue", "credit"),
    ("4100", "Other income", "revenue", "credit"),
    ("5000", "Cost of services", "expense", "debit"),
    ("5100", "Office expenses", "expense", "debit"),
    ("5200", "Marketing expenses", "expense", "debit"),
    ("5300", "Travel expenses", "expense", "debit"),
    ("5400", "Software subscriptions", "expense", "debit"),
]


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


def current_market(request: HttpRequest) -> str:
    return "US" if is_us_host(request) else "IN"


def account_q(request: HttpRequest) -> Q:
    email = current_account_email(request)
    return (Q(owner=request.user) | Q(owner_email__iexact=email)) & Q(market=current_market(request))


def get_subscription(request: HttpRequest) -> PlanSubscription:
    email = current_account_email(request)
    market = current_market(request)
    subscription = PlanSubscription.objects.filter((Q(owner=request.user) | Q(owner_email__iexact=email)) & Q(market=market)).first()
    if subscription is None:
        subscription = PlanSubscription.objects.create(owner=request.user, owner_email=email, market=market)
    elif subscription.owner_id is None:
        subscription.owner = request.user
        subscription.save(update_fields=["owner", "updated_at"])
    return subscription


def subscription_for_email(email: str, market: str = "IN") -> PlanSubscription | None:
    if not email:
        return None
    return PlanSubscription.objects.filter(owner_email__iexact=email, market=market).first()


def invoice_month_range():
    now = timezone.localtime()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def invoice_quota_for_email(email: str, market: str = "IN") -> tuple[int, int, str]:
    subscription = subscription_for_email(email, market)
    limit = PAID_MONTHLY_INVOICE_LIMIT if subscription and subscription.is_pro_active else FREE_MONTHLY_INVOICE_LIMIT
    plan_name = "paid" if subscription and subscription.is_pro_active else "free"
    start, end = invoice_month_range()
    used = Invoice.objects.filter(owner_email__iexact=email, market=market, created_at__gte=start, created_at__lt=end).count()
    return used, limit, plan_name


def invoice_quota_message(used: int, limit: int, plan_name: str) -> str:
    if used >= limit:
        if plan_name == "paid":
            return f"Your paid plan allows {limit} invoices per month. Contact support if you need a higher limit."
        return f"Free plan allows {limit} invoices per month. Upgrade to create up to {PAID_MONTHLY_INVOICE_LIMIT} invoices per month."
    return f"{used}/{limit} invoices used this month on your {plan_name} plan."


def get_business_profile(request: HttpRequest) -> BusinessProfile | None:
    return BusinessProfile.objects.filter(account_q(request)).first()


def ensure_default_chart(request: HttpRequest) -> None:
    email = current_account_email(request)
    market = current_market(request)
    existing_codes = set(Account.objects.filter(account_q(request)).values_list("code", flat=True))
    accounts = []
    for code, name, account_type, normal_balance in DEFAULT_CHART:
        if code not in existing_codes:
            accounts.append(
                Account(
                    market=market,
                    owner=request.user,
                    owner_email=email,
                    code=code,
                    name=name,
                    account_type=account_type,
                    normal_balance=normal_balance,
                )
            )
    if accounts:
        Account.objects.bulk_create(accounts)


def account_options(accounts, selected_id: str = "") -> str:
    return "".join(
        f'<option value="{account.id}" {"selected" if selected_id and str(account.id) == str(selected_id) else ""}>{escape(account.code)} - {escape(account.name)}</option>'
        for account in accounts
    )


def invoice_options(invoices, selected_id: str = "") -> str:
    options = ['<option value="">Select open invoice or leave blank for direct receipt</option>']
    for invoice in invoices:
        number = invoice_number(invoice)
        total = invoice_total_display(invoice)
        label = f"{number} - {invoice.client_name} - {total} - {invoice.created_at:%d %b %Y}"
        options.append(
            f'<option value="{invoice.id}" data-client="{escape(invoice.client_name)}" data-amount="{invoice_total_amount(invoice)}" {"selected" if selected_id and str(invoice.id) == str(selected_id) else ""}>{escape(label)}</option>'
        )
    return "".join(options)


def client_name_options(clients, selected_name: str = "") -> str:
    options = ['<option value="">Select customer</option>']
    seen = set()
    for client in clients:
        if client.name in seen:
            continue
        seen.add(client.name)
        options.append(f'<option value="{escape(client.name)}" {"selected" if selected_name == client.name else ""}>{escape(client.name)}</option>')
    return "".join(options)


def journal_totals_for_user(request: HttpRequest) -> dict[str, Decimal]:
    entries = JournalEntry.objects.filter(account_q(request), is_posted=True)
    return {
        "assets": sum((line.debit - line.credit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="asset")), Decimal("0")),
        "liabilities": sum((line.credit - line.debit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="liability")), Decimal("0")),
        "income": sum((line.credit - line.debit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="revenue")), Decimal("0")),
        "expenses": sum((line.debit - line.credit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="expense")), Decimal("0")),
    }


def invoice_total_amount(invoice: Invoice) -> Decimal:
    subtotal = invoice_subtotal(invoice)
    tax = subtotal * invoice.gst_rate / Decimal("100") if invoice.include_gst else Decimal("0")
    return (subtotal + tax).quantize(Decimal("0.01"))


def invoice_subtotal(invoice: Invoice) -> Decimal:
    items = list(invoice.line_items.all()) if getattr(invoice, "pk", None) else []
    if items:
        return sum((item.amount for item in items), Decimal("0")).quantize(Decimal("0.01"))
    return invoice.amount_before_gst


def invoice_items(invoice: Invoice):
    items = list(invoice.line_items.all()) if getattr(invoice, "pk", None) else []
    if items:
        return items
    return [type("FallbackInvoiceItem", (), {"description": invoice.service_name, "quantity": Decimal("1"), "rate": invoice.amount_before_gst, "amount": invoice.amount_before_gst})()]


def format_quantity(value: Decimal) -> str:
    quantity = value.quantize(Decimal("0.01"))
    return f"{quantity.normalize():f}" if quantity == quantity.to_integral() else f"{quantity:f}"


def parse_invoice_line_items(source: Any) -> list[dict[str, Decimal | str]]:
    rows: list[dict[str, Decimal | str]] = []
    if hasattr(source, "getlist"):
        descriptions = source.getlist("item_description")
        quantities = source.getlist("item_quantity")
        rates = source.getlist("item_rate")
        for index, description in enumerate(descriptions):
            description = clean_text(description, max_length=240)
            quantity = decimal_value(quantities[index] if index < len(quantities) else "1")
            rate = decimal_value(rates[index] if index < len(rates) else "0")
            if not description and rate <= 0:
                continue
            rows.append(
                {
                    "description": description or "Item",
                    "quantity": quantity if quantity > 0 else Decimal("1"),
                    "rate": rate if rate >= 0 else Decimal("0"),
                }
            )
    else:
        for raw_item in source or []:
            if not isinstance(raw_item, dict):
                continue
            description = clean_text(raw_item.get("description"), max_length=240)
            quantity = decimal_value(raw_item.get("quantity"))
            rate = decimal_value(raw_item.get("rate"))
            if not description and rate <= 0:
                continue
            rows.append(
                {
                    "description": description or "Item",
                    "quantity": quantity if quantity > 0 else Decimal("1"),
                    "rate": rate if rate >= 0 else Decimal("0"),
                }
            )
    return rows


def invoice_items_subtotal(rows: list[dict[str, Decimal | str]]) -> Decimal:
    return sum((item["quantity"] * item["rate"] for item in rows), Decimal("0")).quantize(Decimal("0.01"))


def save_invoice_line_items(invoice: Invoice, rows: list[dict[str, Decimal | str]]) -> None:
    invoice.line_items.all().delete()
    if rows:
        InvoiceLineItem.objects.bulk_create(
            InvoiceLineItem(
                invoice=invoice,
                description=str(row["description"]),
                quantity=row["quantity"],
                rate=row["rate"],
            )
            for row in rows
        )


def invoice_item_rows_html(rows: list[dict[str, Decimal | str]], minimum_rows: int = 5) -> str:
    padded = rows + [{"description": "", "quantity": Decimal("1"), "rate": Decimal("0")}] * max(minimum_rows - len(rows), 0)
    return "".join(
        f"""
        <div class="invoice-item-row">
          <label><span>Item description</span><input name="item_description" value="{escape(str(row['description']))}" placeholder="Service, product or work completed" data-preview-field /></label>
          <label><span>Qty</span><input name="item_quantity" type="number" min="0.01" step="0.01" value="{escape(format_quantity(row['quantity']))}" data-preview-field /></label>
          <label><span>Rate</span><input name="item_rate" type="number" min="0" step="0.01" value="{escape(str(row['rate']))}" data-preview-field /></label>
        </div>
        """
        for row in padded
    )


def invoice_print_rows(invoice: Invoice) -> str:
    return "".join(
        f"""
          <tr>
            <td><strong>{escape(item.description)}</strong></td>
            <td>{escape(format_quantity(item.quantity))}</td>
            <td>{escape(invoice_money(invoice, item.rate))}</td>
            <td>{escape(invoice_money(invoice, item.amount))}</td>
          </tr>
        """
        for item in invoice_items(invoice)
    )


def account_by_code(request: HttpRequest, code: str) -> Account:
    ensure_default_chart(request)
    return Account.objects.get(account_q(request), code=code)


def post_two_line_entry(
    request: HttpRequest,
    *,
    entry_date,
    memo: str,
    source: str,
    debit_account: Account,
    credit_account: Account,
    amount: Decimal,
    description: str = "",
) -> JournalEntry:
    amount = amount.quantize(Decimal("0.01"))
    entry = JournalEntry.objects.create(
        market=current_market(request),
        owner=request.user,
        owner_email=current_account_email(request),
        entry_date=entry_date or timezone.localdate(),
        memo=memo,
        source=source,
        total_debit=amount,
        total_credit=amount,
    )
    JournalLine.objects.create(entry=entry, account=debit_account, description=description, debit=amount, credit=Decimal("0"))
    JournalLine.objects.create(entry=entry, account=credit_account, description=description, debit=Decimal("0"), credit=amount)
    return entry


def finance_summary_for_user(request: HttpRequest) -> dict[str, Decimal]:
    invoices = Invoice.objects.filter(account_q(request))
    bills = VendorBill.objects.filter(account_q(request))
    return {
        "accounts_receivable": sum((invoice_total_amount(invoice) for invoice in invoices.exclude(status="paid")), Decimal("0")),
        "accounts_payable": sum((bill.amount for bill in bills.filter(status="unpaid")), Decimal("0")),
        "cash_collected": sum((receipt.amount for receipt in PaymentReceipt.objects.filter(account_q(request))), Decimal("0")),
        "bills_paid": sum((bill.amount for bill in bills.filter(status="paid")), Decimal("0")),
    }


def aging_bucket(days_old: int) -> str:
    if days_old <= 0:
        return "Current"
    if days_old <= 30:
        return "1-30"
    if days_old <= 60:
        return "31-60"
    if days_old <= 90:
        return "61-90"
    return "90+"


def empty_aging_totals() -> dict[str, Decimal]:
    return {bucket: Decimal("0") for bucket in ("Current", "1-30", "31-60", "61-90", "90+")}


def cash_account_balances(request: HttpRequest) -> dict[str, Decimal]:
    entries = JournalEntry.objects.filter(account_q(request), is_posted=True)
    lines = JournalLine.objects.filter(entry__in=entries, account__code__in=["1000", "1010"])
    balances = {"cash": Decimal("0"), "bank": Decimal("0")}
    for line in lines:
        key = "cash" if line.account.code == "1000" else "bank"
        balances[key] += line.debit - line.credit
    balances["total"] = balances["cash"] + balances["bank"]
    return balances


def save_business_profile_from_invoice(invoice: Invoice, owner=None) -> None:
    if not invoice.owner_email:
        return
    defaults = {
        "owner": owner or invoice.owner,
        "business_name": invoice.business_name,
        "business_phone": invoice.business_phone,
        "business_address": invoice.business_address,
        "upi_link": invoice.upi_link,
        "bank_details": invoice.bank_details,
        "thank_you_note": invoice.thank_you_note,
        "template": invoice.template,
        "accent_color": invoice.accent_color,
    }
    if invoice.business_logo:
        defaults["business_logo"] = invoice.business_logo
    BusinessProfile.objects.update_or_create(market=invoice.market, owner_email=invoice.owner_email, defaults=defaults)


def save_client_from_invoice(invoice: Invoice, owner=None) -> None:
    if not invoice.owner_email or not invoice.client_name:
        return
    Client.objects.update_or_create(
        market=invoice.market,
        owner_email=invoice.owner_email,
        name=invoice.client_name,
        defaults={
            "owner": owner or invoice.owner,
            "phone": invoice.client_phone,
            "address": invoice.client_address,
            "gstin": invoice.client_gstin,
        },
    )


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


def rate_limited_page(request: HttpRequest) -> HttpResponse:
    body = """
    <main class="account-shell">
      <section class="account-card">
        <p class="eyebrow">Security</p>
        <h1>Too many attempts</h1>
        <p class="account-copy">Please wait a few minutes and try again.</p>
        <a class="button secondary" href="/">Back to RozLedger</a>
      </section>
    </main>
    """
    return page_shell("Too many attempts", body, request)


def audit_log(request: HttpRequest, action: str, object_type: str, object_id: str = "", summary: str = "") -> None:
    if not request.user.is_authenticated:
        return
    AuditLog.objects.create(
        market=current_market(request),
        owner=request.user,
        owner_email=current_account_email(request),
        action=action[:80],
        object_type=object_type[:80],
        object_id=str(object_id or "")[:80],
        summary=summary[:240],
        ip_address=client_ip(request)[:80],
    )


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


def is_us_host(request: HttpRequest) -> bool:
    host = request.get_host().split(":", 1)[0].lower()
    return host in {"rozledger.com", "www.rozledger.com"}


def invoice_is_us(invoice: Invoice) -> bool:
    return invoice.currency_symbol == "$" or (invoice.tax_label or "").lower() == "sales tax"


def invoice_total_text(amount_before_gst: Decimal, gst_rate: Decimal, include_gst: bool = True, currency_symbol: str = RUPEE_SYMBOL) -> str:
    total = amount_before_gst + (amount_before_gst * gst_rate / Decimal("100")) if include_gst else amount_before_gst
    return f"{currency_symbol} {total.quantize(Decimal('0.01'))}"


def build_invoice_text(invoice: Invoice) -> str:
    tax_label = invoice.tax_label or "GST"
    payment_label = "Payment link" if invoice_is_us(invoice) else "UPI/payment link"
    items_text = "\n".join(
        f"- {item.description}: {format_quantity(item.quantity)} x {invoice_money(invoice, item.rate)} = {invoice_money(invoice, item.amount)}"
        for item in invoice_items(invoice)
    )
    return "\n".join(
        [
            f"Invoice from {invoice.business_name}",
            invoice.business_address,
            f"Phone: {invoice.business_phone}" if invoice.business_phone else "",
            f"Client: {invoice.client_name}",
            f"Client phone: {invoice.client_phone}" if invoice.client_phone else "",
            invoice.client_address,
            f"Client GSTIN: {invoice.client_gstin}" if invoice.client_gstin and not invoice_is_us(invoice) else "",
            "Items:",
            items_text,
            f"Subtotal: {money(invoice_subtotal(invoice), invoice.currency_symbol)}",
            f"{tax_label}: {invoice.gst_rate}%" if invoice.include_gst else f"{tax_label}: Not charged",
            f"Total: {invoice.total_text}",
            f"{payment_label}: {invoice.upi_link}" if invoice.upi_link else "",
            f"Bank details: {invoice.bank_details}" if invoice.bank_details else "",
            invoice.thank_you_note or "Thank you for your business.",
        ]
    ).strip()


def invoice_logo_html(invoice: Invoice) -> str:
    if not invoice.business_logo:
        return ""
    return f'<img class="invoice-logo" src="/invoice/{escape(invoice.public_token)}/logo/" alt="{escape(invoice.business_name)} logo" />'


def image_content_type(filename: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(Path(filename).suffix.lower(), "application/octet-stream")


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
    return (invoice_subtotal(invoice) * invoice.gst_rate / Decimal("100")).quantize(Decimal("0.01"))


def money(value: Decimal, currency_symbol: str = RUPEE_SYMBOL) -> str:
    return f"{currency_symbol or RUPEE_SYMBOL} {value.quantize(Decimal('0.01'))}"


def invoice_money(invoice: Invoice, value: Decimal) -> str:
    return money(value, invoice.currency_symbol)


def invoice_tax_label(invoice: Invoice) -> str:
    return invoice.tax_label or "GST"


def invoice_total_display(invoice: Invoice) -> str:
    text = (invoice.total_text or "").strip()
    if text.lower().startswith("rs"):
        return f"{RUPEE_SYMBOL} {text[2:].strip()}"
    if text.startswith(RUPEE_SYMBOL) or text.startswith("$"):
        return text
    try:
        return money(Decimal(text), invoice.currency_symbol)
    except (InvalidOperation, ValueError):
        return text


def invoice_brand_url(invoice: Invoice) -> str:
    return "www.rozledger.com" if invoice.currency_symbol == "$" else "www.rozledger.in"


def clean_accent_color(value: Any) -> str:
    color = clean_text(value, "#126b4f", 7)
    return color if HEX_COLOR_RE.match(color) else "#126b4f"


def clean_invoice_template(value: Any) -> str:
    template = clean_text(value, "classic", 20)
    allowed = {choice[0] for choice in Invoice.TEMPLATE_CHOICES}
    return template if template in allowed else "classic"


def clean_currency_symbol(value: Any) -> str:
    symbol = clean_text(value, RUPEE_SYMBOL, 8)
    return symbol if symbol in {RUPEE_SYMBOL, "$"} else RUPEE_SYMBOL


def clean_tax_label(value: Any) -> str:
    return clean_text(value, "GST", 40) or "GST"


def preferred_gateway_for_market(market: str) -> str:
    return "stripe" if market == "US" else "razorpay"


def active_payment_gateway_for_market(market: str) -> PaymentGatewayConfig | None:
    return PaymentGatewayConfig.active_gateway(preferred_gateway_for_market(market), market)


def gateway_name_for_market(market: str) -> str:
    return "Stripe or PayPal" if market == "US" else "Razorpay"


def invoice_template_options(selected: str) -> str:
    return "".join(
        f'<option value="{escape(value)}" {"selected" if selected == value else ""}>{escape(label)}</option>'
        for value, label in Invoice.TEMPLATE_CHOICES
    )


def csrf_input(request: HttpRequest) -> str:
    return f'<input type="hidden" name="csrfmiddlewaretoken" value="{escape(get_token(request))}" />'


def generate_registration_captcha(request: HttpRequest) -> str:
    left = secrets.randbelow(8) + 2
    right = secrets.randbelow(8) + 2
    request.session["registration_captcha_answer"] = str(left + right)
    return f"{left} + {right}"


def registration_captcha_valid(request: HttpRequest) -> bool:
    expected = request.session.pop("registration_captcha_answer", "")
    provided = clean_text(request.POST.get("captcha_answer"), max_length=20)
    return bool(expected) and provided == expected


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


def market_price(request: HttpRequest) -> str:
    return "$3.99/month" if is_us_host(request) else f"{RUPEE_SYMBOL}299/month"


def whatsapp_url(message: str) -> str:
    return f"https://wa.me/919516022222?text={quote_plus(message)}"


def safe_next_url(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/dashboard/"


def brand_html() -> str:
    return '<a class="brand" href="/" aria-label="RozLedger home"><img class="brand-logo" src="/rozledger-logo.png" alt="RozLedger" /></a>'


def app_sidebar(request: HttpRequest | None = None) -> str:
    if not request or not request.user.is_authenticated:
        return ""
    links = [
        ("/dashboard/", "D", "Dashboard"),
        ("/dashboard/invoices/new/", "I", "Create invoice"),
        ("/dashboard/payments/new/", "R", "Payments received"),
        ("/dashboard/expenses/new/", "E", "Expenses & bills"),
        ("/dashboard/reports/", "P", "Reports"),
        ("/dashboard/business-profile/", "S", "Business profile"),
        ("/dashboard/#invoices", "C", "Customers"),
        ("/dashboard/#accounting", "A", "Chart of accounts"),
        ("/dashboard/billing/pro/", "B", "Billing"),
    ]
    current_path = request.path
    items = []
    for href, icon, label in links:
        base_href = href.split("#", 1)[0]
        is_active = current_path == base_href or (base_href != "/dashboard/" and current_path.startswith(base_href.rstrip("/") + "/"))
        items.append(
            f'<a class="app-nav-link {"active" if is_active else ""}" href="{href}"><span aria-hidden="true">{escape(icon)}</span><strong>{escape(label)}</strong></a>'
        )
    return f"""
    <aside class="app-sidebar" aria-label="Dashboard navigation">
      <div class="app-sidebar-brand">{brand_html()}</div>
      <nav class="app-nav">{''.join(items)}</nav>
      <div class="app-sidebar-footer">
        <span>Signed in</span>
        <strong>{escape(current_account_email(request))}</strong>
      </div>
    </aside>
    """


def subscription_status_copy(subscription: PlanSubscription) -> tuple[str, str, str]:
    if subscription.is_pro_active:
        expiry_copy = f" Trial expires on {subscription.expires_at:%d %b %Y}." if subscription.expires_at else ""
        return (
            "Paid plan active",
            f"Your RozLedger paid access is active. You can create up to {PAID_MONTHLY_INVOICE_LIMIT} invoices per month with saved clients, PDF downloads and payment status tracking.{expiry_copy}",
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
        f"You are using the free RozLedger plan with up to {FREE_MONTHLY_INVOICE_LIMIT} saved invoices per month. Upgrade when you need up to {PAID_MONTHLY_INVOICE_LIMIT} invoices per month.",
        "free",
    )


def page_shell(title: str, body: str, request: HttpRequest | None = None) -> HttpResponse:
    user_link = ""
    if request and request.user.is_authenticated:
        user_link = '<a href="/accounts/logout/">Logout</a>'
    else:
        user_link = '<a href="/accounts/login/">Login</a>'
    dashboard_link = '<a href="/dashboard/">Dashboard</a>' if request and request.user.is_authenticated else ""
    is_app = bool(request and request.user.is_authenticated)
    body_class = "account-page app-page" if is_app else "account-page"
    app_shell_open = f'<div class="app-layout">{app_sidebar(request)}<div class="app-main">' if is_app else ""
    app_shell_close = "</div></div>" if is_app else ""

    html = f"""<!doctype html>
<html lang="en">
  <head>
    {google_tag()}
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)} | RozLedger</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="{body_class}">
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
    {app_shell_open}
    {body}
    {app_shell_close}
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
    captcha_field = ""
    if is_register:
        captcha_question = generate_registration_captcha(request)
        captcha_field = f"""
        <label>
          Security check: {escape(captcha_question)}
          <input name="captcha_answer" inputmode="numeric" autocomplete="off" placeholder="Answer" required />
        </label>
        """
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
          {captcha_field}
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
    host = request.get_host().split(":", 1)[0].lower()
    if host in {"rozledger.com", "www.rozledger.com"}:
        return serve_project_file("index-us.html", "text/html")
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
    host = request.get_host().split(":", 1)[0].lower()
    if host in {"rozledger.com", "www.rozledger.com"}:
        return serve_project_file("content-us.html", "text/html")
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
    if is_us_host(request):
        return serve_project_file("contact-us.html", "text/html")
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

    if is_rate_limited(request, "register_ip", limit=8, window_seconds=900):
        return rate_limited_page(request)
    if email and is_rate_limited(request, "register_email", limit=3, window_seconds=3600, identity=email):
        return rate_limited_page(request)

    if "@" not in email or len(password) < 8:
        return auth_form(request, "register", "Enter a valid email and a password with at least 8 characters.")
    if not registration_captcha_valid(request):
        return auth_form(request, "register", "Please complete the security check correctly.")
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
    if is_rate_limited(request, "login_ip", limit=20, window_seconds=900):
        return rate_limited_page(request)
    if email and is_rate_limited(request, "login_email", limit=8, window_seconds=900, identity=email):
        return rate_limited_page(request)
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
    if is_rate_limited(request, "password_reset_ip", limit=6, window_seconds=900):
        return rate_limited_page(request)
    if email and is_rate_limited(request, "password_reset_email", limit=3, window_seconds=3600, identity=email):
        return rate_limited_page(request)
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
    ensure_default_chart(request)
    us_market = is_us_host(request)
    client_tax_id_label = "Tax ID" if us_market else "GSTIN"
    client_tax_id_placeholder = "Optional tax ID" if us_market else "Optional GSTIN"
    invoices = Invoice.objects.filter(account_q(request))[:20]
    leads = Lead.objects.filter(email__iexact=email, market=current_market(request))[:10]
    clients = Client.objects.filter(account_q(request))[:20]
    business_profile = get_business_profile(request)
    subscription = get_subscription(request)
    market = current_market(request)
    quota_used, quota_limit, quota_plan = invoice_quota_for_email(email, market)
    payment_gateway = active_payment_gateway_for_market(market)
    gateway_name = gateway_name_for_market(market)
    subscription_title, subscription_message, subscription_tone = subscription_status_copy(subscription)
    gateway_message = (
        f"{payment_gateway.get_gateway_display()} {payment_gateway.get_mode_display()} mode is enabled. Online checkout can be connected to this approval flow."
        if payment_gateway
        else f"Online payment checkout is disabled. {gateway_name} activation is currently handled by admin approval."
    )
    notice = ""
    if request.GET.get("pro") == "requested":
        notice = '<p class="dashboard-notice">Your Pro activation request was saved. Admin approval is pending.</p>'
    if request.GET.get("invoice") == "created":
        notice += '<p class="dashboard-notice">Invoice saved. It is now available in your dashboard.</p>'
    if request.GET.get("profile") == "saved":
        notice += '<p class="dashboard-notice">Business profile saved. New invoices will use these defaults.</p>'
    paid_count = Invoice.objects.filter(account_q(request), status="paid").count()
    pending_count = Invoice.objects.filter(account_q(request)).exclude(status="paid").count()
    accounts = Account.objects.filter(account_q(request), is_active=True)[:30]
    journal_entries = JournalEntry.objects.filter(account_q(request))[:10]
    accounting_totals = journal_totals_for_user(request)
    finance_summary = finance_summary_for_user(request)
    payment_receipts = PaymentReceipt.objects.filter(account_q(request))[:8]
    vendor_bills = VendorBill.objects.filter(account_q(request))[:8]

    invoice_rows = []
    for invoice in invoices:
        inv_number = invoice_number(invoice)
        invoice_rows.append(
            f"""
            <article class="dashboard-card invoice-card">
              <div>
                <div class="card-meta-row"><span>Invoice</span><strong class="status-pill status-{escape(invoice.status)}">{escape(invoice.get_status_display())}</strong></div>
                <h2>{escape(inv_number)}</h2>
                <p><strong>{escape(invoice.client_name)}</strong><br />{escape(invoice.service_name)}<br />{escape(invoice.total_text)} - {invoice.created_at:%d %b %Y}</p>
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
                <a class="button ghost" href="/dashboard/payments/new/?invoice={invoice.id}">Record payment</a>
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

    if business_profile:
        business_profile_html = f"""
        <article class="dashboard-card compact-card">
          <div>
            <span>Business profile</span>
            <h2>{escape(business_profile.business_name)}</h2>
            {f'<p>Phone: {escape(business_profile.business_phone)}</p>' if business_profile.business_phone else ''}
            <p>{escape(business_profile.business_address or 'No address saved yet').replace(chr(10), '<br />')}</p>
            {f'<p>{escape(business_profile.bank_details).replace(chr(10), "<br />")}</p>' if business_profile.bank_details else ''}
          </div>
          <div class="dashboard-actions">
            <a class="button secondary" href="/dashboard/business-profile/">Edit profile</a>
          </div>
        </article>
        """
    else:
        business_profile_html = """
        <article class="dashboard-card empty-state compact-card">
          <span>Business profile</span>
          <h2>No business profile yet</h2>
          <p>Create your profile now so invoices, payment notes, bank information and branding are ready before billing customers.</p>
          <a class="button secondary" href="/dashboard/business-profile/">Create profile</a>
        </article>
        """

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
                {f'<p>{escape(client.address).replace(chr(10), "<br />")}</p>' if client.address else ''}
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

    account_rows = []
    for account in accounts:
        account_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(account.get_account_type_display())}</span>
                <h2>{escape(account.code)} - {escape(account.name)}</h2>
                <p>{escape(account.get_normal_balance_display())} normal balance</p>
              </div>
            </article>
            """
        )

    journal_rows = []
    for entry in journal_entries:
        journal_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{entry.entry_date:%d %b %Y}</span>
                <h2>{escape(entry.memo)}</h2>
                <p>Debit {escape(money(entry.total_debit, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))} / Credit {escape(money(entry.total_credit, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</p>
              </div>
            </article>
            """
        )
    if not journal_rows:
        journal_rows.append(
            """
            <article class="dashboard-card empty-state compact-card">
              <span>Journal</span>
              <h2>No accounting entries yet</h2>
              <p>Record receipts, expenses, owner contributions and adjustments as journal entries.</p>
            </article>
            """
        )

    payment_rows = []
    for receipt in payment_receipts:
        receipt_invoice = f"{invoice_number(receipt.invoice)} - {receipt.invoice.client_name}" if receipt.invoice else "Direct receipt"
        payment_rows.append(
            f"""
            <article class="dashboard-card compact-card finance-record">
              <div>
                <span>{receipt.payment_date:%d %b %Y}</span>
                <h2>{escape(receipt.payer_name)}</h2>
                <p>{escape(receipt_invoice)}<br />{escape(money(receipt.amount, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))} via {escape(receipt.get_method_display())}</p>
              </div>
            </article>
            """
        )
    if not payment_rows:
        payment_rows.append(
            """
            <article class="dashboard-card empty-state compact-card">
              <span>Receipts</span>
              <h2>No payments recorded yet</h2>
              <p>Record customer payments to update income and collection totals.</p>
            </article>
            """
        )

    bill_rows = []
    for bill in vendor_bills:
        bill_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(bill.get_status_display())}</span>
                <h2>{escape(bill.vendor_name)}</h2>
                <p>{escape(bill.category)} - {escape(money(bill.amount, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</p>
                {f'<p>Due {bill.due_date:%d %b %Y}</p>' if bill.due_date else ''}
              </div>
            </article>
            """
        )
    if not bill_rows:
        bill_rows.append(
            """
            <article class="dashboard-card empty-state compact-card">
              <span>Payables</span>
              <h2>No vendor bills yet</h2>
              <p>Add expenses or unpaid vendor bills to track accounts payable.</p>
            </article>
            """
        )

    display_name = request.user.first_name or email
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero">
        <p class="eyebrow">Finance command center</p>
        <h1>Welcome, {escape(display_name)}.</h1>
        <p>Run daily billing, collections, expenses and simple accounts from one clean workspace connected to {escape(email)}.</p>
        {notice}
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/invoices/new/">Create invoice</a>
          <a class="button secondary" href="/dashboard/payments/new/">Record payment</a>
          <a class="button secondary" href="/dashboard/expenses/new/">Record expense</a>
        </div>
      </section>
      <section class="dashboard-module-grid" aria-label="Daily workflow">
        <a class="module-tile" href="/dashboard/invoices/new/"><span>01</span><strong>Create invoice</strong><p>Bill customers with saved business and client details.</p></a>
        <a class="module-tile" href="/dashboard/payments/new/"><span>02</span><strong>Collect payment</strong><p>Select customer and invoice, then post the receipt.</p></a>
        <a class="module-tile" href="/dashboard/expenses/new/"><span>03</span><strong>Record expense</strong><p>Track paid expenses and unpaid vendor bills.</p></a>
        <a class="module-tile" href="/dashboard/reports/"><span>04</span><strong>View reports</strong><p>Check profit, receivables, payables and cash position.</p></a>
        <a class="module-tile" href="/dashboard/business-profile/"><span>05</span><strong>Business profile</strong><p>Save company details, tax ID, bank info and invoice branding.</p></a>
      </section>
      <section class="dashboard-summary" aria-label="Account summary">
        <div><span>Pending invoices</span><strong>{pending_count}</strong></div>
        <div><span>Paid invoices</span><strong>{paid_count}</strong></div>
        <div><span>Saved clients</span><strong>{Client.objects.filter(account_q(request)).count()}</strong></div>
        <div><span>Plan</span><strong>{escape(subscription_title)}</strong></div>
        <div><span>Monthly invoices</span><strong>{quota_used}/{quota_limit}</strong></div>
        <div><span>Accounts receivable</span><strong>{escape(money(finance_summary['accounts_receivable'], '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</strong></div>
        <div><span>Accounts payable</span><strong>{escape(money(finance_summary['accounts_payable'], '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</strong></div>
        <div><span>Income</span><strong>{escape(money(accounting_totals['income'], '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</strong></div>
        <div><span>Expenses</span><strong>{escape(money(accounting_totals['expenses'], '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</strong></div>
      </section>
      <section class="dashboard-section">
        <div class="section-head">
          <p class="eyebrow">Company</p>
          <h2>Saved business profile</h2>
        </div>
        <div class="dashboard-grid">{business_profile_html}</div>
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
          <label>Address<textarea name="address" rows="2" placeholder="Client billing address"></textarea></label>
          <label>{client_tax_id_label}<input name="gstin" placeholder="{client_tax_id_placeholder}" /></label>
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
      <section class="dashboard-section" id="accounting">
        <div class="section-head">
          <p class="eyebrow">Accounting</p>
          <h2>Chart of accounts</h2>
        </div>
        <form method="post" action="/dashboard/accounting/accounts/" class="dashboard-form">
          {csrf_input(request)}
          <label>Code<input name="code" placeholder="6000" required /></label>
          <label>Name<input name="name" placeholder="Fuel expense" required /></label>
          <label>Type<select name="account_type">
            <option value="asset">Asset</option>
            <option value="liability">Liability</option>
            <option value="equity">Equity</option>
            <option value="revenue">Revenue</option>
            <option value="expense">Expense</option>
          </select></label>
          <label>Normal balance<select name="normal_balance">
            <option value="debit">Debit</option>
            <option value="credit">Credit</option>
          </select></label>
          <button class="button primary" type="submit">Add account</button>
        </form>
        <div class="dashboard-grid">{''.join(account_rows)}</div>
      </section>
      <section class="dashboard-section" id="receipts">
        <div class="section-head">
          <p class="eyebrow">Accounts receivable</p>
          <h2>Customer payments</h2>
          <p>Select the customer invoice before posting payment so the receipt carries invoice number, client and amount reference.</p>
        </div>
        <div class="dashboard-actions section-actions">
          <a class="button primary" href="/dashboard/payments/new/">Record payment received</a>
        </div>
        <div class="dashboard-grid">{''.join(payment_rows)}</div>
      </section>
      <section class="dashboard-section" id="payables">
        <div class="section-head">
          <p class="eyebrow">Accounts payable</p>
          <h2>Expenses and vendor bills</h2>
        </div>
        <div class="dashboard-actions section-actions">
          <a class="button primary" href="/dashboard/expenses/new/">Record expense or bill</a>
        </div>
        <div class="dashboard-grid">{''.join(bill_rows)}</div>
      </section>
      <section class="dashboard-section">
        <div class="section-head">
          <p class="eyebrow">Accounting</p>
          <h2>Journal entries</h2>
        </div>
        <div class="dashboard-actions section-actions">
          <a class="button primary" href="/dashboard/accounting/journal/new/">Record journal entry</a>
        </div>
        <div class="dashboard-grid">{''.join(journal_rows)}</div>
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
        market=current_market(request),
        owner_email=email,
        name=name,
        defaults={
            "owner": request.user,
            "email": clean_text(request.POST.get("email"), max_length=254),
            "phone": clean_text(request.POST.get("phone"), max_length=40),
            "address": clean_text(request.POST.get("address")),
            "gstin": clean_text(request.POST.get("gstin"), max_length=20).upper(),
        },
    )
    return redirect("/dashboard/")


@login_required
@require_http_methods(["GET", "POST"])
def business_profile(request: HttpRequest) -> HttpResponse:
    profile = get_business_profile(request)
    us_market = is_us_host(request)
    tax_label = "Tax ID" if us_market else "GSTIN"
    payment_label = "Payment link" if us_market else "UPI/payment link"
    payment_placeholder = "Stripe, Square, PayPal, Venmo, Cash App or payment note" if us_market else "UPI link or payment note"
    values = {
        "business_name": profile.business_name if profile else request.user.first_name or "",
        "business_phone": profile.business_phone if profile else "",
        "business_address": profile.business_address if profile else "",
        "gstin": profile.gstin if profile else "",
        "upi_link": profile.upi_link if profile else "",
        "bank_details": profile.bank_details if profile else "",
        "thank_you_note": profile.thank_you_note if profile else "Thank you for your business.",
        "template": profile.template if profile else "classic",
        "accent_color": profile.accent_color if profile else "#126b4f",
    }
    error = ""
    if request.method == "POST":
        values = {
            "business_name": clean_text(request.POST.get("business_name"), max_length=180),
            "business_phone": clean_text(request.POST.get("business_phone"), max_length=40),
            "business_address": clean_text(request.POST.get("business_address")),
            "gstin": clean_text(request.POST.get("gstin"), max_length=20).upper(),
            "upi_link": clean_text(request.POST.get("upi_link")),
            "bank_details": clean_text(request.POST.get("bank_details")),
            "thank_you_note": clean_text(request.POST.get("thank_you_note")),
            "template": clean_invoice_template(request.POST.get("template")),
            "accent_color": clean_accent_color(request.POST.get("accent_color")),
        }
        logo_upload = request.FILES.get("business_logo")
        logo_error = valid_logo_upload(logo_upload)
        if not values["business_name"]:
            error = "Business name is required."
        elif logo_error:
            error = logo_error
        else:
            profile, _created = BusinessProfile.objects.update_or_create(
                market=current_market(request),
                owner_email=current_account_email(request),
                defaults={
                    "owner": request.user,
                    **values,
                },
            )
            if logo_upload:
                profile.business_logo = logo_upload
                profile.save(update_fields=["business_logo", "updated_at"])
            audit_log(request, "business_profile.saved", "BusinessProfile", profile.id, f"Saved business profile for {profile.business_name}")
            return redirect("/dashboard/?profile=saved")

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    logo_hint = "Current logo is saved. Upload a new file to replace it." if profile and profile.business_logo else "Optional PNG, JPG, WebP or GIF logo."
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Settings</p>
        <h1>Business profile</h1>
        <p class="account-copy">Save your company details once. RozLedger will use this profile to pre-fill invoices, payment notes, bank details and invoice styling.</p>
        {error_html}
        <form method="post" class="account-form invoice-server-form" enctype="multipart/form-data">
          {csrf_input(request)}
          <label>Business name<input name="business_name" value="{escape(values['business_name'])}" placeholder="Your company or trade name" required /></label>
          <label>Business phone<input name="business_phone" value="{escape(values['business_phone'])}" placeholder="Phone or WhatsApp number" /></label>
          <label class="full-row">Business full address<textarea name="business_address" rows="3" placeholder="Registered or billing address">{escape(values['business_address'])}</textarea></label>
          <label>{tax_label}<input name="gstin" value="{escape(values['gstin'])}" placeholder="Optional {tax_label}" /></label>
          <label>{payment_label}<input name="upi_link" value="{escape(values['upi_link'])}" placeholder="{escape(payment_placeholder)}" /></label>
          <label class="full-row">Bank information<textarea name="bank_details" rows="4" placeholder="Bank name, account number, routing/IFSC, account holder">{escape(values['bank_details'])}</textarea></label>
          <label class="full-row">Default thank you note<textarea name="thank_you_note" rows="3" placeholder="Thank you for your business.">{escape(values['thank_you_note'])}</textarea></label>
          <label>Default invoice template<select name="template">{invoice_template_options(values['template'])}</select></label>
          <label>Brand/accent color<input name="accent_color" type="color" value="{escape(values['accent_color'])}" /></label>
          <label class="full-row">Business logo<input name="business_logo" type="file" accept="image/png,image/jpeg,image/webp,image/gif" /></label>
          <p class="form-hint full-row">{escape(logo_hint)}</p>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Save business profile</button>
            <a class="button secondary" href="/dashboard/">Back to dashboard</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Business profile", body, request)


@login_required
@require_GET
def business_profile_logo(request: HttpRequest) -> HttpResponse:
    profile = get_business_profile(request)
    if not profile or not profile.business_logo:
        raise Http404("Logo not found")
    response = FileResponse(profile.business_logo.open("rb"))
    response["Content-Type"] = image_content_type(profile.business_logo.name)
    return response


@login_required
@require_POST
def create_account(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    code = clean_text(request.POST.get("code"), max_length=20)
    name = clean_text(request.POST.get("name"), max_length=180)
    account_type = clean_text(request.POST.get("account_type"), max_length=20)
    normal_balance = clean_text(request.POST.get("normal_balance"), max_length=10)
    if not code or not name:
        return redirect("/dashboard/#accounting")
    if account_type not in {"asset", "liability", "equity", "revenue", "expense"}:
        account_type = "expense"
    if normal_balance not in {"debit", "credit"}:
        normal_balance = "debit" if account_type in {"asset", "expense"} else "credit"
    Account.objects.update_or_create(
        market=current_market(request),
        owner_email=current_account_email(request),
        code=code,
        defaults={
            "owner": request.user,
            "name": name,
            "account_type": account_type,
            "normal_balance": normal_balance,
            "is_active": True,
        },
    )
    return redirect("/dashboard/#accounting")


@login_required
@require_http_methods(["GET", "POST"])
def journal_new(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    accounts = list(Account.objects.filter(account_q(request), is_active=True))
    error = ""
    values = {
        "entry_date": f"{timezone.localdate():%Y-%m-%d}",
        "memo": "",
        "debit_account": "",
        "debit_amount": "",
        "credit_account": "",
        "credit_amount": "",
        "description": "",
    }
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        debit_amount = decimal_value(values["debit_amount"])
        credit_amount = decimal_value(values["credit_amount"])
        debit_account = next((account for account in accounts if str(account.id) == values["debit_account"]), None)
        credit_account = next((account for account in accounts if str(account.id) == values["credit_account"]), None)
        if not values["memo"]:
            error = "Memo is required."
        elif not debit_account or not credit_account:
            error = "Choose debit and credit accounts."
        elif debit_account.id == credit_account.id:
            error = "Debit and credit accounts must be different."
        elif debit_amount <= 0 or credit_amount <= 0:
            error = "Debit and credit amounts must be greater than zero."
        elif debit_amount != credit_amount:
            error = "Journal entry must balance: debit must equal credit."
        else:
            entry = JournalEntry.objects.create(
                market=current_market(request),
                owner=request.user,
                owner_email=current_account_email(request),
                entry_date=values["entry_date"] or timezone.localdate(),
                memo=values["memo"],
                source="manual",
                total_debit=debit_amount,
                total_credit=credit_amount,
            )
            JournalLine.objects.create(entry=entry, account=debit_account, description=values["description"], debit=debit_amount, credit=Decimal("0"))
            JournalLine.objects.create(entry=entry, account=credit_account, description=values["description"], debit=Decimal("0"), credit=credit_amount)
            return redirect("/dashboard/#accounting")

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card">
        <p class="eyebrow">Accounting</p>
        <h1>Record journal entry</h1>
        <p class="account-copy">Use this for owner contributions, expense payments, corrections and manual accounting adjustments. Debit must equal credit.</p>
        {error_html}
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Date<input name="entry_date" type="date" value="{escape(values['entry_date'])}" required /></label>
          <label>Memo<input name="memo" value="{escape(values['memo'])}" placeholder="Paid software subscription" required /></label>
          <label>Debit account<select name="debit_account">{account_options(accounts, values['debit_account'])}</select></label>
          <label>Debit amount<input name="debit_amount" type="number" min="0.01" step="0.01" value="{escape(values['debit_amount'])}" required /></label>
          <label>Credit account<select name="credit_account">{account_options(accounts, values['credit_account'])}</select></label>
          <label>Credit amount<input name="credit_amount" type="number" min="0.01" step="0.01" value="{escape(values['credit_amount'])}" required /></label>
          <label class="full-row">Description<input name="description" value="{escape(values['description'])}" placeholder="Optional line note" /></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post journal entry</button>
            <a class="button secondary" href="/dashboard/#accounting">Back to accounting</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Record journal entry", body, request)


def payment_method_options(selected: str = "bank") -> str:
    choices = [
        ("bank", "Bank transfer"),
        ("cash", "Cash"),
        ("upi", "UPI"),
        ("card", "Card"),
        ("check", "Check"),
        ("paypal", "PayPal"),
        ("stripe", "Stripe"),
        ("other", "Other"),
    ]
    return "".join(f'<option value="{value}" {"selected" if value == selected else ""}>{label}</option>' for value, label in choices)


@login_required
@require_GET
def reports(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    email = current_account_email(request)
    today = timezone.localdate()
    totals = journal_totals_for_user(request)
    finance = finance_summary_for_user(request)
    cash = cash_account_balances(request)
    profit = totals["income"] - totals["expenses"]

    ar_totals = empty_aging_totals()
    ar_rows = []
    for invoice in Invoice.objects.filter(account_q(request)).exclude(status="paid").order_by("created_at"):
        due_date = timezone.localtime(invoice.created_at).date() + timedelta(days=invoice.due_days)
        bucket = aging_bucket((today - due_date).days)
        amount = invoice_total_amount(invoice)
        ar_totals[bucket] += amount
        ar_rows.append(
            f"""
            <tr>
              <td><strong>{escape(invoice_number(invoice))}</strong><span>{escape(invoice.client_name)}</span></td>
              <td>{escape(invoice.created_at.strftime('%d %b %Y'))}</td>
              <td>{escape(due_date.strftime('%d %b %Y'))}</td>
              <td>{escape(bucket)}</td>
              <td class="amount-cell">{escape(money(amount, currency))}</td>
            </tr>
            """
        )
    if not ar_rows:
        ar_rows.append('<tr><td colspan="5" class="empty-report-row">No unpaid customer invoices.</td></tr>')

    ap_totals = empty_aging_totals()
    ap_rows = []
    for bill in VendorBill.objects.filter(account_q(request), status="unpaid").order_by("due_date", "bill_date"):
        due_date = bill.due_date or bill.bill_date
        bucket = aging_bucket((today - due_date).days)
        ap_totals[bucket] += bill.amount
        ap_rows.append(
            f"""
            <tr>
              <td><strong>{escape(bill.vendor_name)}</strong><span>{escape(bill.category)}</span></td>
              <td>{escape(bill.bill_date.strftime('%d %b %Y'))}</td>
              <td>{escape(due_date.strftime('%d %b %Y'))}</td>
              <td>{escape(bucket)}</td>
              <td class="amount-cell">{escape(money(bill.amount, currency))}</td>
            </tr>
            """
        )
    if not ap_rows:
        ap_rows.append('<tr><td colspan="5" class="empty-report-row">No unpaid vendor bills.</td></tr>')

    def aging_cards(totals_by_bucket: dict[str, Decimal]) -> str:
        return "".join(
            f'<div><span>{escape(bucket)}</span><strong>{escape(money(amount, currency))}</strong></div>'
            for bucket, amount in totals_by_bucket.items()
        )

    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Reports</p>
        <h1>Financial reports</h1>
        <p>Review profit, unpaid customer invoices, unpaid vendor bills and cash movement for {escape(email)}.</p>
        <div class="hero-actions">
          <a class="button secondary" href="/dashboard/">Back to dashboard</a>
          <a class="button primary" href="/dashboard/payments/new/">Record payment</a>
          <a class="button secondary" href="/dashboard/expenses/new/">Record expense</a>
        </div>
      </section>
      <section class="report-kpi-grid" aria-label="Report summary">
        <article><span>Income</span><strong>{escape(money(totals['income'], currency))}</strong></article>
        <article><span>Expenses</span><strong>{escape(money(totals['expenses'], currency))}</strong></article>
        <article><span>Net profit</span><strong>{escape(money(profit, currency))}</strong></article>
        <article><span>Cash & bank</span><strong>{escape(money(cash['total'], currency))}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Profit & Loss</p>
          <h2>Income minus expenses</h2>
          <p>Based on posted journal entries. Payment received posts income; paid expenses and unpaid vendor bills post expenses.</p>
        </div>
        <div class="report-statement">
          <div><span>Income</span><strong>{escape(money(totals['income'], currency))}</strong></div>
          <div><span>Expenses</span><strong>{escape(money(totals['expenses'], currency))}</strong></div>
          <div class="statement-total"><span>Net profit</span><strong>{escape(money(profit, currency))}</strong></div>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Accounts receivable</p>
          <h2>AR aging by invoice and customer</h2>
          <p>Total unpaid customer invoices: {escape(money(finance['accounts_receivable'], currency))}.</p>
        </div>
        <div class="aging-grid">{aging_cards(ar_totals)}</div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Invoice / customer</th><th>Invoice date</th><th>Due date</th><th>Aging</th><th>Amount</th></tr></thead>
            <tbody>{''.join(ar_rows)}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Accounts payable</p>
          <h2>AP aging by vendor</h2>
          <p>Total unpaid vendor bills: {escape(money(finance['accounts_payable'], currency))}.</p>
        </div>
        <div class="aging-grid">{aging_cards(ap_totals)}</div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Vendor / category</th><th>Bill date</th><th>Due date</th><th>Aging</th><th>Amount</th></tr></thead>
            <tbody>{''.join(ap_rows)}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Cash summary</p>
          <h2>Cash and bank position</h2>
          <p>Based on posted journal lines to Cash on hand and Bank account.</p>
        </div>
        <div class="report-statement">
          <div><span>Cash on hand</span><strong>{escape(money(cash['cash'], currency))}</strong></div>
          <div><span>Bank account</span><strong>{escape(money(cash['bank'], currency))}</strong></div>
          <div><span>Customer payments recorded</span><strong>{escape(money(finance['cash_collected'], currency))}</strong></div>
          <div><span>Bills paid</span><strong>{escape(money(finance['bills_paid'], currency))}</strong></div>
          <div class="statement-total"><span>Total cash and bank</span><strong>{escape(money(cash['total'], currency))}</strong></div>
        </div>
      </section>
    </main>
    """
    return page_shell("Financial reports", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def payment_new(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    clients = list(Client.objects.filter(account_q(request)).order_by("name"))
    open_invoices = list(Invoice.objects.filter(account_q(request)).exclude(status="paid").order_by("-created_at"))
    invoice = None
    invoice_id = clean_text(request.GET.get("invoice") or request.POST.get("invoice_id"), max_length=20)
    if invoice_id:
        try:
            invoice = owned_invoice(request, int(invoice_id))
        except (ValueError, Http404):
            invoice = None
    values = {
        "payment_date": f"{timezone.localdate():%Y-%m-%d}",
        "client_name": invoice.client_name if invoice else "",
        "payer_name": invoice.client_name if invoice else "",
        "amount": f"{invoice_total_amount(invoice)}" if invoice else "",
        "method": "bank",
        "reference": "",
        "notes": "",
    }
    error = ""
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        if invoice:
            values["client_name"] = values["client_name"] or invoice.client_name
            values["payer_name"] = values["payer_name"] or invoice.client_name
            values["amount"] = values["amount"] or f"{invoice_total_amount(invoice)}"
        amount = decimal_value(values["amount"])
        if not values["payer_name"]:
            error = "Payer name is required."
        elif amount <= 0:
            error = "Payment amount must be greater than zero."
        else:
            debit_account = account_by_code(request, "1000" if values["method"] == "cash" else "1010")
            credit_account = account_by_code(request, "4000")
            entry = post_two_line_entry(
                request,
                entry_date=values["payment_date"] or timezone.localdate(),
                memo=f"Payment received from {values['payer_name']}",
                source="payment_received",
                debit_account=debit_account,
                credit_account=credit_account,
                amount=amount,
                description=values["reference"] or values["notes"],
            )
            receipt = PaymentReceipt.objects.create(
                market=current_market(request),
                owner=request.user,
                owner_email=current_account_email(request),
                invoice=invoice,
                journal_entry=entry,
                payment_date=values["payment_date"] or timezone.localdate(),
                payer_name=values["payer_name"],
                amount=amount,
                method=values["method"] if values["method"] in {"bank", "cash", "upi", "card", "check", "paypal", "stripe", "other"} else "bank",
                reference=values["reference"],
                notes=values["notes"],
            )
            if receipt.invoice:
                receipt.invoice.status = "paid"
                receipt.invoice.save(update_fields=["status", "updated_at"])
            audit_log(request, "payment_receipt.created", "PaymentReceipt", receipt.id, f"Recorded payment from {receipt.payer_name} for {receipt.amount}")
            return redirect("/dashboard/#receipts")

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    linked_invoice_html = ""
    if invoice:
        linked_invoice_html = f"""
        <div class="selected-invoice-panel">
          <span>Selected invoice</span>
          <strong>{escape(invoice_number(invoice))}</strong>
          <p>{escape(invoice.client_name)} - {escape(invoice_total_display(invoice))} - {invoice.created_at:%d %b %Y}</p>
        </div>
        """
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Accounts receivable</p>
        <h1>Record customer payment</h1>
        <p class="account-copy">Select the customer and invoice first. RozLedger uses the invoice number and amount as the payment reference, posts a balanced receipt entry and marks the invoice paid.</p>
        {error_html}
        {linked_invoice_html}
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Customer<select name="client_name" id="payment-client">{client_name_options(clients, values['client_name'])}</select></label>
          <label>Invoice / reference<select name="invoice_id" id="payment-invoice">{invoice_options(open_invoices, invoice_id)}</select></label>
          <label>Date<input name="payment_date" type="date" value="{escape(values['payment_date'])}" required /></label>
          <label>Payer name<input name="payer_name" value="{escape(values['payer_name'])}" placeholder="Client or customer name" required /></label>
          <label>Amount<input name="amount" type="number" min="0.01" step="0.01" value="{escape(values['amount'])}" required /></label>
          <label>Method<select name="method">{payment_method_options(values['method'])}</select></label>
          <label>Reference<input name="reference" value="{escape(values['reference'])}" placeholder="UPI ref, check no, Stripe payment ID" /></label>
          <label>Notes<input name="notes" value="{escape(values['notes'])}" placeholder="Optional note" /></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post payment received</button>
            <a class="button secondary" href="/dashboard/#receipts">Back to dashboard</a>
          </div>
        </form>
        <script>
          (() => {{
            const client = document.getElementById('payment-client');
            const invoice = document.getElementById('payment-invoice');
            const payer = document.querySelector('[name="payer_name"]');
            const amount = document.querySelector('[name="amount"]');
            if (!client || !invoice || !payer || !amount) return;
            const syncFromInvoice = () => {{
              const selected = invoice.options[invoice.selectedIndex];
              if (!selected || !selected.value) return;
              const selectedClient = selected.dataset.client || '';
              const selectedAmount = selected.dataset.amount || '';
              if (selectedClient) {{
                client.value = selectedClient;
                payer.value = selectedClient;
              }}
              if (selectedAmount) amount.value = selectedAmount;
            }};
            client.addEventListener('change', () => {{
              const wanted = client.value;
              Array.from(invoice.options).forEach((option) => {{
                option.hidden = Boolean(wanted && option.dataset.client && option.dataset.client !== wanted);
              }});
              if (invoice.selectedOptions[0]?.hidden) invoice.value = '';
              if (!payer.value) payer.value = wanted;
            }});
            invoice.addEventListener('change', syncFromInvoice);
            syncFromInvoice();
          }})();
        </script>
      </section>
    </main>
    """
    return page_shell("Record payment received", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def expense_new(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    expense_accounts = list(Account.objects.filter(account_q(request), is_active=True, account_type="expense"))
    values = {
        "bill_date": f"{timezone.localdate():%Y-%m-%d}",
        "due_date": "",
        "vendor_name": "",
        "category": "Office expenses",
        "expense_account": str(next((account.id for account in expense_accounts if account.code == "5100"), expense_accounts[0].id if expense_accounts else "")),
        "amount": "",
        "status": "paid",
        "payment_method": "bank",
        "reference": "",
        "notes": "",
    }
    error = ""
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        amount = decimal_value(values["amount"])
        expense_account = next((account for account in expense_accounts if str(account.id) == values["expense_account"]), None)
        status = values["status"] if values["status"] in {"paid", "unpaid"} else "paid"
        if not values["vendor_name"]:
            error = "Vendor name is required."
        elif amount <= 0:
            error = "Expense amount must be greater than zero."
        elif expense_account is None:
            error = "Choose an expense account."
        else:
            credit_account = account_by_code(request, "2000" if status == "unpaid" else ("1000" if values["payment_method"] == "cash" else "1010"))
            entry = post_two_line_entry(
                request,
                entry_date=values["bill_date"] or timezone.localdate(),
                memo=f"{'Vendor bill' if status == 'unpaid' else 'Expense paid'} - {values['vendor_name']}",
                source="vendor_bill" if status == "unpaid" else "expense_paid",
                debit_account=expense_account,
                credit_account=credit_account,
                amount=amount,
                description=values["reference"] or values["notes"],
            )
            bill = VendorBill.objects.create(
                market=current_market(request),
                owner=request.user,
                owner_email=current_account_email(request),
                journal_entry=entry,
                bill_date=values["bill_date"] or timezone.localdate(),
                due_date=values["due_date"] or None,
                vendor_name=values["vendor_name"],
                category=values["category"] or expense_account.name,
                amount=amount,
                status=status,
                payment_method=values["payment_method"] if values["payment_method"] in {"bank", "cash", "upi", "card", "check", "paypal", "stripe", "other"} else "bank",
                reference=values["reference"],
                notes=values["notes"],
            )
            audit_log(request, "vendor_bill.created", "VendorBill", bill.id, f"Recorded {status} bill for {bill.vendor_name} amount {bill.amount}")
            return redirect("/dashboard/#payables")

    status_options = "".join(f'<option value="{value}" {"selected" if values["status"] == value else ""}>{label}</option>' for value, label in [("paid", "Paid now"), ("unpaid", "Unpaid vendor bill")])
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card">
        <p class="eyebrow">Accounts payable</p>
        <h1>Record expense or vendor bill</h1>
        <p class="account-copy">Use this for paid expenses and unpaid supplier bills. Paid expenses reduce bank or cash; unpaid bills increase accounts payable.</p>
        {error_html}
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Bill date<input name="bill_date" type="date" value="{escape(values['bill_date'])}" required /></label>
          <label>Due date<input name="due_date" type="date" value="{escape(values['due_date'])}" /></label>
          <label>Vendor name<input name="vendor_name" value="{escape(values['vendor_name'])}" placeholder="Supplier or vendor" required /></label>
          <label>Category<input name="category" value="{escape(values['category'])}" placeholder="Office expenses" /></label>
          <label>Expense account<select name="expense_account">{account_options(expense_accounts, values['expense_account'])}</select></label>
          <label>Amount<input name="amount" type="number" min="0.01" step="0.01" value="{escape(values['amount'])}" required /></label>
          <label>Status<select name="status">{status_options}</select></label>
          <label>Payment method<select name="payment_method">{payment_method_options(values['payment_method'])}</select></label>
          <label>Reference<input name="reference" value="{escape(values['reference'])}" placeholder="Bill number, payment ref or receipt number" /></label>
          <label>Notes<input name="notes" value="{escape(values['notes'])}" placeholder="Optional note" /></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post expense or bill</button>
            <a class="button secondary" href="/dashboard/#payables">Back to dashboard</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Record expense or bill", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def invoice_new(request: HttpRequest) -> HttpResponse:
    error = ""
    profile = get_business_profile(request)
    us_market = is_us_host(request)
    tax_label = "Sales tax" if us_market else "GST"
    currency_symbol = "$" if us_market else RUPEE_SYMBOL
    tax_id_label = "Client tax ID" if us_market else "Client GSTIN"
    amount_label = "Amount before tax" if us_market else "Amount before GST"
    tax_rate_label = "Sales tax rate %" if us_market else "GST rate %"
    include_tax_label = "Add sales tax to this invoice" if us_market else "Include GST on this invoice"
    payment_link_label = "Payment link" if us_market else "UPI/payment link"
    payment_placeholder = "Stripe, Square, PayPal, Venmo, Cash App or payment note" if us_market else "Optional UPI link or payment note"
    values = {
        "template": profile.template if profile else "classic",
        "accent_color": profile.accent_color if profile else "#126b4f",
        "business_name": profile.business_name if profile else request.user.first_name or "Your business",
        "business_phone": profile.business_phone if profile else "",
        "business_address": profile.business_address if profile else "",
        "client_name": "",
        "client_phone": "",
        "client_address": "",
        "client_gstin": "",
        "service_name": "",
        "include_gst": "" if us_market else "on",
        "amount_before_gst": "",
        "gst_rate": "0" if us_market else "18",
        "due_days": "7",
        "upi_link": profile.upi_link if profile else "",
        "bank_details": profile.bank_details if profile else "",
        "thank_you_note": profile.thank_you_note if profile and profile.thank_you_note else "Thank you for your business.",
    }
    item_rows: list[dict[str, Decimal | str]] = []

    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        values["template"] = clean_invoice_template(request.POST.get("template"))
        values["accent_color"] = clean_accent_color(request.POST.get("accent_color"))
        item_rows = parse_invoice_line_items(request.POST)
        if not item_rows and values["service_name"]:
            item_rows = [{"description": values["service_name"], "quantity": Decimal("1"), "rate": decimal_value(values["amount_before_gst"])}]
        amount_before_gst = invoice_items_subtotal(item_rows)
        include_gst = request.POST.get("include_gst") == "on"
        gst_rate = decimal_value(values["gst_rate"]) if include_gst else Decimal("0")
        due_days_raw = digits_only(values["due_days"])
        due_days = int(due_days_raw or 0)
        logo_upload = request.FILES.get("business_logo")
        logo_error = valid_logo_upload(logo_upload)
        service_name = clean_text(item_rows[0]["description"], "Service", 240) if item_rows else ""
        values["service_name"] = service_name
        values["amount_before_gst"] = str(amount_before_gst) if amount_before_gst > 0 else values["amount_before_gst"]

        if not values["business_name"] or not values["client_name"] or not service_name:
            error = "Business name, client name and at least one invoice item are required."
        elif amount_before_gst <= 0:
            error = "Invoice item total must be greater than zero."
        elif logo_error:
            error = logo_error
        else:
            quota_used, quota_limit, quota_plan = invoice_quota_for_email(current_account_email(request), current_market(request))
            if quota_used >= quota_limit:
                error = invoice_quota_message(quota_used, quota_limit, quota_plan)
            else:
                invoice = Invoice.objects.create(
                    market=current_market(request),
                    owner=request.user,
                    owner_email=current_account_email(request),
                    template=values["template"],
                    accent_color=values["accent_color"],
                    business_name=values["business_name"],
                    business_phone=values["business_phone"],
                    business_address=values["business_address"],
                    client_name=values["client_name"],
                    client_phone=values["client_phone"],
                    client_address=values["client_address"],
                    client_gstin=values["client_gstin"].upper(),
                    service_name=service_name,
                    include_gst=include_gst,
                    amount_before_gst=amount_before_gst,
                    gst_rate=gst_rate,
                    tax_label=tax_label,
                    currency_symbol=currency_symbol,
                    due_days=due_days,
                    total_text=invoice_total_text(amount_before_gst, gst_rate, include_gst, currency_symbol),
                    upi_link=values["upi_link"],
                    bank_details=values["bank_details"],
                    thank_you_note=values["thank_you_note"],
                    invoice_text="",
                )
                if logo_upload:
                    invoice.business_logo = logo_upload
                elif profile and profile.business_logo:
                    invoice.business_logo = profile.business_logo
                save_invoice_line_items(invoice, item_rows)
                invoice.invoice_text = build_invoice_text(invoice)
                invoice.save(update_fields=["business_logo", "invoice_text", "updated_at"])
                save_business_profile_from_invoice(invoice, request.user)
                save_client_from_invoice(invoice, request.user)
                audit_log(request, "invoice.created", "Invoice", invoice.id, f"Created invoice for {invoice.client_name} amount {invoice.total_text}")
                return redirect(f"/dashboard/?invoice=created#invoices")

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    preview_logo = ""
    if profile and profile.business_logo:
        preview_logo = '<img class="invoice-preview-logo" data-preview-logo src="/dashboard/business-profile/logo/" alt="Business logo preview" />'
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card invoice-builder-card">
        <div>
          <p class="eyebrow">Invoice</p>
          <h1>Create invoice</h1>
          <p class="account-copy">Choose a professional template and preview it with your saved company information before saving the invoice.</p>
          {error_html}
        </div>
        <div class="invoice-builder-layout">
          <form method="post" action="/dashboard/invoices/new/" class="account-form invoice-server-form invoice-builder-form" enctype="multipart/form-data">
            {csrf_input(request)}
            <label>Professional template<select name="template" data-preview-field>{invoice_template_options(values['template'])}</select></label>
            <label>Brand/accent color<input name="accent_color" type="color" value="{escape(values['accent_color'])}" data-preview-field /></label>
            <label>Business name<input name="business_name" value="{escape(values['business_name'])}" required data-preview-field /></label>
            <label>Business logo<input name="business_logo" type="file" accept="image/png,image/jpeg,image/webp,image/gif" /></label>
            <label>Business phone<input name="business_phone" value="{escape(values['business_phone'])}" placeholder="Optional" data-preview-field /></label>
            <label>Business full address<textarea name="business_address" rows="3" data-preview-field>{escape(values['business_address'])}</textarea></label>
            <label>Client name<input name="client_name" value="{escape(values['client_name'])}" required data-preview-field /></label>
            <label>Client phone<input name="client_phone" value="{escape(values['client_phone'])}" placeholder="Optional" data-preview-field /></label>
            <label>Client full address<textarea name="client_address" rows="3" data-preview-field>{escape(values['client_address'])}</textarea></label>
            <label>{tax_id_label}<input name="client_gstin" value="{escape(values['client_gstin'])}" placeholder="Optional" data-preview-field /></label>
            <label class="checkbox-row"><input name="include_gst" type="checkbox" {'checked' if values['include_gst'] == 'on' else ''} data-preview-field /> {include_tax_label}</label>
            <label>{tax_rate_label}<input name="gst_rate" type="number" min="0" step="0.01" value="{escape(values['gst_rate'])}" required data-preview-field /></label>
            <label>Due days<input name="due_days" type="number" min="0" step="1" value="{escape(values['due_days'])}" data-preview-field /></label>
            <input name="service_name" type="hidden" value="{escape(values['service_name'])}" />
            <input name="amount_before_gst" type="hidden" value="{escape(values['amount_before_gst'])}" />
            <div class="invoice-items-editor full-row">
              <div class="invoice-items-heading">
                <div>
                  <span>Line items</span>
                  <strong>Description, quantity and rate</strong>
                </div>
                <small>Leave unused rows blank.</small>
              </div>
              <div class="invoice-item-row invoice-item-head">
                <span>Item</span>
                <span>Qty</span>
                <span>Rate</span>
              </div>
              {invoice_item_rows_html(item_rows)}
            </div>
            <label class="full-row">{payment_link_label}<input name="upi_link" value="{escape(values['upi_link'])}" placeholder="{payment_placeholder}" data-preview-field /></label>
            <label class="full-row">Bank information<textarea name="bank_details" rows="4" placeholder="Bank name, account number, IFSC, account holder" data-preview-field>{escape(values['bank_details'])}</textarea></label>
            <label class="full-row">Thank you note<textarea name="thank_you_note" rows="3" data-preview-field>{escape(values['thank_you_note'])}</textarea></label>
            <div class="dashboard-actions">
              <button class="button primary" type="submit">Save invoice</button>
              <a class="button secondary" href="/dashboard/">Back to dashboard</a>
            </div>
          </form>
          <aside class="invoice-live-preview" aria-label="Selected invoice template preview">
            <div class="preview-toolbar">
              <span>Selected template preview</span>
              <strong data-preview-template-name>{escape(dict(Invoice.TEMPLATE_CHOICES).get(values['template'], 'Classic Ledger'))}</strong>
            </div>
            <article class="invoice-preview-document invoice-preview-{escape(values['template'])}" data-preview-document style="--preview-accent: {escape(values['accent_color'])};">
              <header class="invoice-preview-header">
                <div>
                  <div class="invoice-preview-logo-frame" data-preview-logo-frame>
                    {preview_logo or '<span data-preview-logo-placeholder>Logo preview</span>'}
                  </div>
                  <span class="invoice-preview-kicker">{escape('Tax invoice' if not us_market else 'Invoice')}</span>
                  <h2 data-preview="business_name">{escape(values['business_name'] or 'Your business')}</h2>
                  <p data-preview="business_phone">{escape(values['business_phone'] or 'Business phone')}</p>
                  <p data-preview="business_address">{escape(values['business_address'] or 'Business address').replace(chr(10), '<br />')}</p>
                </div>
                <div class="invoice-preview-number">
                  <strong>RL-PREVIEW</strong>
                  <span>Due in <b data-preview="due_days">{escape(values['due_days'] or '7')}</b> days</span>
                </div>
              </header>
              <section class="invoice-preview-addresses">
                <div><span>Bill to</span><strong data-preview="client_name">{escape(values['client_name'] or 'Client name')}</strong><p data-preview="client_phone">{escape(values['client_phone'] or 'Client phone')}</p><p data-preview="client_address">{escape(values['client_address'] or 'Client address').replace(chr(10), '<br />')}</p></div>
                <div><span>Payment</span><p data-preview="upi_link">{escape(values['upi_link'] or payment_link_label)}</p><p data-preview="bank_details">{escape(values['bank_details'] or 'Bank information').replace(chr(10), '<br />')}</p></div>
              </section>
              <div class="invoice-preview-items" data-preview-items></div>
              <section class="invoice-preview-totals">
                <div><span>Subtotal</span><strong data-preview-subtotal>{escape(money(invoice_items_subtotal(item_rows), currency_symbol))}</strong></div>
                <div><span>{escape(tax_label)}</span><strong data-preview-tax>{escape(money(invoice_items_subtotal(item_rows) * decimal_value(values['gst_rate']) / Decimal('100') if values['include_gst'] == 'on' else Decimal('0'), currency_symbol))}</strong></div>
                <div class="preview-grand-total"><span>Total</span><strong data-preview-total>{escape(invoice_total_text(invoice_items_subtotal(item_rows), decimal_value(values['gst_rate']), values['include_gst'] == 'on', currency_symbol))}</strong></div>
              </section>
              <p class="invoice-preview-note" data-preview="thank_you_note">{escape(values['thank_you_note'] or 'Thank you for your business.')}</p>
            </article>
          </aside>
        </div>
        <script>
          (() => {{
            const form = document.querySelector('.invoice-builder-form');
            const doc = document.querySelector('[data-preview-document]');
            if (!form || !doc) return;
            const labels = {{"classic": "Classic Ledger", "executive": "Executive Black", "modern": "Modern Accent", "minimal": "Minimal Clean", "service": "Service Pro"}};
            const currency = {json.dumps(currency_symbol)};
            const taxLabel = {json.dumps(tax_label)};
            const setHtml = (name, fallback) => {{
              const target = doc.querySelector(`[data-preview="${{name}}"]`);
              const field = form.elements[name];
              if (!target || !field) return;
              const value = field.value || fallback;
              target.innerHTML = value.replace(/\\n/g, '<br />');
            }};
            const totalText = () => {{
              const amount = itemSubtotal();
              const includeTax = Boolean(form.elements.include_gst?.checked);
              const rate = includeTax ? Number(form.elements.gst_rate.value || 0) : 0;
              const total = amount + (amount * rate / 100);
              return `${{currency}} ${{total.toFixed(2)}}`;
            }};
            const moneyText = (value) => `${{currency}} ${{Number(value || 0).toFixed(2)}}`;
            const escapeHtml = (value) => String(value || '').replace(/[&<>"']/g, (char) => ({{'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}}[char]));
            const itemRows = () => Array.from(form.querySelectorAll('.invoice-item-row:not(.invoice-item-head)'));
            const itemSubtotal = () => itemRows().reduce((sum, row) => {{
              const quantity = Number(row.querySelector('[name="item_quantity"]')?.value || 0);
              const rate = Number(row.querySelector('[name="item_rate"]')?.value || 0);
              return sum + (quantity * rate);
            }}, 0);
            const updateItems = () => {{
              const target = doc.querySelector('[data-preview-items]');
              if (!target) return;
              const rows = itemRows().map((row) => {{
                const description = row.querySelector('[name="item_description"]')?.value || '';
                const quantity = Number(row.querySelector('[name="item_quantity"]')?.value || 0);
                const rate = Number(row.querySelector('[name="item_rate"]')?.value || 0);
                const amount = quantity * rate;
                return {{ description, quantity, rate, amount }};
              }}).filter((row) => row.description || row.amount > 0);
              target.innerHTML = `
                <div class="invoice-preview-item invoice-preview-item-head"><span>Description</span><span>Qty</span><span>Rate</span><span>Amount</span></div>
                ${{(rows.length ? rows : [{{description: 'Service description', quantity: 1, rate: 0, amount: 0}}]).map((row) => `
                  <div class="invoice-preview-item">
                    <strong>${{escapeHtml(row.description || 'Item')}}</strong>
                    <span>${{row.quantity || 1}}</span>
                    <span>${{moneyText(row.rate)}}</span>
                    <span>${{moneyText(row.amount)}}</span>
                  </div>
                `).join('')}}
              `;
            }};
            const update = () => {{
              const template = form.elements.template.value || 'classic';
              const accent = form.elements.accent_color.value || '#126b4f';
              doc.className = `invoice-preview-document invoice-preview-${{template}}`;
              doc.style.setProperty('--preview-accent', accent);
              const templateName = document.querySelector('[data-preview-template-name]');
              if (templateName) templateName.textContent = labels[template] || 'Classic Ledger';
              setHtml('business_name', 'Your business');
              setHtml('business_phone', 'Business phone');
              setHtml('business_address', 'Business address');
              setHtml('client_name', 'Client name');
              setHtml('client_phone', 'Client phone');
              setHtml('client_address', 'Client address');
              setHtml('upi_link', {json.dumps(payment_link_label)});
              setHtml('bank_details', 'Bank information');
              setHtml('thank_you_note', 'Thank you for your business.');
              setHtml('due_days', '7');
              updateItems();
              const subtotal = itemSubtotal();
              const includeTax = Boolean(form.elements.include_gst?.checked);
              const rate = includeTax ? Number(form.elements.gst_rate.value || 0) : 0;
              const tax = subtotal * rate / 100;
              const subtotalEl = doc.querySelector('[data-preview-subtotal]');
              const taxEl = doc.querySelector('[data-preview-tax]');
              const total = doc.querySelector('[data-preview-total]');
              if (subtotalEl) subtotalEl.textContent = moneyText(subtotal);
              if (taxEl) taxEl.textContent = includeTax ? moneyText(tax) : 'Not charged';
              if (total) total.textContent = totalText();
            }};
            const logoInput = form.elements.business_logo;
            const logoFrame = document.querySelector('[data-preview-logo-frame]');
            if (logoInput && logoFrame) {{
              logoInput.addEventListener('change', () => {{
                const file = logoInput.files && logoInput.files[0];
                if (!file || !file.type.startsWith('image/')) return;
                const reader = new FileReader();
                reader.onload = () => {{
                  logoFrame.innerHTML = `<img class="invoice-preview-logo" data-preview-logo src="${{reader.result}}" alt="Business logo preview" />`;
                }};
                reader.readAsDataURL(file);
              }});
            }}
            form.querySelectorAll('[data-preview-field]').forEach((field) => {{
              field.addEventListener('input', update);
              field.addEventListener('change', update);
            }});
            update();
          }})();
        </script>
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
    us_invoice = invoice_is_us(invoice)
    tax_id_label = "Client tax ID" if us_invoice else "Client GSTIN"
    amount_label = "Amount before tax" if us_invoice else "Amount before GST"
    tax_rate_label = "Sales tax rate" if us_invoice else "GST rate"
    include_tax_label = "Add sales tax to this invoice" if us_invoice else "Include GST on this invoice"
    payment_link_label = "Payment link" if us_invoice else "UPI/payment link"
    if request.method == "POST":
        item_rows = parse_invoice_line_items(request.POST)
        if not item_rows:
            item_rows = [{"description": clean_text(request.POST.get("service_name"), invoice.service_name, 240), "quantity": Decimal("1"), "rate": decimal_value(request.POST.get("amount_before_gst"))}]
        amount_before_gst = invoice_items_subtotal(item_rows)
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
            invoice.business_phone = clean_text(request.POST.get("business_phone"), max_length=40)
            invoice.business_address = clean_text(request.POST.get("business_address"))
            invoice.client_name = clean_text(request.POST.get("client_name"), invoice.client_name, 180)
            invoice.client_phone = clean_text(request.POST.get("client_phone"), max_length=40)
            invoice.client_address = clean_text(request.POST.get("client_address"))
            invoice.client_gstin = clean_text(request.POST.get("client_gstin"), max_length=20).upper()
            invoice.service_name = clean_text(item_rows[0]["description"], invoice.service_name, 240)
            invoice.include_gst = include_gst
            invoice.amount_before_gst = amount_before_gst
            invoice.gst_rate = gst_rate
            invoice.tax_label = clean_tax_label(request.POST.get("tax_label") or invoice.tax_label)
            invoice.currency_symbol = clean_currency_symbol(request.POST.get("currency_symbol") or invoice.currency_symbol)
            status = clean_text(request.POST.get("status"), invoice.status, 20)
            invoice.status = status if status in dict(Invoice.STATUS_CHOICES) else invoice.status
            invoice.total_text = clean_text(request.POST.get("total_text"), invoice.total_text, 80)
            if not invoice.total_text:
                invoice.total_text = invoice_total_text(amount_before_gst, gst_rate, include_gst, invoice.currency_symbol)
            invoice.upi_link = clean_text(request.POST.get("upi_link"))
            invoice.bank_details = clean_text(request.POST.get("bank_details"))
            invoice.thank_you_note = clean_text(request.POST.get("thank_you_note"))
            if logo_upload:
                invoice.business_logo = logo_upload
            invoice.save()
            save_invoice_line_items(invoice, item_rows)
            invoice.invoice_text = build_invoice_text(invoice)
            invoice.save(update_fields=["invoice_text", "updated_at"])
            save_business_profile_from_invoice(invoice, request.user)
            save_client_from_invoice(invoice, request.user)
            audit_log(request, "invoice.updated", "Invoice", invoice.id, f"Updated invoice for {invoice.client_name}")
            return redirect("/dashboard/")

    status_options = "".join(
        f'<option value="{value}" {"selected" if invoice.status == value else ""}>{label}</option>'
        for value, label in Invoice.STATUS_CHOICES
    )
    edit_item_rows = [{"description": item.description, "quantity": item.quantity, "rate": item.rate} for item in invoice_items(invoice)]
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
          <label>Business phone<input name="business_phone" value="{escape(invoice.business_phone)}" placeholder="Optional" /></label>
          <label>Business full address<textarea name="business_address" rows="3">{escape(invoice.business_address)}</textarea></label>
          <label>Client name<input name="client_name" value="{escape(invoice.client_name)}" /></label>
          <label>Client phone<input name="client_phone" value="{escape(invoice.client_phone)}" placeholder="Optional" /></label>
          <label>Client full address<textarea name="client_address" rows="3">{escape(invoice.client_address)}</textarea></label>
          <label>{tax_id_label}<input name="client_gstin" value="{escape(invoice.client_gstin)}" /></label>
          <label class="checkbox-row"><input name="include_gst" type="checkbox" {'checked' if invoice.include_gst else ''} /> {include_tax_label}</label>
          <label>{tax_rate_label}<input name="gst_rate" type="number" min="0" step="0.01" value="{invoice.gst_rate}" /></label>
          <input name="service_name" type="hidden" value="{escape(invoice.service_name)}" />
          <input name="amount_before_gst" type="hidden" value="{invoice_subtotal(invoice)}" />
          <div class="invoice-items-editor full-row">
            <div class="invoice-items-heading">
              <div>
                <span>Line items</span>
                <strong>Description, quantity and rate</strong>
              </div>
              <small>Leave unused rows blank.</small>
            </div>
            <div class="invoice-item-row invoice-item-head">
              <span>Item</span>
              <span>Qty</span>
              <span>Rate</span>
            </div>
            {invoice_item_rows_html(edit_item_rows)}
          </div>
          <label>Total text<input name="total_text" value="{escape(invoice.total_text)}" /></label>
          <label>Status<select name="status">{status_options}</select></label>
          <label>{payment_link_label}<input name="upi_link" value="{escape(invoice.upi_link)}" /></label>
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
        audit_log(request, "invoice.status_changed", "Invoice", invoice.id, f"Changed invoice status to {status}")
    return redirect("/dashboard/")


@login_required
@require_POST
def invoice_delete(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    audit_log(request, "invoice.deleted", "Invoice", invoice.id, f"Deleted invoice for {invoice.client_name}")
    invoice.delete()
    return redirect("/dashboard/")


@login_required
@require_http_methods(["GET", "POST"])
def pro_billing(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        return request_pro_activation(request)

    email = current_account_email(request)
    subscription = get_subscription(request)
    title, message, tone = subscription_status_copy(subscription)
    market = current_market(request)
    payment_gateway = active_payment_gateway_for_market(market)
    gateway_name = gateway_name_for_market(market)
    paid_price = market_price(request)
    gateway_message = (
        f"{payment_gateway.get_gateway_display()} {payment_gateway.get_mode_display()} mode is configured, but manual admin approval is still kept as a control step."
        if payment_gateway
        else f"{gateway_name} checkout is not enabled yet. This request will be reviewed and approved manually from Django admin."
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
        <p class="account-copy">Free accounts can save {FREE_MONTHLY_INVOICE_LIMIT} invoices per month. Paid access is {escape(paid_price)} and allows up to {PAID_MONTHLY_INVOICE_LIMIT} invoices per month. Payment checkout can be attached after gateway approval.</p>
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
    market = current_market(request)
    payment_gateway = active_payment_gateway_for_market(market)
    subscription = get_subscription(request)
    subscription.plan = "pro"
    subscription.status = "requested"
    subscription.requested_at = timezone.now()
    subscription.paused_at = None
    subscription.cancelled_at = None
    subscription.save(update_fields=["plan", "status", "requested_at", "paused_at", "cancelled_at", "updated_at"])
    audit_log(request, "subscription.requested", "PlanSubscription", subscription.id, f"Requested {subscription.plan} activation")
    send_mail(
        "RozLedger Pro activation requested",
        "\n".join(
            [
                f"Pro activation requested by {email}.",
                f"Market: {subscription.get_market_display()}",
                f"Payment gateway: {payment_gateway.get_gateway_display() + ' enabled' if payment_gateway else 'disabled'}",
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
    tax_label = invoice_tax_label(invoice)
    total_text = invoice_total_display(invoice)
    us_invoice = invoice_is_us(invoice)
    invoice_title = "Invoice" if us_invoice else "Tax invoice"
    payment_link_label = "Payment link" if us_invoice else "UPI / payment link"
    subtotal = invoice_subtotal(invoice)
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
            <p class="invoice-kicker">{invoice_title}</p>
            <h1>{escape(invoice.business_name)}</h1>
            {f'<p class="invoice-contact-line">Phone: {escape(invoice.business_phone)}</p>' if invoice.business_phone else ''}
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
          {f'<p class="invoice-contact-line">Phone: {escape(invoice.business_phone)}</p>' if invoice.business_phone else ''}
          <p>{escape(invoice.business_address).replace(chr(10), '<br />')}</p>
        </article>
        <article>
          <span>Bill to</span>
          <strong>{escape(invoice.client_name)}</strong>
          {f'<p class="invoice-contact-line">Phone: {escape(invoice.client_phone)}</p>' if invoice.client_phone else ''}
          <p>{escape(invoice.client_address).replace(chr(10), '<br />')}</p>
          {f'<p>GSTIN: {escape(invoice.client_gstin)}</p>' if invoice.client_gstin and not us_invoice else ''}
        </article>
      </section>
      <table class="invoice-line-table">
        <thead>
          <tr>
            <th>Description</th>
            <th>Qty</th>
            <th>Rate</th>
            <th>Amount</th>
          </tr>
        </thead>
        <tbody>
          {invoice_print_rows(invoice)}
        </tbody>
      </table>
      <section class="invoice-total-panel">
        <div>
          <span>Subtotal</span>
          <strong>{escape(invoice_money(invoice, subtotal))}</strong>
        </div>
        <div>
          <span>{f'{tax_label} @ {invoice.gst_rate}%' if invoice.include_gst else tax_label}</span>
          <strong>{escape(invoice_money(invoice, gst_amount)) if invoice.include_gst else 'Not charged'}</strong>
        </div>
        <div class="grand-total">
          <span>Amount payable</span>
          <strong>{escape(total_text)}</strong>
        </div>
      </section>
      <section class="invoice-payment-grid">
        {f'<article><span>Bank information</span><p>{escape(invoice.bank_details).replace(chr(10), "<br />")}</p></article>' if invoice.bank_details else ''}
        {f'<article><span>{payment_link_label}</span><p>{escape(invoice.upi_link)}</p></article>' if invoice.upi_link else ''}
      </section>
      {f'<p class="thank-you-note">{escape(invoice.thank_you_note)}</p>' if invoice.thank_you_note else ''}
      <section class="print-actions no-print">
        <button class="button primary" type="button" onclick="window.print()">Print / Save PDF</button>
        {f'<a class="button secondary" href="{escape(invoice.upi_link)}">Open payment link</a>' if invoice.upi_link else ''}
        <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/download.pdf">Download PDF</a>
        <a class="button secondary" href="{escape(whatsapp_url(invoice.invoice_text))}" rel="noopener">Send on WhatsApp</a>
        <a class="button ghost" href="/dashboard/">Dashboard</a>
      </section>
      <p class="print-disclaimer">Generated by RozLedger - {escape(invoice_brand_url(invoice))}. Verify tax and legal details with a qualified professional.</p>
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
    response["Content-Type"] = image_content_type(invoice.business_logo.name)
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
    us_invoice = invoice_is_us(invoice)
    subtotal = invoice_subtotal(invoice)
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

    story = [
        Table([[""]], colWidths=[480], rowHeights=[5], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)])),
        Spacer(1, 18),
    ]
    header_items = [Paragraph("INVOICE" if us_invoice else "TAX INVOICE", title_style), Paragraph(escape(invoice.business_name), heading_style)]
    if invoice.business_phone:
        header_items.append(para(f"Phone: {invoice.business_phone}", small_style))
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
                    para("\n".join(part for part in [invoice.client_name, f"Phone: {invoice.client_phone}" if invoice.client_phone else "", invoice.client_address, f"GSTIN: {invoice.client_gstin}" if invoice.client_gstin and not us_invoice else ""] if part)),
                    para("\n".join(part for part in [invoice.business_name, f"Phone: {invoice.business_phone}" if invoice.business_phone else "", invoice.business_address] if part)),
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
    line_table_rows = [[Paragraph("Description", label_style), Paragraph("Qty", label_style), Paragraph("Rate", label_style), Paragraph("Amount", label_style)]]
    for item in invoice_items(invoice):
        line_table_rows.append(
            [
                para(item.description),
                para(format_quantity(item.quantity), amount_style),
                para(invoice_money(invoice, item.rate), amount_style),
                para(invoice_money(invoice, item.amount), amount_bold_style),
            ]
        )
    story.append(
        Table(
            line_table_rows,
            colWidths=[250, 58, 82, 90],
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
                [para("Subtotal", right_style), para(invoice_money(invoice, subtotal), amount_bold_style)],
                [para(f"{invoice_tax_label(invoice)} @ {invoice.gst_rate}%" if invoice.include_gst else invoice_tax_label(invoice), right_style), para(invoice_money(invoice, gst_amount) if invoice.include_gst else "Not charged", amount_bold_style)],
                [para("Amount payable", right_bold_style), para(invoice_total_display(invoice), amount_bold_style)],
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
    story.append(Paragraph(f"Invoice generated by RozLedger - {invoice_brand_url(invoice)}", small_style))
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
    market = current_market(request)
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
    if email and Lead.objects.filter(email__iexact=email, market=market, created_at__gte=timezone.now() - timedelta(hours=24)).exists():
        errors["email"] = "A request for this email is already saved."
    if phone_digits and Lead.objects.filter(phone_digits=phone_digits, market=market, created_at__gte=timezone.now() - timedelta(hours=24)).exists():
        errors["phone"] = "A request for this phone number is already saved."
    if errors:
        return None, errors

    lead = Lead.objects.create(
        market=market,
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
    item_rows = parse_invoice_line_items(payload.get("items") if isinstance(payload.get("items"), list) else [])
    if not item_rows:
        item_rows = [{"description": clean_text(payload.get("service_name"), "Service", 240), "quantity": Decimal("1"), "rate": decimal_value(payload.get("amount_before_gst"))}]
    amount_before_gst = invoice_items_subtotal(item_rows)
    include_gst = bool(payload.get("include_gst", True))
    gst_rate = decimal_value(payload.get("gst_rate")) if include_gst else Decimal("0")
    owner_email = clean_text(payload.get("owner_email"), max_length=254).lower()
    if request.user.is_authenticated and request.user.email:
        owner_email = request.user.email.lower()
    if not owner_email:
        return JsonResponse({"error": "Enter your email or create an account to save invoices."}, status=400)
    if "@" not in owner_email:
        return JsonResponse({"error": "Enter a valid email to save this invoice."}, status=400)

    if amount_before_gst <= 0:
        return JsonResponse({"error": "Invoice item total must be greater than zero."}, status=400)

    market = current_market(request)
    quota_used, quota_limit, quota_plan = invoice_quota_for_email(owner_email, market)
    if quota_used >= quota_limit:
        return JsonResponse(
            {
                "error": invoice_quota_message(quota_used, quota_limit, quota_plan),
                "quota": {"used": quota_used, "limit": quota_limit, "plan": quota_plan},
            },
            status=403,
        )

    invoice = Invoice.objects.create(
        market=market,
        owner=request.user if request.user.is_authenticated else None,
        owner_email=owner_email,
        template=clean_invoice_template(payload.get("template")),
        accent_color=clean_accent_color(payload.get("accent_color")),
        business_name=clean_text(payload.get("business_name"), "Your business", 180),
        business_phone=clean_text(payload.get("business_phone"), max_length=40),
        business_address=clean_text(payload.get("business_address")),
        client_name=clean_text(payload.get("client_name"), "Client", 180),
        client_phone=clean_text(payload.get("client_phone"), max_length=40),
        client_address=clean_text(payload.get("client_address")),
        client_gstin=clean_text(payload.get("client_gstin"), max_length=20).upper(),
        service_name=clean_text(item_rows[0]["description"], "Service", 240),
        include_gst=include_gst,
        amount_before_gst=amount_before_gst,
        gst_rate=gst_rate,
        tax_label=clean_tax_label(payload.get("tax_label")),
        currency_symbol=clean_currency_symbol(payload.get("currency_symbol")),
        due_days=max(int(payload.get("due_days") or 0), 0),
        total_text=clean_text(payload.get("total_text"), invoice_total_text(amount_before_gst, gst_rate, include_gst), 80),
        upi_link=clean_text(payload.get("upi_link")),
        bank_details=clean_text(payload.get("bank_details")),
        thank_you_note=clean_text(payload.get("thank_you_note"), "Thank you for your business."),
        invoice_text=clean_text(payload.get("invoice_text")),
    )
    save_invoice_line_items(invoice, item_rows)
    if not invoice.invoice_text:
        invoice.invoice_text = build_invoice_text(invoice)
        invoice.save(update_fields=["invoice_text", "updated_at"])
    if owner_email:
        save_business_profile_from_invoice(invoice, request.user if request.user.is_authenticated else None)
        save_client_from_invoice(invoice, request.user if request.user.is_authenticated else None)

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
