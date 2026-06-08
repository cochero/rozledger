from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta
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
from django.db import connection, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import Account, AffiliateClick, AuditLog, BusinessProfile, Client, CustomerCreditNote, ExpenseUploadDraft, Godown, GstnApiConfig, InventoryItem, Invoice, InvoiceLineItem, JournalEntry, JournalLine, Lead, PaymentEvent, PaymentGatewayConfig, PaymentReceipt, PaymentReversal, PlanSubscription, ReconciliationLine, ReconciliationSession, StockCostLayer, StockGroup, StockLayerConsumption, StockMovement, UnitOfMeasure, VendorBill, VendorBillPayment, VendorDebitNote, Voucher, VoucherInventoryLine, VoucherLedgerLine
from . import razorpay_client
from . import gstn_client


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
    ("1210", "Inventory asset", "asset", "debit"),
    ("2000", "Accounts payable", "liability", "credit"),
    ("2100", "Sales tax / GST payable", "liability", "credit"),
    ("2110", "CGST payable", "liability", "credit"),
    ("2120", "SGST payable", "liability", "credit"),
    ("2130", "IGST payable", "liability", "credit"),
    ("3000", "Owner equity", "equity", "credit"),
    ("4000", "Service income", "revenue", "credit"),
    ("4100", "Other income", "revenue", "credit"),
    ("4120", "Product sales", "revenue", "credit"),
    ("5000", "Cost of services", "expense", "debit"),
    ("5010", "Cost of goods sold", "expense", "debit"),
    ("5100", "Office expenses", "expense", "debit"),
    ("5200", "Marketing expenses", "expense", "debit"),
    ("5300", "Travel expenses", "expense", "debit"),
    ("5400", "Software subscriptions", "expense", "debit"),
]

BUSINESS_TYPE_PRESETS = {
    "service": {
        "label": "Service business",
        "summary": "Best for freelancers, agencies, repairs, contractors and local service teams.",
        "requirements": ["Business profile", "Client records", "Service invoice templates", "Payment tracking", "Expense categories", "Profit & loss"],
        "sales": ["Consulting", "Installation", "Repair", "Maintenance", "Retainer", "Project milestone"],
        "inventory": ["Optional consumables", "Tools/equipment tracking"],
        "accounts": [("4110", "Service retainers", "revenue", "credit"), ("5500", "Subcontractor costs", "expense", "debit")],
    },
    "trading": {
        "label": "Trading / retail",
        "summary": "For shops, distributors, ecommerce sellers and wholesale businesses.",
        "requirements": ["Product catalog", "Purchase bills", "Sales invoices", "Stock inward/outward", "Low stock alerts", "Gross margin review"],
        "sales": ["Product sale", "Wholesale order", "Delivery charge", "Discount", "Return"],
        "inventory": ["Trading goods", "Opening stock", "Purchase receipts", "Sales issue", "Stock adjustment"],
        "accounts": [("1210", "Inventory asset", "asset", "debit"), ("5010", "Cost of goods sold", "expense", "debit"), ("4120", "Product sales", "revenue", "credit")],
    },
    "manufacturing": {
        "label": "Manufacturing",
        "summary": "For businesses that buy raw material and produce finished goods.",
        "requirements": ["Raw material master", "Finished goods master", "Production stock movement", "Vendor bills", "Cost tracking", "Inventory valuation"],
        "sales": ["Finished goods sale", "Job work", "Scrap sale", "Freight recovery"],
        "inventory": ["Raw materials", "Work in progress", "Finished goods", "Production issue", "Production receipt"],
        "accounts": [("1220", "Raw material inventory", "asset", "debit"), ("1230", "Finished goods inventory", "asset", "debit"), ("5020", "Manufacturing cost", "expense", "debit")],
    },
    "travel": {
        "label": "Travel & tour operator",
        "summary": "For package tours, ticketing, hotel bookings, transport and travel agencies.",
        "requirements": ["Package/service catalog", "Customer advance tracking", "Supplier payable tracking", "Itinerary notes", "Commission income", "Payment reminders"],
        "sales": ["Tour package", "Ticketing fee", "Hotel booking", "Transport", "Visa assistance", "Commission"],
        "inventory": ["Packages as non-stock items", "Vendor commitments", "Optional ticket inventory"],
        "accounts": [("2110", "Customer advances", "liability", "credit"), ("4130", "Travel package income", "revenue", "credit"), ("5030", "Supplier travel costs", "expense", "debit")],
    },
    "professional": {
        "label": "Professional firm",
        "summary": "For accountants, consultants, lawyers, architects and other professional practices.",
        "requirements": ["Client records", "Matter/project billing", "Recurring retainers", "Expense recovery", "Receivables ageing", "Tax-ready records"],
        "sales": ["Professional fee", "Retainer", "Filing charge", "Reimbursement", "Advisory package"],
        "inventory": ["Non-stock service catalog"],
        "accounts": [("4140", "Professional fees", "revenue", "credit"), ("5510", "Client reimbursable expenses", "expense", "debit")],
    },
    "construction": {
        "label": "Construction / contractor",
        "summary": "For contractors, site work, interior projects and milestone billing.",
        "requirements": ["Project/customer records", "Milestone invoices", "Material purchase tracking", "Vendor bills", "Retention notes", "Cash summary"],
        "sales": ["Milestone billing", "Labour charge", "Material recovery", "Site supervision"],
        "inventory": ["Construction materials", "Site consumables", "Tools"],
        "accounts": [("1240", "Site materials", "asset", "debit"), ("4150", "Contract income", "revenue", "credit"), ("5040", "Site labour and material cost", "expense", "debit")],
    },
    "restaurant": {
        "label": "Restaurant / food service",
        "summary": "For cafes, restaurants, catering and cloud kitchens.",
        "requirements": ["Menu/service items", "Ingredient stock", "Vendor purchases", "Daily sales", "Expense tracking", "Cash/bank summary"],
        "sales": ["Food sales", "Catering order", "Delivery charge", "Event package"],
        "inventory": ["Ingredients", "Packaging", "Finished/menu items", "Wastage adjustment"],
        "accounts": [("1250", "Food inventory", "asset", "debit"), ("4160", "Food sales", "revenue", "credit"), ("5050", "Food cost", "expense", "debit")],
    },
    "education": {
        "label": "Education / coaching",
        "summary": "For tutors, coaching centers, training providers and workshops.",
        "requirements": ["Student/customer records", "Course fee invoices", "Batch/service catalog", "Payment reminders", "Expense tracking"],
        "sales": ["Course fee", "Monthly tuition", "Workshop", "Study material"],
        "inventory": ["Books/materials", "Non-stock courses"],
        "accounts": [("4170", "Course fee income", "revenue", "credit"), ("5520", "Teaching material expense", "expense", "debit")],
    },
    "healthcare": {
        "label": "Healthcare / clinic",
        "summary": "For clinics, wellness centers and healthcare service providers.",
        "requirements": ["Patient/customer records", "Service invoice", "Consumable inventory", "Expense tracking", "Receipt records"],
        "sales": ["Consultation", "Procedure", "Medicine/consumable", "Package"],
        "inventory": ["Consumables", "Medicines", "Clinic supplies"],
        "accounts": [("4180", "Consultation income", "revenue", "credit"), ("5060", "Medical consumables cost", "expense", "debit")],
    },
    "other": {
        "label": "Other business",
        "summary": "A balanced starter setup for businesses that need invoices, expenses, accounts and optional inventory.",
        "requirements": ["Business profile", "Client/vendor records", "Invoices", "Payments", "Expenses", "Reports"],
        "sales": ["Service", "Product", "Project", "Package"],
        "inventory": ["Optional stock items"],
        "accounts": [],
    },
}


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


def business_type_options(selected: str = "service") -> str:
    return "".join(
        f'<option value="{escape(key)}" {"selected" if selected == key else ""}>{escape(preset["label"])}</option>'
        for key, preset in BUSINESS_TYPE_PRESETS.items()
    )


def business_type_preset(key: str) -> dict[str, Any]:
    return BUSINESS_TYPE_PRESETS.get(key) or BUSINESS_TYPE_PRESETS["other"]


def apply_business_type_accounts(request: HttpRequest, business_type: str) -> int:
    ensure_default_chart(request)
    preset = business_type_preset(business_type)
    email = current_account_email(request)
    market = current_market(request)
    existing_codes = set(Account.objects.filter(account_q(request)).values_list("code", flat=True))
    accounts = []
    for code, name, account_type, normal_balance in preset["accounts"]:
        if code in existing_codes:
            continue
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
    return len(accounts)


def ensure_default_inventory_masters(request: HttpRequest) -> tuple[UnitOfMeasure, Godown, StockGroup]:
    email = current_account_email(request)
    market = current_market(request)
    unit, _ = UnitOfMeasure.objects.get_or_create(
        market=market,
        owner_email=email,
        symbol="pcs",
        defaults={"owner": request.user, "name": "Pieces"},
    )
    godown, _ = Godown.objects.get_or_create(
        market=market,
        owner_email=email,
        name="Main location",
        defaults={"owner": request.user, "address": ""},
    )
    stock_group, _ = StockGroup.objects.get_or_create(
        market=market,
        owner_email=email,
        name="Primary",
        defaults={"owner": request.user},
    )
    return unit, godown, stock_group


def next_voucher_number(request: HttpRequest, voucher_type: str) -> str:
    prefix = {
        "sales": "SAL",
        "purchase": "PUR",
        "expense": "EXP",
        "receipt": "RCT",
        "credit_note": "CRN",
        "debit_note": "DBN",
        "reversal": "REV",
        "payment": "PAY",
        "contra": "CON",
        "journal": "JRN",
        "stock_journal": "STK",
    }.get(voucher_type, "VCH")
    count = Voucher.objects.filter(account_q(request), voucher_type=voucher_type).count() + 1
    return f"{prefix}-{timezone.localdate():%Y%m}-{count:05d}"


def next_credit_note_number(request: HttpRequest) -> str:
    count = CustomerCreditNote.objects.filter(account_q(request)).count() + 1
    return f"CN-{timezone.localdate():%Y%m}-{count:05d}"


def next_debit_note_number(request: HttpRequest) -> str:
    count = VendorDebitNote.objects.filter(account_q(request)).count() + 1
    return f"DN-{timezone.localdate():%Y%m}-{count:05d}"


def next_reversal_number(request: HttpRequest) -> str:
    count = PaymentReversal.objects.filter(account_q(request)).count() + 1
    return f"RV-{timezone.localdate():%Y%m}-{count:05d}"


def parse_form_date(value: str):
    value = clean_text(value, max_length=20)
    if not value:
        return timezone.localdate()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return timezone.localdate()


def create_voucher_with_lines(
    request: HttpRequest,
    *,
    voucher_type: str,
    party_name: str,
    narration: str,
    ledger_lines: list[dict[str, Any]],
    inventory_lines: list[dict[str, Any]] | None = None,
    voucher_date=None,
) -> Voucher:
    with transaction.atomic():
        voucher_date = voucher_date or timezone.localdate()
        total_debit = sum((decimal_value(line.get("debit")) for line in ledger_lines), Decimal("0")).quantize(Decimal("0.01"))
        total_credit = sum((decimal_value(line.get("credit")) for line in ledger_lines), Decimal("0")).quantize(Decimal("0.01"))
        if total_debit != total_credit:
            raise ValueError("Voucher must balance before posting.")
        voucher = Voucher.objects.create(
            market=current_market(request),
            owner=request.user,
            owner_email=current_account_email(request),
            voucher_type=voucher_type,
            voucher_number=next_voucher_number(request, voucher_type),
            voucher_date=voucher_date,
            party_name=party_name,
            narration=narration,
            total_amount=max(total_debit, total_credit),
        )
        journal = JournalEntry.objects.create(
            market=voucher.market,
            owner=request.user,
            owner_email=voucher.owner_email,
            entry_date=voucher_date,
            memo=f"{voucher.get_voucher_type_display()} {voucher.voucher_number} - {party_name}",
            source=f"voucher_{voucher_type}",
            total_debit=total_debit,
            total_credit=total_credit,
        )
        for line in ledger_lines:
            account = line["account"]
            description = clean_text(line.get("description"), max_length=240)
            debit = decimal_value(line.get("debit"))
            credit = decimal_value(line.get("credit"))
            JournalLine.objects.create(entry=journal, account=account, description=description, debit=debit, credit=credit)
            VoucherLedgerLine.objects.create(voucher=voucher, account=account, description=description, debit=debit, credit=credit)
        voucher.journal_entry = journal
        voucher.save(update_fields=["journal_entry"])
        for line in inventory_lines or []:
            post_voucher_inventory_line(request, voucher, line)
        return voucher


def post_voucher_inventory_line(request: HttpRequest, voucher: Voucher, line: dict[str, Any]) -> VoucherInventoryLine:
    item = line["item"]
    godown = line.get("godown")
    quantity = decimal_value(line.get("quantity"))
    rate = decimal_value(line.get("rate"))
    amount = (quantity * rate).quantize(Decimal("0.01"))
    movement_type = "purchase" if voucher.voucher_type == "purchase" else "sale" if voucher.voucher_type == "sales" else "adjustment"
    movement = StockMovement.objects.create(
        market=voucher.market,
        owner=request.user,
        owner_email=voucher.owner_email,
        item=item,
        godown=godown,
        movement_type=movement_type,
        movement_date=voucher.voucher_date,
        quantity=quantity,
        unit_cost=rate,
        reference=voucher.voucher_number,
        notes=voucher.narration,
    )
    voucher_line = VoucherInventoryLine.objects.create(
        voucher=voucher,
        item=item,
        godown=godown,
        description=clean_text(line.get("description"), item.name, 240),
        quantity=quantity,
        rate=rate,
        amount=amount,
        stock_movement=movement,
    )
    if voucher.voucher_type == "purchase":
        StockCostLayer.objects.create(
            market=voucher.market,
            owner_email=voucher.owner_email,
            item=item,
            godown=godown,
            source_line=voucher_line,
            source_movement=movement,
            layer_date=voucher.voucher_date,
            original_quantity=quantity,
            remaining_quantity=quantity,
            unit_cost=rate,
        )
    elif voucher.voucher_type == "sales":
        fifo_cost = consume_fifo_layers(voucher_line)
        if fifo_cost > 0 and voucher.journal_entry:
            cogs_account = account_by_code(request, "5010")
            inventory_account = account_by_code(request, "1210")
            description = f"FIFO cost for {item.name}"
            JournalLine.objects.create(entry=voucher.journal_entry, account=cogs_account, description=description, debit=fifo_cost, credit=Decimal("0"))
            JournalLine.objects.create(entry=voucher.journal_entry, account=inventory_account, description=description, debit=Decimal("0"), credit=fifo_cost)
            VoucherLedgerLine.objects.create(voucher=voucher, account=cogs_account, description=description, debit=fifo_cost, credit=Decimal("0"))
            VoucherLedgerLine.objects.create(voucher=voucher, account=inventory_account, description=description, debit=Decimal("0"), credit=fifo_cost)
            voucher.journal_entry.total_debit = (voucher.journal_entry.total_debit + fifo_cost).quantize(Decimal("0.01"))
            voucher.journal_entry.total_credit = (voucher.journal_entry.total_credit + fifo_cost).quantize(Decimal("0.01"))
            voucher.journal_entry.save(update_fields=["total_debit", "total_credit"])
    return voucher_line


def consume_fifo_layers(sale_line: VoucherInventoryLine) -> Decimal:
    required = sale_line.quantity
    total_cost = Decimal("0")
    layers = StockCostLayer.objects.filter(
        market=sale_line.voucher.market,
        owner_email=sale_line.voucher.owner_email,
        item=sale_line.item,
        remaining_quantity__gt=0,
    ).order_by("layer_date", "created_at", "id")
    if sale_line.godown_id:
        layers = layers.filter(godown=sale_line.godown)
    for layer in layers:
        if required <= 0:
            break
        take = min(required, layer.remaining_quantity)
        amount = (take * layer.unit_cost).quantize(Decimal("0.01"))
        StockLayerConsumption.objects.create(sale_line=sale_line, layer=layer, quantity=take, unit_cost=layer.unit_cost, amount=amount)
        layer.remaining_quantity = (layer.remaining_quantity - take).quantize(Decimal("0.01"))
        layer.save(update_fields=["remaining_quantity"])
        total_cost += amount
        required -= take
    if required > 0:
        raise ValueError(f"Insufficient FIFO stock for {sale_line.item.name}. Short by {required}.")
    return total_cost.quantize(Decimal("0.01"))


def fifo_stock_value(item: InventoryItem) -> Decimal:
    return sum((layer.remaining_value for layer in item.cost_layers.all()), Decimal("0")).quantize(Decimal("0.01"))


def account_options(accounts, selected_id: str = "") -> str:
    return "".join(
        f'<option value="{account.id}" {"selected" if selected_id and str(account.id) == str(selected_id) else ""}>{escape(account.code)} - {escape(account.name)}</option>'
        for account in accounts
    )


def account_options_with_blank(accounts, selected_id: str = "", label: str = "Select ledger") -> str:
    return f'<option value="">{escape(label)}</option>' + account_options(accounts, selected_id)


def invoice_options(invoices, selected_id: str = "") -> str:
    options = ['<option value="">Select open invoice or leave blank for direct receipt</option>']
    for invoice in invoices:
        number = invoice_number(invoice)
        total = invoice_total_display(invoice)
        balance = invoice_outstanding_amount(invoice)
        label = f"{number} - {invoice.client_name} - balance {invoice.currency_symbol} {balance} / total {total} - {invoice.created_at:%d %b %Y}"
        options.append(
            f'<option value="{invoice.id}" data-client="{escape(invoice.client_name)}" data-amount="{balance}" {"selected" if selected_id and str(invoice.id) == str(selected_id) else ""}>{escape(label)}</option>'
        )
    return "".join(options)


def vendor_bill_options(bills, selected_id: str = "") -> str:
    options = ['<option value="">Select unpaid vendor bill</option>']
    for bill in bills:
        due = bill.due_date or bill.bill_date
        balance = vendor_bill_outstanding_amount(bill)
        label = f"{bill.vendor_name} - {bill.reference or 'No reference'} - balance {balance} / total {bill.amount} - due {due:%d %b %Y}"
        options.append(
            f'<option value="{bill.id}" data-vendor="{escape(bill.vendor_name)}" data-amount="{balance}" {"selected" if selected_id and str(bill.id) == str(selected_id) else ""}>{escape(label)}</option>'
        )
    return "".join(options)


def journal_lines_table(entry: JournalEntry | None, currency: str) -> str:
    if not entry:
        return '<tr><td colspan="5" class="empty-report-row">No journal entry linked.</td></tr>'
    rows = []
    for line in entry.lines.select_related("account").all():
        rows.append(
            f"""
            <tr>
              <td>{escape(line.account.code)}</td>
              <td>{escape(line.account.name)}</td>
              <td>{escape(line.description)}</td>
              <td class="amount-cell">{escape(money(line.debit, currency)) if line.debit else '-'}</td>
              <td class="amount-cell">{escape(money(line.credit, currency)) if line.credit else '-'}</td>
            </tr>
            """
        )
    return "".join(rows) or '<tr><td colspan="5" class="empty-report-row">No journal lines posted.</td></tr>'


def voucher_ledger_table(voucher: Voucher | None, currency: str) -> str:
    if not voucher:
        return '<tr><td colspan="5" class="empty-report-row">No voucher linked.</td></tr>'
    rows = []
    for line in voucher.ledger_lines.select_related("account").all():
        rows.append(
            f"""
            <tr>
              <td>{escape(line.account.code)}</td>
              <td>{escape(line.account.name)}</td>
              <td>{escape(line.description)}</td>
              <td class="amount-cell">{escape(money(line.debit, currency)) if line.debit else '-'}</td>
              <td class="amount-cell">{escape(money(line.credit, currency)) if line.credit else '-'}</td>
            </tr>
            """
        )
    return "".join(rows) or '<tr><td colspan="5" class="empty-report-row">No voucher ledger lines posted.</td></tr>'


def voucher_inventory_table(voucher: Voucher | None, currency: str) -> str:
    if not voucher:
        return '<tr><td colspan="6" class="empty-report-row">No voucher linked.</td></tr>'
    rows = []
    for line in voucher.inventory_lines.select_related("item", "godown").all():
        rows.append(
            f"""
            <tr>
              <td>{escape(line.item.name)}</td>
              <td>{escape(line.godown.name if line.godown else 'Main')}</td>
              <td>{escape(line.description)}</td>
              <td class="amount-cell">{escape(format_quantity(line.quantity))}</td>
              <td class="amount-cell">{escape(money(line.rate, currency))}</td>
              <td class="amount-cell">{escape(money(line.amount, currency))}</td>
            </tr>
            """
        )
    return "".join(rows) or '<tr><td colspan="6" class="empty-report-row">No stock lines on this voucher.</td></tr>'


def journal_entry_link(entry: JournalEntry | None) -> str:
    if not entry:
        return "Not linked"
    return f'<a href="/dashboard/accounting/journal/{entry.id}/">{escape(entry.memo)}</a>'


def voucher_link(voucher: Voucher | None) -> str:
    if not voucher:
        return "Not linked"
    return f'<a href="/dashboard/vouchers/{voucher.id}/">{escape(voucher.voucher_number)}</a>'


def statement_name_options(names: list[str], selected_name: str = "", placeholder: str = "Select name") -> str:
    options = [f'<option value="">{escape(placeholder)}</option>']
    for name in names:
        options.append(f'<option value="{escape(name)}" {"selected" if selected_name == name else ""}>{escape(name)}</option>')
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


def customer_statement_names(request: HttpRequest) -> list[str]:
    names = set(Client.objects.filter(account_q(request)).values_list("name", flat=True))
    names.update(Invoice.objects.filter(account_q(request)).values_list("client_name", flat=True))
    names.update(PaymentReceipt.objects.filter(account_q(request), invoice__isnull=True).values_list("payer_name", flat=True))
    return sorted(name for name in names if name)


def vendor_statement_names(request: HttpRequest) -> list[str]:
    names = set(VendorBill.objects.filter(account_q(request)).values_list("vendor_name", flat=True))
    names.update(VendorBillPayment.objects.filter(account_q(request)).values_list("vendor_name", flat=True))
    names.update(VendorDebitNote.objects.filter(account_q(request)).values_list("vendor_name", flat=True))
    return sorted(name for name in names if name)


def customer_statement_entries(request: HttpRequest, customer_name: str) -> tuple[list[dict[str, Any]], Decimal, Decimal, Decimal]:
    invoices = list(Invoice.objects.filter(account_q(request), client_name__iexact=customer_name).prefetch_related("payments", "line_items").order_by("created_at", "id"))
    invoice_ids = [invoice.id for invoice in invoices]
    credit_notes = list(
        CustomerCreditNote.objects.filter(account_q(request))
        .filter(Q(invoice_id__in=invoice_ids) | Q(client_name__iexact=customer_name))
        .select_related("invoice", "voucher")
        .order_by("credit_date", "created_at", "id")
    )
    receipts = list(
        PaymentReceipt.objects.filter(account_q(request))
        .filter(Q(invoice_id__in=invoice_ids) | Q(invoice__isnull=True, payer_name__iexact=customer_name))
        .select_related("invoice", "voucher")
        .order_by("payment_date", "created_at", "id")
    )
    events: list[dict[str, Any]] = []
    for invoice in invoices:
        amount = invoice_total_amount(invoice)
        events.append(
            {
                "date": timezone.localtime(invoice.created_at).date(),
                "sort": invoice.created_at,
                "kind": "Invoice",
                "reference": invoice_number(invoice),
                "description": invoice.service_name,
                "debit": amount,
                "credit": Decimal("0"),
                "link": f"/invoice/{invoice.public_token}/",
            }
        )
    for credit_note in credit_notes:
        events.append(
            {
                "date": credit_note.credit_date,
                "sort": credit_note.created_at,
                "kind": "Credit note",
                "reference": credit_note.credit_note_number,
                "description": credit_note.invoice and invoice_number(credit_note.invoice) or credit_note.reason,
                "debit": Decimal("0"),
                "credit": credit_note.total_amount,
                "link": f"/dashboard/credit-notes/{credit_note.id}/",
            }
        )
    for receipt in receipts:
        reference = receipt.voucher.voucher_number if receipt.voucher else receipt.reference
        events.append(
            {
                "date": receipt.payment_date,
                "sort": receipt.created_at,
                "kind": "Receipt",
                "reference": reference or "Receipt",
                "description": receipt.invoice and invoice_number(receipt.invoice) or receipt.reference or receipt.notes,
                "debit": Decimal("0"),
                "credit": receipt.amount,
                "link": f"/dashboard/payments/new/?invoice={receipt.invoice_id}" if receipt.invoice_id else "",
            }
        )
    events.sort(key=lambda event: (event["date"], event["sort"], event["kind"]))
    balance = Decimal("0")
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    rows = []
    for event in events:
        total_debit += event["debit"]
        total_credit += event["credit"]
        balance = (balance + event["debit"] - event["credit"]).quantize(Decimal("0.01"))
        event["balance"] = balance
        rows.append(event)
    return rows, total_debit.quantize(Decimal("0.01")), total_credit.quantize(Decimal("0.01")), max(balance, Decimal("0")).quantize(Decimal("0.01"))


def vendor_statement_entries(request: HttpRequest, vendor_name: str) -> tuple[list[dict[str, Any]], Decimal, Decimal, Decimal]:
    bills = list(VendorBill.objects.filter(account_q(request), vendor_name__iexact=vendor_name).prefetch_related("payments").order_by("bill_date", "id"))
    bill_ids = [bill.id for bill in bills]
    payments = list(
        VendorBillPayment.objects.filter(account_q(request))
        .filter(Q(bill_id__in=bill_ids) | Q(vendor_name__iexact=vendor_name))
        .select_related("bill", "voucher")
        .order_by("payment_date", "created_at", "id")
    )
    debit_notes = list(
        VendorDebitNote.objects.filter(account_q(request))
        .filter(Q(bill_id__in=bill_ids) | Q(vendor_name__iexact=vendor_name))
        .select_related("bill", "voucher")
        .order_by("debit_date", "created_at", "id")
    )
    events: list[dict[str, Any]] = []
    for bill in bills:
        events.append(
            {
                "date": bill.bill_date,
                "sort": bill.created_at,
                "kind": "Bill",
                "reference": bill.reference or (bill.voucher.voucher_number if bill.voucher else "Bill"),
                "description": bill.category,
                "debit": Decimal("0"),
                "credit": bill.amount,
                "link": f"/dashboard/expenses/pay/?bill={bill.id}" if vendor_bill_outstanding_amount(bill) > 0 else "",
            }
        )
    for payment in payments:
        events.append(
            {
                "date": payment.payment_date,
                "sort": payment.created_at,
                "kind": "Payment",
                "reference": payment.voucher.voucher_number if payment.voucher else payment.reference or "Payment",
                "description": payment.bill.reference if payment.bill and payment.bill.reference else payment.reference or payment.notes,
                "debit": payment.amount,
                "credit": Decimal("0"),
                "link": f"/dashboard/expenses/pay/?bill={payment.bill_id}",
            }
        )
    for debit_note in debit_notes:
        events.append(
            {
                "date": debit_note.debit_date,
                "sort": debit_note.created_at,
                "kind": "Debit note",
                "reference": debit_note.debit_note_number,
                "description": debit_note.bill.reference if debit_note.bill and debit_note.bill.reference else debit_note.reason,
                "debit": debit_note.amount,
                "credit": Decimal("0"),
                "link": f"/dashboard/debit-notes/{debit_note.id}/",
            }
        )
    events.sort(key=lambda event: (event["date"], event["sort"], event["kind"]))
    balance = Decimal("0")
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    rows = []
    for event in events:
        total_debit += event["debit"]
        total_credit += event["credit"]
        balance = (balance + event["credit"] - event["debit"]).quantize(Decimal("0.01"))
        event["balance"] = balance
        rows.append(event)
    return rows, total_credit.quantize(Decimal("0.01")), total_debit.quantize(Decimal("0.01")), max(balance, Decimal("0")).quantize(Decimal("0.01"))


def journal_totals_for_user(request: HttpRequest) -> dict[str, Decimal]:
    entries = JournalEntry.objects.filter(account_q(request), is_posted=True)
    return {
        "assets": sum((line.debit - line.credit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="asset")), Decimal("0")),
        "liabilities": sum((line.credit - line.debit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="liability")), Decimal("0")),
        "equity": sum((line.credit - line.debit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="equity")), Decimal("0")),
        "income": sum((line.credit - line.debit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="revenue")), Decimal("0")),
        "expenses": sum((line.debit - line.credit for line in JournalLine.objects.filter(entry__in=entries, account__account_type="expense")), Decimal("0")),
    }


def invoice_total_amount(invoice: Invoice) -> Decimal:
    subtotal = invoice_subtotal(invoice)
    tax = subtotal * invoice.gst_rate / Decimal("100") if invoice.include_gst else Decimal("0")
    return (subtotal + tax).quantize(Decimal("0.01"))


def invoice_amount_received(invoice: Invoice) -> Decimal:
    received = sum((payment.amount for payment in invoice.payments.all()), Decimal("0"))
    reversed_amount = sum((reversal.amount for reversal in PaymentReversal.objects.filter(customer_receipt__invoice=invoice)), Decimal("0"))
    return max(received - reversed_amount, Decimal("0")).quantize(Decimal("0.01"))


def invoice_amount_credited(invoice: Invoice) -> Decimal:
    return sum((credit.total_amount for credit in invoice.credit_notes.all()), Decimal("0")).quantize(Decimal("0.01"))


def invoice_outstanding_amount(invoice: Invoice) -> Decimal:
    if invoice.document_type != "tax_invoice":
        return Decimal("0.00")
    balance = invoice_total_amount(invoice) - invoice_amount_received(invoice) - invoice_amount_credited(invoice)
    return max(balance, Decimal("0")).quantize(Decimal("0.01"))


def update_invoice_payment_status(invoice: Invoice) -> None:
    received = invoice_amount_received(invoice)
    credited = invoice_amount_credited(invoice)
    total = invoice_total_amount(invoice)
    if total <= 0:
        return
    if received >= total:
        invoice.status = "paid"
    elif credited >= total and received <= 0:
        invoice.status = "credited"
    elif received + credited >= total:
        invoice.status = "paid"
    elif received > 0:
        invoice.status = "partially_paid"
    elif credited > 0:
        invoice.status = "partially_credited"
    elif invoice.status in {"paid", "partially_paid", "partially_credited", "credited"}:
        invoice.status = "sent"
    else:
        return
    invoice.save(update_fields=["status", "updated_at"])


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


def vendor_bill_amount_paid(bill: VendorBill) -> Decimal:
    payments = list(bill.payments.all()) if getattr(bill, "pk", None) else []
    if payments:
        paid = sum((payment.amount for payment in payments), Decimal("0"))
        reversed_amount = sum((reversal.amount for reversal in PaymentReversal.objects.filter(vendor_payment__bill=bill)), Decimal("0"))
        return max(paid - reversed_amount, Decimal("0")).quantize(Decimal("0.01"))
    return bill.amount if bill.status == "paid" else Decimal("0")


def vendor_bill_amount_debited(bill: VendorBill) -> Decimal:
    return sum((debit_note.amount for debit_note in bill.debit_notes.all()), Decimal("0")).quantize(Decimal("0.01"))


def vendor_bill_outstanding_amount(bill: VendorBill) -> Decimal:
    balance = bill.amount - vendor_bill_amount_paid(bill) - vendor_bill_amount_debited(bill)
    return max(balance, Decimal("0")).quantize(Decimal("0.01"))


def update_vendor_bill_payment_status(bill: VendorBill) -> None:
    paid = vendor_bill_amount_paid(bill)
    debited = vendor_bill_amount_debited(bill)
    outstanding = max(bill.amount - paid - debited, Decimal("0")).quantize(Decimal("0.01"))
    if outstanding <= 0 and bill.amount > 0:
        bill.status = "paid"
    elif paid > 0:
        bill.status = "partially_paid"
    elif bill.status in {"paid", "partially_paid"}:
        bill.status = "unpaid"
    else:
        return
    bill.save(update_fields=["status"])


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


def inventory_item_options(items, selected_id: str = "") -> str:
    options = ['<option value="">Select inventory item</option>']
    for item in items:
        label = f"{item.sku + ' - ' if item.sku else ''}{item.name}"
        options.append(f'<option value="{item.id}" {"selected" if selected_id and str(item.id) == str(selected_id) else ""}>{escape(label)}</option>')
    return "".join(options)


def godown_options(godowns, selected_id: str = "") -> str:
    return "".join(
        f'<option value="{godown.id}" {"selected" if selected_id and str(godown.id) == str(selected_id) else ""}>{escape(godown.name)}</option>'
        for godown in godowns
    )


def voucher_type_options(selected: str = "sales") -> str:
    options = [
        ("sales", "Sales"),
        ("purchase", "Purchase"),
        ("expense", "Expense"),
        ("receipt", "Receipt"),
        ("payment", "Payment"),
        ("contra", "Contra"),
        ("journal", "Journal"),
    ]
    return "".join(f'<option value="{value}" {"selected" if selected == value else ""}>{label}</option>' for value, label in options)


def stock_signed_quantity(movement: StockMovement) -> Decimal:
    if movement.movement_type in {"sale"}:
        return -movement.quantity
    return movement.quantity


def stock_quantity(item: InventoryItem) -> Decimal:
    return sum((stock_signed_quantity(movement) for movement in item.movements.all()), Decimal("0")).quantize(Decimal("0.01"))


def stock_status(item: InventoryItem, quantity: Decimal) -> str:
    if not item.track_inventory:
        return "Not tracked"
    if quantity <= 0:
        return "Out of stock"
    if item.reorder_level and quantity <= item.reorder_level:
        return "Low stock"
    return "In stock"


def extract_amount(text: str) -> Decimal:
    match = re.search(r"(?:rs\.?|inr|usd|\$|\u20b9)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text, re.IGNORECASE)
    return decimal_value(match.group(1).replace(",", "")) if match else Decimal("0")


def detect_business_type(text: str) -> str:
    lowered = text.lower()
    keyword_map = {
        "manufacturing": ["manufactur", "factory", "raw material", "finished goods", "production"],
        "travel": ["travel", "tour", "ticket", "hotel booking", "visa"],
        "trading": ["trading", "retail", "shop", "store", "wholesale", "product", "ecommerce"],
        "restaurant": ["restaurant", "cafe", "food", "catering", "kitchen"],
        "construction": ["construction", "contractor", "site", "interior", "civil"],
        "education": ["education", "coaching", "tuition", "school", "training"],
        "healthcare": ["clinic", "health", "doctor", "medical", "wellness"],
        "professional": ["accountant", "lawyer", "consultant", "architect", "firm"],
        "service": ["service", "freelancer", "agency", "repair", "maintenance"],
    }
    for business_type, keywords in keyword_map.items():
        if any(keyword in lowered for keyword in keywords):
            return business_type
    return "other"


def expense_account_for_text(request: HttpRequest, text: str) -> Account:
    ensure_default_chart(request)
    lowered = text.lower()
    code = "5100"
    if any(keyword in lowered for keyword in ["software", "subscription", "hosting", "domain"]):
        code = "5400"
    elif any(keyword in lowered for keyword in ["travel", "fuel", "taxi", "flight", "hotel"]):
        code = "5300"
    elif any(keyword in lowered for keyword in ["marketing", "advertising", "facebook", "google ads", "promotion"]):
        code = "5200"
    elif any(keyword in lowered for keyword in ["subcontract", "contractor", "labour", "labor"]):
        code = "5500"
    try:
        return account_by_code(request, code)
    except Account.DoesNotExist:
        return account_by_code(request, "5100")


def ai_parse_invoice_prompt(prompt: str) -> dict[str, Any]:
    text = clean_text(prompt, max_length=1000)
    client_name = "Client"
    client_match = re.search(r"(?:for|to)\s+([^,\.]+?)(?:\s+(?:for|with)\s+|,|$)", text, re.IGNORECASE)
    if client_match:
        client_name = clean_text(client_match.group(1), "Client", 180)
    quantity = Decimal("1")
    quantity_match = re.search(r"\b(\d+(?:\.\d+)?)\s+(?:x\s+)?([A-Za-z][^,@]*)", text)
    if quantity_match:
        quantity = decimal_value(quantity_match.group(1)) or Decimal("1")
        description = clean_text(quantity_match.group(2), "Service", 240)
    else:
        description_match = re.search(r"(?:for|of)\s+([^,@]+)", text, re.IGNORECASE)
        description = clean_text(description_match.group(1) if description_match else "Service", "Service", 240)
    rate_match = re.search(r"(?:at|rate|each|for)\s+(?:rs\.?|inr|usd|\$|\u20b9)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text, re.IGNORECASE)
    amount = extract_amount(text)
    rate = decimal_value(rate_match.group(1).replace(",", "")) if rate_match else amount
    if rate <= 0:
        rate = Decimal("1")
    due_match = re.search(r"due\s+(?:in\s+)?(\d+)\s+day", text, re.IGNORECASE)
    gst_match = re.search(r"(?:gst|tax)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    return {
        "client_name": client_name,
        "description": description,
        "quantity": quantity,
        "rate": rate,
        "due_days": int(due_match.group(1)) if due_match else 7,
        "gst_rate": decimal_value(gst_match.group(1)) if gst_match else Decimal("18"),
    }


def ai_parse_expense_prompt(request: HttpRequest, prompt: str) -> dict[str, Any]:
    text = clean_text(prompt, max_length=1000)
    vendor = "Vendor"
    vendor_match = re.search(r"(?:to|from|paid\s+to)\s+([^,\.]+?)(?:\s+for\s+|,|$)", text, re.IGNORECASE)
    if vendor_match:
        vendor = clean_text(vendor_match.group(1), "Vendor", 180)
    category_match = re.search(r"\bfor\s+([^,\.]+)", text, re.IGNORECASE)
    category = clean_text(category_match.group(1) if category_match else expense_account_for_text(request, text).name, "Office expenses", 180)
    status = "unpaid" if any(keyword in text.lower() for keyword in ["bill", "unpaid", "pay later", "due"]) else "paid"
    method = "cash" if "cash" in text.lower() else "upi" if "upi" in text.lower() else "card" if "card" in text.lower() else "bank"
    account = expense_account_for_text(request, f"{category} {text}")
    return {
        "vendor_name": vendor,
        "category": category,
        "amount": extract_amount(text),
        "status": status,
        "payment_method": method,
        "expense_account": account,
    }


def ai_parse_payment_prompt(prompt: str) -> dict[str, Any]:
    text = clean_text(prompt, max_length=1000)
    payer = ""
    payer_match = re.search(r"(?:from|by)\s+([^,\.]+)", text, re.IGNORECASE)
    if payer_match:
        payer = clean_text(payer_match.group(1), max_length=180)
    method = "cash" if "cash" in text.lower() else "upi" if "upi" in text.lower() else "card" if "card" in text.lower() else "bank"
    return {"payer_name": payer, "amount": extract_amount(text), "method": method}


def ai_match_payment_invoices(request: HttpRequest, payer_name: str, amount: Decimal):
    invoices = list(Invoice.objects.filter(account_q(request)).exclude(status="paid").order_by("-created_at")[:50])
    matches = []
    lowered_payer = payer_name.lower()
    for invoice in invoices:
        score = 0
        if amount > 0 and invoice_total_amount(invoice) == amount:
            score += 4
        if lowered_payer and lowered_payer in invoice.client_name.lower():
            score += 3
        if score:
            matches.append((score, invoice))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [invoice for _score, invoice in matches[:5]]


def ai_dashboard_summary(request: HttpRequest) -> list[str]:
    finance = finance_summary_for_user(request)
    totals = journal_totals_for_user(request)
    open_invoices = Invoice.objects.filter(account_q(request), document_type="tax_invoice").exclude(status="paid").count()
    overdue_invoices = sum(1 for invoice in Invoice.objects.filter(account_q(request), document_type="tax_invoice").exclude(status="paid") if invoice_due_date(invoice).date() < timezone.localdate())
    low_stock = 0
    for item in InventoryItem.objects.filter(account_q(request), is_active=True).prefetch_related("movements"):
        quantity = stock_quantity(item)
        if item.track_inventory and quantity <= item.reorder_level:
            low_stock += 1
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    profit = totals["income"] - totals["expenses"]
    return [
        f"Receivables are {money(finance['accounts_receivable'], currency)} across {open_invoices} open invoice(s).",
        f"Payables are {money(finance['accounts_payable'], currency)}. Keep vendor bills updated before cash planning.",
        f"Current profit from posted entries is {money(profit, currency)}.",
        f"{overdue_invoices} invoice(s) appear overdue based on due date.",
        f"{low_stock} inventory item(s) are at or below reorder level.",
    ]


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


def normalized_payment_method(method: str) -> str:
    return method if method in {"bank", "cash", "upi", "card", "check", "paypal", "stripe", "other"} else "bank"


def cash_account_for_method(request: HttpRequest, method: str) -> Account:
    return account_by_code(request, "1000" if normalized_payment_method(method) == "cash" else "1010")


def post_customer_receipt(
    request: HttpRequest,
    *,
    invoice: Invoice | None,
    payment_date,
    payer_name: str,
    amount: Decimal,
    method: str,
    reference: str = "",
    notes: str = "",
) -> PaymentReceipt:
    method = normalized_payment_method(method)
    amount = amount.quantize(Decimal("0.01"))
    payment_date = payment_date or timezone.localdate()
    debit_account = cash_account_for_method(request, method)
    credit_account = account_by_code(request, "1100" if invoice else "4000")
    if invoice:
        outstanding = invoice_outstanding_amount(invoice)
        if outstanding <= 0:
            raise ValueError("This invoice has no outstanding balance.")
        if amount > outstanding:
            raise ValueError(f"Payment cannot exceed the outstanding balance of {invoice.currency_symbol} {outstanding}.")
    voucher = create_voucher_with_lines(
        request,
        voucher_type="receipt",
        party_name=payer_name,
        narration=notes or reference,
        voucher_date=payment_date,
        ledger_lines=[
            {"account": debit_account, "description": reference or payer_name, "debit": amount, "credit": Decimal("0")},
            {"account": credit_account, "description": invoice_number(invoice) if invoice else payer_name, "debit": Decimal("0"), "credit": amount},
        ],
    )
    receipt = PaymentReceipt.objects.create(
        market=current_market(request),
        owner=request.user,
        owner_email=current_account_email(request),
        invoice=invoice,
        journal_entry=voucher.journal_entry,
        voucher=voucher,
        payment_date=payment_date,
        payer_name=payer_name,
        amount=amount,
        method=method,
        reference=reference,
        notes=notes,
    )
    if invoice:
        update_invoice_payment_status(invoice)
    return receipt


def invoice_accounting_amounts(invoice: Invoice) -> dict[str, Decimal]:
    subtotal = invoice_subtotal(invoice).quantize(Decimal("0.01"))
    tax = (subtotal * invoice.gst_rate / Decimal("100") if invoice.include_gst else Decimal("0")).quantize(Decimal("0.01"))
    total = (subtotal + tax).quantize(Decimal("0.01"))
    return {"subtotal": subtotal, "tax": tax, "total": total}


def gst_split(tax_amount: Decimal, supply_type: str) -> dict[str, Decimal]:
    """Split a GST amount into CGST/SGST (intra-state) or IGST (inter-state).

    CGST and SGST are each half of the tax; the rounding remainder is given to
    SGST so cgst + sgst == tax_amount exactly (no paisa lost).
    """
    tax_amount = (tax_amount or Decimal("0")).quantize(Decimal("0.01"))
    zero = Decimal("0.00")
    if tax_amount <= 0:
        return {"cgst": zero, "sgst": zero, "igst": zero}
    if supply_type == "inter":
        return {"cgst": zero, "sgst": zero, "igst": tax_amount}
    cgst = (tax_amount / Decimal("2")).quantize(Decimal("0.01"))
    sgst = (tax_amount - cgst).quantize(Decimal("0.01"))
    return {"cgst": cgst, "sgst": sgst, "igst": zero}


def clean_supply_type(value: Any) -> str:
    value = clean_text(value, max_length=10)
    return value if value in {"intra", "inter"} else "intra"


def clean_document_type(value: Any) -> str:
    value = clean_text(value, max_length=20)
    return value if value in {"tax_invoice", "proforma", "quotation"} else "tax_invoice"


def gst_payable_lines(request: HttpRequest, invoice: Invoice, tax_amount: Decimal, *, on_debit: bool = False) -> list[dict]:
    """Ledger line(s) for the tax component of an invoice.

    US sales-tax invoices post to the single Sales tax / GST payable account (2100).
    Indian GST invoices post the CGST/SGST/IGST split to dedicated accounts
    (2110/2120/2130). Pass on_debit=True to reverse the side (credit notes).
    """
    tax_amount = (tax_amount or Decimal("0")).quantize(Decimal("0.01"))
    if tax_amount <= 0:
        return []
    label = invoice_tax_label(invoice)

    def line(code: str, amount: Decimal, suffix: str = "") -> dict:
        return {
            "account": account_by_code(request, code),
            "description": f"{label} {suffix}".strip(),
            "debit": amount if on_debit else Decimal("0"),
            "credit": Decimal("0") if on_debit else amount,
        }

    if invoice_is_us(invoice):
        return [line("2100", tax_amount)]

    split = gst_split(tax_amount, invoice.supply_type)
    lines = []
    if split["igst"] > 0:
        lines.append(line("2130", split["igst"], "IGST"))
    if split["cgst"] > 0:
        lines.append(line("2110", split["cgst"], "CGST"))
    if split["sgst"] > 0:
        lines.append(line("2120", split["sgst"], "SGST"))
    return lines or [line("2100", tax_amount)]


def delete_invoice_sales_voucher(invoice: Invoice) -> None:
    voucher = invoice.sales_voucher
    if not voucher:
        return
    journal = voucher.journal_entry
    invoice.sales_voucher = None
    invoice.save(update_fields=["sales_voucher", "updated_at"])
    voucher.delete()
    if journal and not journal.vouchers.exists() and not journal.payment_receipts.exists() and not journal.vendor_bills.exists():
        journal.delete()


def post_invoice_sales_voucher(request: HttpRequest, invoice: Invoice, *, replace: bool = False) -> Voucher | None:
    if not request.user.is_authenticated or invoice.owner_id is None:
        return None
    if invoice.document_type != "tax_invoice":
        # Quotations and proforma invoices are non-accounting documents: no revenue,
        # no receivable and no GST liability until they become a tax invoice.
        return None
    ensure_default_chart(request)
    if invoice.sales_voucher_id and not replace:
        return invoice.sales_voucher
    if replace:
        delete_invoice_sales_voucher(invoice)
    amounts = invoice_accounting_amounts(invoice)
    if amounts["total"] <= 0:
        return None
    ledger_lines = [
        {"account": account_by_code(request, "1100"), "description": invoice_number(invoice), "debit": amounts["total"], "credit": Decimal("0")},
        {"account": account_by_code(request, "4000"), "description": invoice.service_name or "Invoice income", "debit": Decimal("0"), "credit": amounts["subtotal"]},
    ]
    ledger_lines.extend(gst_payable_lines(request, invoice, amounts["tax"]))
    voucher = create_voucher_with_lines(
        request,
        voucher_type="sales",
        party_name=invoice.client_name,
        narration=f"Sales invoice {invoice_number(invoice)}",
        voucher_date=timezone.localdate(),
        ledger_lines=ledger_lines,
    )
    invoice.sales_voucher = voucher
    invoice.save(update_fields=["sales_voucher", "updated_at"])
    return voucher


def split_credit_note_amount(invoice: Invoice, total_amount: Decimal) -> tuple[Decimal, Decimal]:
    total_amount = total_amount.quantize(Decimal("0.01"))
    if not invoice.include_gst or invoice.gst_rate <= 0:
        return total_amount, Decimal("0.00")
    denominator = Decimal("100") + invoice.gst_rate
    tax_amount = (total_amount * invoice.gst_rate / denominator).quantize(Decimal("0.01"))
    taxable_amount = (total_amount - tax_amount).quantize(Decimal("0.01"))
    return taxable_amount, tax_amount


def post_customer_credit_note(
    request: HttpRequest,
    *,
    invoice: Invoice,
    credit_date,
    total_amount: Decimal,
    reason: str,
    notes: str = "",
) -> CustomerCreditNote:
    total_amount = total_amount.quantize(Decimal("0.01"))
    credit_date = credit_date or timezone.localdate()
    outstanding = invoice_outstanding_amount(invoice)
    if outstanding <= 0:
        raise ValueError("This invoice has no outstanding balance to credit.")
    if total_amount <= 0:
        raise ValueError("Credit note amount must be greater than zero.")
    if total_amount > outstanding:
        raise ValueError(f"Credit note cannot exceed the outstanding balance of {invoice.currency_symbol} {outstanding}.")
    taxable_amount, tax_amount = split_credit_note_amount(invoice, total_amount)
    with transaction.atomic():
        ledger_lines = []
        if taxable_amount > 0:
            ledger_lines.append({"account": account_by_code(request, "4000"), "description": reason or invoice_number(invoice), "debit": taxable_amount, "credit": Decimal("0")})
        ledger_lines.extend(gst_payable_lines(request, invoice, tax_amount, on_debit=True))
        ledger_lines.append({"account": account_by_code(request, "1100"), "description": invoice_number(invoice), "debit": Decimal("0"), "credit": total_amount})
        voucher = create_voucher_with_lines(
            request,
            voucher_type="credit_note",
            party_name=invoice.client_name,
            narration=notes or reason,
            voucher_date=credit_date,
            ledger_lines=ledger_lines,
        )
        credit_note = CustomerCreditNote.objects.create(
            market=current_market(request),
            owner=request.user,
            owner_email=current_account_email(request),
            invoice=invoice,
            voucher=voucher,
            journal_entry=voucher.journal_entry,
            credit_note_number=next_credit_note_number(request),
            credit_date=credit_date,
            client_name=invoice.client_name,
            taxable_amount=taxable_amount,
            tax_amount=tax_amount,
            total_amount=total_amount,
            reason=reason or "Invoice correction",
            notes=notes,
        )
        update_invoice_payment_status(invoice)
    return credit_note


def post_expense_bill(
    request: HttpRequest,
    *,
    bill_date,
    due_date=None,
    vendor_name: str,
    category: str,
    amount: Decimal,
    status: str,
    payment_method: str,
    expense_account: Account,
    reference: str = "",
    notes: str = "",
    source_prefix: str = "",
) -> VendorBill:
    status = status if status in {"paid", "unpaid"} else "paid"
    payment_method = normalized_payment_method(payment_method)
    credit_account = account_by_code(request, "2000" if status == "unpaid" else ("1000" if payment_method == "cash" else "1010"))
    bill_date = bill_date or timezone.localdate()
    voucher = create_voucher_with_lines(
        request,
        voucher_type="expense",
        party_name=vendor_name,
        narration=notes or reference,
        voucher_date=bill_date,
        ledger_lines=[
            {"account": expense_account, "description": category or expense_account.name, "debit": amount, "credit": Decimal("0")},
            {"account": credit_account, "description": reference or vendor_name, "debit": Decimal("0"), "credit": amount},
        ],
    )
    return VendorBill.objects.create(
        market=current_market(request),
        owner=request.user,
        owner_email=current_account_email(request),
        journal_entry=voucher.journal_entry,
        voucher=voucher,
        bill_date=bill_date,
        due_date=due_date or None,
        paid_date=bill_date if status == "paid" else None,
        vendor_name=vendor_name,
        category=category or expense_account.name,
        amount=amount,
        status=status,
        payment_method=payment_method,
        reference=reference,
        notes=notes,
    )


def post_vendor_bill_payment(
    request: HttpRequest,
    *,
    bill: VendorBill,
    payment_date,
    amount: Decimal,
    method: str,
    reference: str = "",
    notes: str = "",
) -> Voucher:
    method = normalized_payment_method(method)
    amount = amount.quantize(Decimal("0.01"))
    payment_date = payment_date or timezone.localdate()
    outstanding = vendor_bill_outstanding_amount(bill)
    if outstanding <= 0:
        raise ValueError("This vendor bill has no outstanding balance.")
    if amount > outstanding:
        raise ValueError(f"Payment cannot exceed the outstanding vendor bill balance of {outstanding}.")
    debit_account = account_by_code(request, "2000")
    credit_account = cash_account_for_method(request, method)
    voucher = create_voucher_with_lines(
        request,
        voucher_type="payment",
        party_name=bill.vendor_name,
        narration=notes or reference,
        voucher_date=payment_date,
        ledger_lines=[
            {"account": debit_account, "description": bill.reference or bill.vendor_name, "debit": amount, "credit": Decimal("0")},
            {"account": credit_account, "description": reference or bill.vendor_name, "debit": Decimal("0"), "credit": amount},
        ],
    )
    payment = VendorBillPayment.objects.create(
        market=current_market(request),
        owner=request.user,
        owner_email=current_account_email(request),
        bill=bill,
        voucher=voucher,
        payment_date=payment_date,
        vendor_name=bill.vendor_name,
        amount=amount,
        method=method,
        reference=reference,
        notes=notes,
    )
    bill.payment_method = method
    bill.payment_voucher = voucher
    bill.payment_reference = reference
    if notes:
        bill.notes = f"{bill.notes}\nPayment note: {notes}".strip()
    bill.save(update_fields=["payment_method", "payment_voucher", "payment_reference", "notes"])
    update_vendor_bill_payment_status(bill)
    bill.refresh_from_db()
    if bill.status == "paid":
        bill.paid_date = payment.payment_date
        bill.save(update_fields=["paid_date"])
    return voucher


def vendor_bill_expense_account(request: HttpRequest, bill: VendorBill) -> Account:
    if bill.voucher_id:
        expense_line = bill.voucher.ledger_lines.select_related("account").filter(debit__gt=0, account__account_type="expense").first()
        if expense_line:
            return expense_line.account
    return account_by_code(request, "5100")


def post_vendor_debit_note(
    request: HttpRequest,
    *,
    bill: VendorBill,
    debit_date,
    amount: Decimal,
    reason: str,
    notes: str = "",
) -> VendorDebitNote:
    amount = amount.quantize(Decimal("0.01"))
    debit_date = debit_date or timezone.localdate()
    outstanding = vendor_bill_outstanding_amount(bill)
    if outstanding <= 0:
        raise ValueError("This vendor bill has no outstanding balance to adjust.")
    if amount <= 0:
        raise ValueError("Debit note amount must be greater than zero.")
    if amount > outstanding:
        raise ValueError(f"Debit note cannot exceed the outstanding vendor bill balance of {outstanding}.")
    with transaction.atomic():
        voucher = create_voucher_with_lines(
            request,
            voucher_type="debit_note",
            party_name=bill.vendor_name,
            narration=notes or reason,
            voucher_date=debit_date,
            ledger_lines=[
                {"account": account_by_code(request, "2000"), "description": bill.reference or bill.vendor_name, "debit": amount, "credit": Decimal("0")},
                {"account": vendor_bill_expense_account(request, bill), "description": reason or bill.category, "debit": Decimal("0"), "credit": amount},
            ],
        )
        debit_note = VendorDebitNote.objects.create(
            market=current_market(request),
            owner=request.user,
            owner_email=current_account_email(request),
            bill=bill,
            voucher=voucher,
            journal_entry=voucher.journal_entry,
            debit_note_number=next_debit_note_number(request),
            debit_date=debit_date,
            vendor_name=bill.vendor_name,
            amount=amount,
            reason=reason or "Vendor bill adjustment",
            notes=notes,
        )
        update_vendor_bill_payment_status(bill)
    return debit_note


def payment_receipt_amount_reversed(receipt: PaymentReceipt) -> Decimal:
    return sum((reversal.amount for reversal in receipt.reversals.all()), Decimal("0")).quantize(Decimal("0.01"))


def vendor_payment_amount_reversed(payment: VendorBillPayment) -> Decimal:
    return sum((reversal.amount for reversal in payment.reversals.all()), Decimal("0")).quantize(Decimal("0.01"))


def post_customer_receipt_reversal(
    request: HttpRequest,
    *,
    receipt: PaymentReceipt,
    reversal_date,
    amount: Decimal,
    reason: str,
    notes: str = "",
) -> PaymentReversal:
    amount = amount.quantize(Decimal("0.01"))
    available = (receipt.amount - payment_receipt_amount_reversed(receipt)).quantize(Decimal("0.01"))
    if available <= 0:
        raise ValueError("This receipt has already been fully reversed.")
    if amount <= 0:
        raise ValueError("Reversal amount must be greater than zero.")
    if amount > available:
        raise ValueError(f"Reversal cannot exceed available receipt amount of {available}.")
    with transaction.atomic():
        voucher = create_voucher_with_lines(
            request,
            voucher_type="reversal",
            party_name=receipt.payer_name,
            narration=notes or reason,
            voucher_date=reversal_date or timezone.localdate(),
            ledger_lines=[
                {"account": account_by_code(request, "1100" if receipt.invoice else "4000"), "description": reason or receipt.reference, "debit": amount, "credit": Decimal("0")},
                {"account": cash_account_for_method(request, receipt.method), "description": reason or receipt.reference, "debit": Decimal("0"), "credit": amount},
            ],
        )
        reversal = PaymentReversal.objects.create(
            market=current_market(request),
            owner=request.user,
            owner_email=current_account_email(request),
            reversal_type="customer_receipt",
            reversal_number=next_reversal_number(request),
            reversal_date=reversal_date or timezone.localdate(),
            customer_receipt=receipt,
            voucher=voucher,
            journal_entry=voucher.journal_entry,
            party_name=receipt.payer_name,
            amount=amount,
            reason=reason or "Receipt reversal",
            notes=notes,
        )
        if receipt.invoice:
            update_invoice_payment_status(receipt.invoice)
    return reversal


def post_vendor_payment_reversal(
    request: HttpRequest,
    *,
    payment: VendorBillPayment,
    reversal_date,
    amount: Decimal,
    reason: str,
    notes: str = "",
) -> PaymentReversal:
    amount = amount.quantize(Decimal("0.01"))
    available = (payment.amount - vendor_payment_amount_reversed(payment)).quantize(Decimal("0.01"))
    if available <= 0:
        raise ValueError("This vendor payment has already been fully reversed.")
    if amount <= 0:
        raise ValueError("Reversal amount must be greater than zero.")
    if amount > available:
        raise ValueError(f"Reversal cannot exceed available vendor payment amount of {available}.")
    with transaction.atomic():
        voucher = create_voucher_with_lines(
            request,
            voucher_type="reversal",
            party_name=payment.vendor_name,
            narration=notes or reason,
            voucher_date=reversal_date or timezone.localdate(),
            ledger_lines=[
                {"account": cash_account_for_method(request, payment.method), "description": reason or payment.reference, "debit": amount, "credit": Decimal("0")},
                {"account": account_by_code(request, "2000"), "description": reason or payment.reference, "debit": Decimal("0"), "credit": amount},
            ],
        )
        reversal = PaymentReversal.objects.create(
            market=current_market(request),
            owner=request.user,
            owner_email=current_account_email(request),
            reversal_type="vendor_payment",
            reversal_number=next_reversal_number(request),
            reversal_date=reversal_date or timezone.localdate(),
            vendor_payment=payment,
            voucher=voucher,
            journal_entry=voucher.journal_entry,
            party_name=payment.vendor_name,
            amount=amount,
            reason=reason or "Vendor payment reversal",
            notes=notes,
        )
        update_vendor_bill_payment_status(payment.bill)
    return reversal


def finance_summary_for_user(request: HttpRequest) -> dict[str, Decimal]:
    invoices = Invoice.objects.filter(account_q(request))
    bills = VendorBill.objects.filter(account_q(request))
    customer_reversals = PaymentReversal.objects.filter(account_q(request), reversal_type="customer_receipt")
    vendor_reversals = PaymentReversal.objects.filter(account_q(request), reversal_type="vendor_payment")
    return {
        "accounts_receivable": sum((invoice_outstanding_amount(invoice) for invoice in invoices.exclude(status="paid")), Decimal("0")),
        "accounts_payable": sum((vendor_bill_outstanding_amount(bill) for bill in bills.exclude(status="paid")), Decimal("0")),
        "cash_collected": sum((receipt.amount for receipt in PaymentReceipt.objects.filter(account_q(request))), Decimal("0")) - sum((reversal.amount for reversal in customer_reversals), Decimal("0")),
        "bills_paid": sum((payment.amount for payment in VendorBillPayment.objects.filter(account_q(request))), Decimal("0")) - sum((reversal.amount for reversal in vendor_reversals), Decimal("0")),
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
    existing = BusinessProfile.objects.filter(market=invoice.market, owner_email=invoice.owner_email).first()
    if existing:
        defaults["business_type"] = existing.business_type
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


def valid_expense_document_upload(upload) -> str:
    if not upload:
        return "Upload a bill photo or PDF."
    allowed_types = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}
    if getattr(upload, "content_type", "") not in allowed_types:
        return "Upload a JPG, PNG, WebP, GIF or PDF bill."
    if getattr(upload, "size", 0) > 8 * 1024 * 1024:
        return "Bill upload must be 8 MB or smaller."
    return ""


def extract_expense_text_from_upload(upload) -> str:
    # Placeholder for OCR provider integration. Filename text still gives a useful
    # zero-cost first draft for mobile uploads named like airtel-1800-june.jpg.
    name_text = Path(getattr(upload, "name", "")).stem.replace("_", " ").replace("-", " ")
    return clean_text(name_text, max_length=1000)


def expense_draft_from_text(request: HttpRequest, text: str) -> dict[str, Any]:
    parsed = ai_parse_expense_prompt(request, text)
    return {
        "vendor_name": parsed["vendor_name"] if parsed["vendor_name"] != "Vendor" else "",
        "category": parsed["category"],
        "amount": parsed["amount"],
        "bill_status": parsed["status"],
        "payment_method": parsed["payment_method"],
        "reference": "",
        "notes": f"Extracted draft from upload: {text}" if text else "Please verify the uploaded bill details before posting.",
    }


DOCUMENT_NUMBER_PREFIX = {"quotation": "QTN", "proforma": "PI"}


def invoice_number(invoice: Invoice) -> str:
    prefix = DOCUMENT_NUMBER_PREFIX.get(invoice.document_type, "RL")
    return f"{prefix}-{invoice.created_at:%Y%m}-{invoice.id:05d}"


def document_type_label(invoice: Invoice) -> str:
    if invoice.document_type == "quotation":
        return "Quotation"
    if invoice.document_type == "proforma":
        return "Proforma invoice"
    return "Invoice" if invoice_is_us(invoice) else "Tax invoice"


def invoice_due_date(invoice: Invoice):
    return invoice.created_at + timedelta(days=invoice.due_days)


def invoice_gst_amount(invoice: Invoice) -> Decimal:
    if not invoice.include_gst:
        return Decimal("0")
    return (invoice_subtotal(invoice) * invoice.gst_rate / Decimal("100")).quantize(Decimal("0.01"))


def invoice_tax_breakup(invoice: Invoice) -> list[tuple[str, Decimal]]:
    """Tax line(s) to show on an invoice: CGST/SGST or IGST for Indian GST, single line for US sales tax."""
    if not invoice.include_gst:
        return []
    tax = invoice_gst_amount(invoice)
    label = invoice_tax_label(invoice)
    if tax <= 0 or invoice_is_us(invoice):
        return [(f"{label} @ {format_quantity(invoice.gst_rate)}%", tax)]
    split = gst_split(tax, invoice.supply_type)
    half = format_quantity(invoice.gst_rate / Decimal("2"))
    if split["igst"] > 0:
        return [(f"IGST @ {format_quantity(invoice.gst_rate)}%", split["igst"])]
    return [(f"CGST @ {half}%", split["cgst"]), (f"SGST @ {half}%", split["sgst"])]


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
        ("/dashboard/workflows/", "W", "Workflows"),
        ("/dashboard/invoices/new/", "I", "Create invoice"),
        ("/dashboard/payments/new/", "R", "Payments received"),
        ("/dashboard/expenses/new/", "E", "Expenses & bills"),
        ("/dashboard/ai/", "AI", "AI assistant"),
        ("/dashboard/vouchers/new/", "V", "Vouchers"),
        ("/dashboard/inventory/", "N", "Inventory"),
        ("/dashboard/reports/", "P", "Reports"),
        ("/dashboard/ledger/customers/", "CL", "Customer ledger"),
        ("/dashboard/ledger/vendors/", "VL", "Vendor ledger"),
        ("/dashboard/search/", "F", "Search"),
        ("/dashboard/help/", "?", "Help & guides"),
        ("/dashboard/audit/", "AT", "Audit trail"),
        ("/dashboard/reconciliation/", "RC", "Reconcile"),
        ("/dashboard/setup/", "T", "Business setup"),
        ("/dashboard/business-profile/", "S", "Business profile"),
        ("/dashboard/#invoices", "C", "Customers"),
        ("/dashboard/#accounting", "A", "Chart of accounts"),
        ("/dashboard/billing/pro/", "B", "Billing"),
    ]
    if request.user.is_staff:
        links.append(("/dashboard/monitoring/", "M", "Monitoring"))
        links.append(("/dashboard/gstn/", "GS", "GSTN API"))
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
    app_topbar = (
        '<header class="app-topbar">'
        '<form class="topbar-search" method="get" action="/dashboard/search/" role="search">'
        '<input type="search" name="q" placeholder="Search anything — invoices, quotations, reports, GST, help…" aria-label="Search RozLedger" autocomplete="off" />'
        '<button type="submit">Search</button>'
        '</form>'
        '<a class="app-topbar-help" href="/dashboard/help/">Help &amp; guides</a>'
        '</header>'
    ) if is_app else ""
    app_shell_open = f'<div class="app-layout">{app_sidebar(request)}<div class="app-main">{app_topbar}' if is_app else ""
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
    if is_us_host(request):
        return serve_project_file("pricing-us.html", "text/html")
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
    currency = "$" if us_market else RUPEE_SYMBOL
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
    paid_count = Invoice.objects.filter(account_q(request), document_type="tax_invoice", status="paid").count()
    pending_count = Invoice.objects.filter(account_q(request), document_type="tax_invoice").exclude(status="paid").count()
    accounts = Account.objects.filter(account_q(request), is_active=True)[:30]
    journal_entries = JournalEntry.objects.filter(account_q(request))[:10]
    accounting_totals = journal_totals_for_user(request)
    finance_summary = finance_summary_for_user(request)
    payment_receipts = PaymentReceipt.objects.filter(account_q(request)).select_related("invoice", "voucher", "journal_entry")[:8]
    vendor_bills = VendorBill.objects.filter(account_q(request))[:8]
    inventory_items = list(InventoryItem.objects.filter(account_q(request), is_active=True).prefetch_related("movements")[:8])
    stock_snapshots = [(item, stock_quantity(item)) for item in inventory_items]
    low_stock_count = sum(1 for item, quantity in stock_snapshots if item.track_inventory and quantity <= item.reorder_level)
    today = timezone.localdate()
    all_open_invoices = list(Invoice.objects.filter(account_q(request)).exclude(status="paid").order_by("created_at"))
    overdue_invoice_count = sum(
        1
        for open_invoice in all_open_invoices
        if invoice_outstanding_amount(open_invoice) > 0
        and timezone.localtime(open_invoice.created_at).date() + timedelta(days=open_invoice.due_days) < today
    )
    open_bill_count = VendorBill.objects.filter(account_q(request), status__in=["unpaid", "partially_paid"]).count()
    recent_reconciliation = ReconciliationSession.objects.filter(account_q(request)).first()
    profile_fields = [
        bool(business_profile),
        bool(business_profile and business_profile.business_phone),
        bool(business_profile and business_profile.business_address),
        bool(business_profile and (business_profile.bank_details or business_profile.upi_link)),
    ]
    profile_score = int((sum(profile_fields) / len(profile_fields)) * 100)

    next_actions = []
    if not business_profile:
        next_actions.append(("Setup", "Create business profile", "Save company name, logo, address, phone, bank details and default invoice template.", "/dashboard/business-profile/", "primary"))
    elif profile_score < 100:
        next_actions.append(("Setup", f"Complete profile ({profile_score}%)", "Add the missing contact or payment details so every invoice is customer-ready.", "/dashboard/business-profile/", "primary"))
    else:
        next_actions.append(("Ready", "Business profile ready", "Your saved profile can prefill invoices and keep branding consistent.", "/dashboard/business-profile/", "secondary"))

    if not invoices:
        next_actions.append(("Sales", "Create first invoice", "Use quantity, rate, logo, address, phone and your chosen professional template.", "/dashboard/invoices/new/", "primary"))
    elif finance_summary["accounts_receivable"] > 0:
        label = f"{overdue_invoice_count} overdue invoice(s)" if overdue_invoice_count else "Collect open invoices"
        next_actions.append(("AR", label, f"Outstanding customer balance is {money(finance_summary['accounts_receivable'], currency)}. Record payments against the selected invoice.", "/dashboard/payments/new/", "primary"))
    else:
        next_actions.append(("Sales", "Create next invoice", "Keep the billing habit daily and let the dashboard update ledgers automatically.", "/dashboard/invoices/new/", "secondary"))

    if open_bill_count:
        next_actions.append(("AP", "Pay vendor bills", f"{open_bill_count} vendor bill(s) are open. Post payments against the selected bill to update AP.", "/dashboard/expenses/pay/", "primary"))
    else:
        next_actions.append(("Purchases", "Record expense or bill", "Capture daily expenses, unpaid bills and supplier references before they are forgotten.", "/dashboard/expenses/new/", "secondary"))

    if recent_reconciliation:
        next_actions.append(("Control", "Review reports", "Use P&L, AR aging, AP aging, cash summary, trial balance and tax summary for control.", "/dashboard/reports/", "secondary"))
    else:
        next_actions.append(("Control", "Reconcile bank/cash", "Compare posted ledger lines with your bank or cash statement and save reconciliation history.", "/dashboard/reconciliation/", "secondary"))

    next_action_cards = "".join(
        f"""
        <a class="next-action-card next-action-{escape(tone)}" href="{escape(href)}">
          <span>{escape(kicker)}</span>
          <strong>{escape(title)}</strong>
          <p>{escape(description)}</p>
        </a>
        """
        for kicker, title, description, href, tone in next_actions[:4]
    )

    workflow_lanes = [
        ("01", "Start", "Business setup", "Choose business type, create profile, add logo, contact details, payment note and opening chart.", "/dashboard/business-profile/"),
        ("02", "Sell", "Invoice and collect", "Create invoice, send PDF or WhatsApp reminder, record partial/final payment against invoice.", "/dashboard/invoices/new/"),
        ("03", "Buy", "Expenses and AP", "Record paid expenses, upload bills, post vendor bills, pay selected bill and keep attachments.", "/dashboard/expenses/new/"),
        ("04", "Stock", "Inventory", "Create products or services, post stock inward/outward and keep FIFO cost layers.", "/dashboard/inventory/"),
        ("05", "Correct", "Audit-safe corrections", "Use credit notes, debit notes and reversals instead of deleting posted accounting records.", "/dashboard/search/"),
        ("06", "Control", "Reports and reconcile", "Review P&L, AR/AP aging, cash, trial balance, tax summary, audit trail and bank reconciliation.", "/dashboard/reports/"),
    ]
    workflow_lane_cards = "".join(
        f"""
        <a class="workflow-lane-card" href="{escape(href)}">
          <span>{escape(number)} / {escape(kicker)}</span>
          <strong>{escape(title)}</strong>
          <p>{escape(description)}</p>
        </a>
        """
        for number, kicker, title, description, href in workflow_lanes
    )

    invoice_rows = []
    for invoice in invoices:
        inv_number = invoice_number(invoice)
        received = invoice_amount_received(invoice)
        credited = invoice_amount_credited(invoice)
        balance = invoice_outstanding_amount(invoice)
        payment_action = f'<a class="button ghost" href="/dashboard/payments/new/?invoice={invoice.id}">Record payment</a>' if balance > 0 else ""
        credit_action = f'<a class="button ghost" href="/dashboard/invoices/{invoice.id}/credit-notes/new/">Credit note</a>' if balance > 0 else ""
        doc_label = document_type_label(invoice)
        if invoice.document_type == "quotation":
            convert_action = (
                f'<form method="post" action="/dashboard/invoices/{invoice.id}/convert/" style="display:inline-block">{csrf_input(request)}<input type="hidden" name="target" value="proforma" /><button class="button ghost" type="submit">To proforma</button></form>'
                f'<form method="post" action="/dashboard/invoices/{invoice.id}/convert/" style="display:inline-block">{csrf_input(request)}<input type="hidden" name="target" value="tax_invoice" /><button class="button secondary" type="submit">Convert to invoice</button></form>'
            )
        elif invoice.document_type == "proforma":
            convert_action = f'<form method="post" action="/dashboard/invoices/{invoice.id}/convert/" style="display:inline-block">{csrf_input(request)}<input type="hidden" name="target" value="tax_invoice" /><button class="button secondary" type="submit">Convert to invoice</button></form>'
        else:
            convert_action = ""
        invoice_rows.append(
            f"""
            <article class="dashboard-card invoice-card">
              <div>
                <div class="card-meta-row"><span>{escape(doc_label)}</span><strong class="status-pill status-{escape(invoice.status)}">{escape(invoice.get_status_display())}</strong></div>
                <h2>{escape(inv_number)}</h2>
                <p><strong>{escape(invoice.client_name)}</strong><br />{escape(invoice.service_name)}<br />Total {escape(invoice_total_display(invoice))} / Received {escape(money(received, invoice.currency_symbol))} / Credited {escape(money(credited, invoice.currency_symbol))} / Balance {escape(money(balance, invoice.currency_symbol))}<br />{invoice.created_at:%d %b %Y}</p>
              </div>
              <div class="dashboard-actions">
                <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/" target="_blank" rel="noopener">Open invoice</a>
                <a class="button secondary" href="/invoice/{escape(invoice.public_token)}/download.pdf">PDF</a>
                <a class="button ghost" href="/dashboard/invoices/{invoice.id}/accounting/">Accounting</a>
                <a class="button ghost" href="/dashboard/ledger/customers/?customer={quote_plus(invoice.client_name)}">Statement</a>
                <a class="button ghost" href="/dashboard/invoices/{invoice.id}/edit/">Edit</a>
                <a class="button ghost" href="{escape(whatsapp_url(invoice.invoice_text))}" target="_blank" rel="noopener">WhatsApp</a>
                {payment_action}
                {credit_action}
                {convert_action}
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
            <p>Type: {escape(business_profile.get_business_type_display())}</p>
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
          <div class="dashboard-actions">
            <a class="button secondary" href="/dashboard/ledger/customers/?customer={quote_plus(client.name)}">Statement</a>
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
              <div class="dashboard-actions">
                <a class="button secondary" href="/dashboard/accounting/journal/{entry.id}/">Open journal</a>
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
              <div class="dashboard-actions">
                <a class="button secondary" href="/dashboard/payments/{receipt.id}/">Open receipt</a>
                <a class="button secondary" href="/dashboard/payments/{receipt.id}/receipt.pdf">Receipt PDF</a>
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
        paid = vendor_bill_amount_paid(bill)
        balance = vendor_bill_outstanding_amount(bill)
        bill_action = f'<a class="button secondary" href="/dashboard/expenses/pay/?bill={bill.id}">Pay bill</a>' if balance > 0 else ""
        voucher_ref = bill.voucher.voucher_number if bill.voucher else ""
        payment_ref = bill.payment_voucher.voucher_number if bill.payment_voucher else ""
        bill_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(bill.get_status_display())}</span>
                <h2>{escape(bill.vendor_name)}</h2>
                <p>{escape(bill.category)} - Total {escape(money(bill.amount, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))} / Paid {escape(money(paid, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))} / Balance {escape(money(balance, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</p>
                {f'<p>Due {bill.due_date:%d %b %Y}</p>' if bill.due_date else ''}
                {f'<p>Voucher: {escape(voucher_ref)}</p>' if voucher_ref else ''}
                {f'<p>Paid by: {escape(payment_ref)}</p>' if payment_ref else ''}
              </div>
              <div class="dashboard-actions">
                <a class="button secondary" href="/dashboard/expenses/{bill.id}/">Open bill</a>
                <a class="button secondary" href="/dashboard/ledger/vendors/?vendor={quote_plus(bill.vendor_name)}">Statement</a>
                {bill_action}
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

    inventory_rows = []
    for item, quantity in stock_snapshots:
        status = stock_status(item, quantity)
        inventory_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(status)}</span>
                <h2>{escape(item.name)}</h2>
                <p>{escape(item.sku or item.get_item_type_display())}<br />Stock: {escape(format_quantity(quantity))} {escape(item.unit)} / Reorder: {escape(format_quantity(item.reorder_level))}</p>
              </div>
            </article>
            """
        )
    if not inventory_rows:
        inventory_rows.append(
            """
            <article class="dashboard-card empty-state compact-card">
              <span>Inventory</span>
              <h2>No inventory items yet</h2>
              <p>Add products, raw materials, finished goods, travel packages or service catalog items.</p>
            </article>
            """
        )

    display_name = request.user.first_name or email
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero">
        <p class="eyebrow">Finance command center</p>
        <h1>Welcome, {escape(display_name)}.</h1>
        <p>Run daily billing, collections, expenses, inventory, accounting reports and audit-safe corrections from one clean workspace connected to {escape(email)}.</p>
        {notice}
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/invoices/new/">Create invoice</a>
          <a class="button secondary" href="/dashboard/payments/new/">Record payment</a>
          <a class="button secondary" href="/dashboard/expenses/new/">Record expense</a>
          <a class="button secondary" href="/dashboard/expenses/upload/">Upload bill photo</a>
          <a class="button secondary" href="/dashboard/ai/">Open AI assistant</a>
          <a class="button secondary" href="/dashboard/vouchers/new/">Post voucher</a>
          <a class="button secondary" href="/dashboard/inventory/">Manage inventory</a>
        </div>
      </section>
      <section class="next-action-grid" aria-label="Next best actions">
        <div class="section-head next-action-head">
          <p class="eyebrow">Next best actions</p>
          <h2>What to do now</h2>
          <p>RozLedger points customers toward the next practical step instead of making them search through accounting menus.</p>
        </div>
        {next_action_cards}
      </section>
      <section class="workflow-map" aria-label="Workflow map">
        <div class="section-head">
          <p class="eyebrow">Workflow map</p>
          <h2>From setup to reports</h2>
          <p>A simple operating path for service, trading, manufacturing, travel and other small-business accounts.</p>
        </div>
        <div class="workflow-lane-grid">{workflow_lane_cards}</div>
        <div class="dashboard-actions section-actions">
          <a class="button secondary" href="/dashboard/workflows/">Open workflow guide</a>
          <a class="button secondary" href="/dashboard/audit/">Audit trail</a>
          <a class="button secondary" href="/dashboard/search/">Search records</a>
        </div>
      </section>
      <section class="dashboard-module-grid" aria-label="Daily workflow">
        <a class="module-tile" href="/dashboard/ai/"><span>01</span><strong>AI assistant</strong><p>Create invoices, categorize expenses, match payments and summarize your books.</p></a>
        <a class="module-tile" href="/dashboard/invoices/new/"><span>02</span><strong>Create invoice</strong><p>Bill customers with saved business and client details.</p></a>
        <a class="module-tile" href="/dashboard/payments/new/"><span>03</span><strong>Collect payment</strong><p>Select customer and invoice, then post the receipt.</p></a>
        <a class="module-tile" href="/dashboard/expenses/new/"><span>04</span><strong>Record expense</strong><p>Track paid expenses and unpaid vendor bills.</p></a>
        <a class="module-tile" href="/dashboard/vouchers/new/"><span>05</span><strong>Voucher engine</strong><p>Post purchase and sales vouchers with FIFO stock costing.</p></a>
        <a class="module-tile" href="/dashboard/inventory/"><span>06</span><strong>Inventory</strong><p>Manage products, services, raw materials, stock inward and stock outward.</p></a>
        <a class="module-tile" href="/dashboard/setup/"><span>07</span><strong>Business setup</strong><p>Choose your business type and apply required accounting defaults.</p></a>
        <a class="module-tile" href="/dashboard/reports/"><span>08</span><strong>View reports</strong><p>Check profit, receivables, payables and cash position.</p></a>
        <a class="module-tile" href="/dashboard/ledger/customers/"><span>09</span><strong>Customer ledger</strong><p>View invoices, receipts, partial payments and customer balances.</p></a>
        <a class="module-tile" href="/dashboard/ledger/vendors/"><span>10</span><strong>Vendor ledger</strong><p>View bills, payments, partial payments and vendor balances.</p></a>
      </section>
      <section class="dashboard-summary" aria-label="Account summary">
        <div><span>Pending invoices</span><strong>{pending_count}</strong></div>
        <div><span>Paid invoices</span><strong>{paid_count}</strong></div>
        <div><span>Saved clients</span><strong>{Client.objects.filter(account_q(request)).count()}</strong></div>
        <div><span>Plan</span><strong>{escape(subscription_title)}</strong></div>
        <div><span>Monthly invoices</span><strong>{quota_used}/{quota_limit}</strong></div>
        <div><span>Accounts receivable</span><strong>{escape(money(finance_summary['accounts_receivable'], '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</strong></div>
        <div><span>Accounts payable</span><strong>{escape(money(finance_summary['accounts_payable'], '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</strong></div>
        <div><span>Inventory items</span><strong>{InventoryItem.objects.filter(account_q(request), is_active=True).count()}</strong></div>
        <div><span>Low stock</span><strong>{low_stock_count}</strong></div>
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
          <a class="button secondary" href="/dashboard/expenses/pay/">Pay vendor bill</a>
          <a class="button secondary" href="/dashboard/expenses/upload/">Upload bill photo</a>
        </div>
        <div class="dashboard-grid">{''.join(bill_rows)}</div>
      </section>
      <section class="dashboard-section" id="inventory">
        <div class="section-head">
          <p class="eyebrow">Inventory</p>
          <h2>Products, services and stock</h2>
          <p>Use inventory for trading goods, raw materials, finished goods, consumables, travel packages and repeatable service catalog items.</p>
        </div>
        <div class="dashboard-actions section-actions">
          <a class="button primary" href="/dashboard/inventory/">Manage inventory</a>
        </div>
        <div class="dashboard-grid">{''.join(inventory_rows)}</div>
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
@require_GET
def workflow_guide(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    us_market = is_us_host(request)
    currency = "$" if us_market else RUPEE_SYMBOL
    profile = get_business_profile(request)
    finance = finance_summary_for_user(request)
    invoice_count = Invoice.objects.filter(account_q(request)).count()
    bill_count = VendorBill.objects.filter(account_q(request)).count()
    receipt_count = PaymentReceipt.objects.filter(account_q(request)).count()
    reconciliation_count = ReconciliationSession.objects.filter(account_q(request)).count()
    profile_status = "Ready" if profile and profile.business_address and (profile.bank_details or profile.upi_link) else "Needs setup"
    tax_copy = "Sales tax and payment links" if us_market else "GST and UPI"
    cards = [
        (
            "01",
            "Setup",
            "Business profile and market rules",
            f"Save logo, business address, phone, bank/payment details, thank-you note and default template. Market copy stays separate for {'US' if us_market else 'India'} users.",
            profile_status,
            "/dashboard/business-profile/",
            "Open profile",
        ),
        (
            "02",
            "Sales",
            "Invoice to receipt",
            f"Create professional invoices with quantity and rate, send PDF, then record partial or full payment against the selected invoice. {tax_copy} labels are handled by market.",
            f"{invoice_count} invoice(s), {receipt_count} receipt(s)",
            "/dashboard/invoices/new/",
            "Create invoice",
        ),
        (
            "03",
            "Purchases",
            "Expense, bill and vendor payment",
            "Record paid expenses, unpaid vendor bills, upload bill photos, confirm extracted details and pay selected vendor bills without losing the audit trail.",
            f"{bill_count} bill(s)",
            "/dashboard/expenses/new/",
            "Record bill",
        ),
        (
            "04",
            "Inventory",
            "Products, services and FIFO stock",
            "Create products, raw materials, finished goods, travel packages or service items, then post stock inward/outward and keep FIFO cost tracking.",
            f"{InventoryItem.objects.filter(account_q(request), is_active=True).count()} item(s)",
            "/dashboard/inventory/",
            "Manage stock",
        ),
        (
            "05",
            "Corrections",
            "Credit notes, debit notes and reversals",
            "Correct posted documents with accounting documents instead of deleting history. This keeps customer, vendor and journal records explainable.",
            f"AR {money(finance['accounts_receivable'], currency)} / AP {money(finance['accounts_payable'], currency)}",
            "/dashboard/search/",
            "Find records",
        ),
        (
            "06",
            "Control",
            "Reports, audit trail and reconciliation",
            "Review P&L, AR aging, AP aging, cash summary, trial balance, balance sheet, tax summary, audit log and bank/cash reconciliation.",
            f"{reconciliation_count} reconciliation(s)",
            "/dashboard/reports/",
            "Open reports",
        ),
    ]
    card_html = "".join(
        f"""
        <article class="workflow-detail-card">
          <div>
            <span>{escape(number)} / {escape(kicker)}</span>
            <h2>{escape(title)}</h2>
            <p>{escape(description)}</p>
          </div>
          <div class="workflow-card-footer">
            <strong>{escape(status)}</strong>
            <a class="button secondary" href="{escape(href)}">{escape(action)}</a>
          </div>
        </article>
        """
        for number, kicker, title, description, status, href, action in cards
    )
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero">
        <p class="eyebrow">Workflow guide</p>
        <h1>Run the business from one clean accounting path.</h1>
        <p>Use this screen as the customer-friendly operating guide: setup first, bill customers, record purchases, manage inventory, correct safely and review reports.</p>
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/invoices/new/">Create invoice</a>
          <a class="button secondary" href="/dashboard/payments/new/">Record receipt</a>
          <a class="button secondary" href="/dashboard/expenses/new/">Record expense</a>
          <a class="button secondary" href="/dashboard/reconciliation/">Reconcile</a>
        </div>
      </section>
      <section class="workflow-detail-grid" aria-label="RozLedger workflow steps">
        {card_html}
      </section>
      <section class="trust-panel" aria-label="Trust and control">
        <div>
          <p class="eyebrow">Trust controls</p>
          <h2>Built for real records, not mock billing.</h2>
          <p>Posted receipts, bills, vouchers, journal entries and correction documents remain traceable through audit pages and detail screens.</p>
        </div>
        <ul class="feature-list">
          <li>Invoice and customer records are saved per account and market.</li>
          <li>Payments are posted against selected invoices; vendor payments are posted against selected bills.</li>
          <li>Credit notes, debit notes and reversals preserve a clean audit trail.</li>
          <li>Reports read from posted accounting entries so daily work updates finance views.</li>
        </ul>
      </section>
    </main>
    """
    return page_shell("Workflow guide", body, request)


@login_required
@require_GET
def monitoring(request: HttpRequest) -> HttpResponse:
    if not request.user.is_staff:
        raise Http404("Monitoring not found")

    now = timezone.now()
    market = current_market(request)
    currency = "$" if market == "US" else RUPEE_SYMBOL
    db_ok = False
    db_message = "Database check failed"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_ok = True
        db_message = "Database connection OK"
    except Exception as exc:
        db_message = f"Database check failed: {type(exc).__name__}"

    recent_invoices = Invoice.objects.filter(market=market, created_at__gte=now - timedelta(days=1)).count()
    recent_leads = Lead.objects.filter(market=market, created_at__gte=now - timedelta(days=1)).count()
    pending_pro = PlanSubscription.objects.filter(market=market, status="requested").count()
    open_ar = finance_summary_for_user(request)["accounts_receivable"]
    open_ap = finance_summary_for_user(request)["accounts_payable"]
    gateways = PaymentGatewayConfig.objects.filter(market=market, enabled=True).order_by("-updated_at")
    gateway_copy = ", ".join(f"{gateway.get_gateway_display()} {gateway.get_mode_display()}" for gateway in gateways) or "No enabled gateway for this market"
    recent_audits = AuditLog.objects.filter(market=market).order_by("-created_at")[:8]
    audit_rows = "".join(
        f"""
        <tr>
          <td>{audit.created_at:%d %b %Y %H:%M}</td>
          <td>{escape(audit.owner_email)}</td>
          <td>{escape(audit.action)}</td>
          <td>{escape(audit.object_type)} #{escape(audit.object_id)}</td>
        </tr>
        """
        for audit in recent_audits
    ) or '<tr><td colspan="4" class="empty-report-row">No audit events yet.</td></tr>'
    endpoint_rows = "".join(
        f"""
        <tr>
          <td><strong>{escape(label)}</strong><span>{escape(url)}</span></td>
          <td>{escape(expected)}</td>
          <td><a class="button secondary" href="{escape(url)}" target="_blank" rel="noopener">Open</a></td>
        </tr>
        """
        for label, url, expected in [
            ("India home", "https://rozledger.in/", "200"),
            ("India pricing", "https://rozledger.in/pricing/", "200"),
            ("India login", "https://rozledger.in/accounts/login/", "200"),
            ("US home", "https://rozledger.com/", "200"),
            ("US pricing", "https://rozledger.com/pricing/", "200"),
            ("Health API", "https://rozledger.in/api/health", "200 JSON ok=true"),
            ("Dashboard auth guard", "https://rozledger.in/dashboard/", "302 to login when logged out"),
        ]
    )
    readiness_cards = [
        ("Uptime", "External monitor", "Create checks for rozledger.in, rozledger.com, /api/health, login and pricing. Recommended interval: 5 minutes.", "Manual setup required"),
        ("Errors", "Application error alerts", "Connect Sentry or GlitchTip DSN in the VPS environment and alert support email for server errors.", "Manual setup required"),
        ("Backups", "Database backup alert", "Current deploy script writes MySQL backups before deploy. Add daily backup success/failure alert to email or chat.", "Manual setup required"),
        ("Email", "Transactional email", "Verify Brevo/Titan delivery for password reset, Pro request, approval, invoice and receipt emails.", "Needs final provider check"),
        ("Payments", "Gateway monitoring", "Watch Razorpay for India and Stripe/PayPal for US webhooks, failed payments and subscription status changes.", gateway_copy),
        ("Security", "Admin protection", "Use strong admin passwords, limit staff accounts, review audit trail and keep customer data isolated by owner and market.", "Active process"),
    ]
    readiness_html = "".join(
        f"""
        <article class="workflow-detail-card monitoring-card">
          <div>
            <span>{escape(kicker)}</span>
            <h2>{escape(title)}</h2>
            <p>{escape(description)}</p>
          </div>
          <div class="workflow-card-footer"><strong>{escape(status)}</strong></div>
        </article>
        """
        for kicker, title, description, status in readiness_cards
    )
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero">
        <p class="eyebrow">Staff monitoring</p>
        <h1>Launch operations center.</h1>
        <p>Use this staff-only screen to check core app health, beta activity, public endpoints and remaining monitoring setup tasks. It does not show API keys or payment secrets.</p>
        <div class="hero-actions">
          <a class="button primary" href="/api/health" target="_blank" rel="noopener">Open health API</a>
          <a class="button secondary" href="/dashboard/audit/">Audit trail</a>
          <a class="button secondary" href="/admin/">Django admin</a>
          <a class="button secondary" href="/dashboard/reports/">Reports</a>
        </div>
      </section>
      <section class="report-kpi-grid" aria-label="Monitoring summary">
        <article><span>Database</span><strong>{escape('OK' if db_ok else 'Check')}</strong></article>
        <article><span>24h invoices</span><strong>{recent_invoices}</strong></article>
        <article><span>24h leads</span><strong>{recent_leads}</strong></article>
        <article><span>Pending Pro</span><strong>{pending_pro}</strong></article>
      </section>
      <section class="report-kpi-grid" aria-label="Finance watch">
        <article><span>Market</span><strong>{escape(market)}</strong></article>
        <article><span>AR exposure</span><strong>{escape(money(open_ar, currency))}</strong></article>
        <article><span>AP exposure</span><strong>{escape(money(open_ap, currency))}</strong></article>
        <article><span>Gateway</span><strong>{escape(gateway_copy)}</strong></article>
      </section>
      <section class="trust-panel monitoring-status-panel">
        <div>
          <p class="eyebrow">System status</p>
          <h2>{escape(db_message)}</h2>
          <p>Last checked at {now:%d %b %Y %H:%M %Z}. Health API should return JSON with <strong>ok=true</strong>.</p>
        </div>
        <ul class="feature-list">
          <li>Keep production secrets in VPS environment variables or encrypted admin fields only.</li>
          <li>Do not store payment secrets in public files, GitHub, or frontend JavaScript.</li>
          <li>Review this screen after deployment and before inviting beta users.</li>
          <li>Use external uptime monitoring for real alerts; this page is the internal operator view.</li>
        </ul>
      </section>
      <section class="workflow-detail-grid" aria-label="Monitoring setup checklist">
        {readiness_html}
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Public checks</p>
          <h2>Endpoints to monitor externally</h2>
        </div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Endpoint</th><th>Expected</th><th>Action</th></tr></thead>
            <tbody>{endpoint_rows}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Recent audit trail</p>
          <h2>Latest staff/customer activity for this market</h2>
        </div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Object</th></tr></thead>
            <tbody>{audit_rows}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Monitoring", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def ai_assistant(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    profile = get_business_profile(request)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    message = ""
    error = ""
    suggestion_html = ""
    prompt = clean_text(request.POST.get("prompt"), max_length=1000) if request.method == "POST" else ""
    action = clean_text(request.POST.get("action"), "analyze", 30) if request.method == "POST" else "analyze"

    if request.method == "POST" and action == "apply_setup":
        business_type = clean_text(request.POST.get("business_type"), "service", 30)
        if business_type not in BUSINESS_TYPE_PRESETS:
            business_type = "other"
        if profile:
            profile.business_type = business_type
            profile.owner = request.user
            profile.save(update_fields=["business_type", "owner", "updated_at"])
        else:
            profile = BusinessProfile.objects.create(
                market=current_market(request),
                owner=request.user,
                owner_email=current_account_email(request),
                business_type=business_type,
                business_name=request.user.first_name or current_account_email(request).split("@", 1)[0],
            )
        added = apply_business_type_accounts(request, business_type)
        audit_log(request, "ai.setup_applied", "BusinessProfile", profile.id, f"AI applied {business_type_preset(business_type)['label']} setup")
        message = f"AI setup applied for {business_type_preset(business_type)['label']}. {added} account(s) added."

    elif request.method == "POST" and action == "create_invoice":
        parsed = {
            "client_name": clean_text(request.POST.get("client_name"), "Client", 180),
            "description": clean_text(request.POST.get("description"), "Service", 240),
            "quantity": decimal_value(request.POST.get("quantity")) or Decimal("1"),
            "rate": decimal_value(request.POST.get("rate")),
            "due_days": int(digits_only(clean_text(request.POST.get("due_days"), "7", 4)) or 7),
            "gst_rate": decimal_value(request.POST.get("gst_rate")),
        }
        subtotal = (parsed["quantity"] * parsed["rate"]).quantize(Decimal("0.01"))
        include_tax = parsed["gst_rate"] > 0
        if subtotal <= 0:
            error = "AI invoice needs a quantity and rate greater than zero."
        else:
            quota_used, quota_limit, quota_plan = invoice_quota_for_email(current_account_email(request), current_market(request))
            if quota_used >= quota_limit:
                error = invoice_quota_message(quota_used, quota_limit, quota_plan)
            else:
                tax_label = "Sales tax" if is_us_host(request) else "GST"
                invoice = Invoice.objects.create(
                    market=current_market(request),
                    owner=request.user,
                    owner_email=current_account_email(request),
                    template=profile.template if profile else "classic",
                    accent_color=profile.accent_color if profile else "#126b4f",
                    business_name=profile.business_name if profile else request.user.first_name or "Your business",
                    business_phone=profile.business_phone if profile else "",
                    business_address=profile.business_address if profile else "",
                    client_name=parsed["client_name"],
                    client_phone="",
                    client_address="",
                    client_gstin="",
                    service_name=parsed["description"],
                    include_gst=include_tax,
                    amount_before_gst=subtotal,
                    gst_rate=parsed["gst_rate"] if include_tax else Decimal("0"),
                    tax_label=tax_label,
                    currency_symbol=currency,
                    due_days=parsed["due_days"],
                    total_text=invoice_total_text(subtotal, parsed["gst_rate"], include_tax, currency),
                    upi_link=profile.upi_link if profile else "",
                    bank_details=profile.bank_details if profile else "",
                    thank_you_note=profile.thank_you_note if profile else "Thank you for your business.",
                    invoice_text="",
                )
                save_invoice_line_items(invoice, [{"description": parsed["description"], "quantity": parsed["quantity"], "rate": parsed["rate"]}])
                invoice.invoice_text = build_invoice_text(invoice)
                invoice.save(update_fields=["invoice_text", "updated_at"])
                post_invoice_sales_voucher(request, invoice)
                save_client_from_invoice(invoice, request.user)
                audit_log(request, "ai.invoice_created", "Invoice", invoice.id, f"AI created invoice for {invoice.client_name}")
                message = f"Invoice {invoice_number(invoice)} created for {invoice.client_name}."

    elif request.method == "POST" and action == "record_expense":
        amount = decimal_value(request.POST.get("amount"))
        vendor_name = clean_text(request.POST.get("vendor_name"), "Vendor", 180)
        category = clean_text(request.POST.get("category"), "Office expenses", 180)
        status = clean_text(request.POST.get("status"), "paid", 20)
        payment_method = clean_text(request.POST.get("payment_method"), "bank", 20)
        account_id = clean_text(request.POST.get("expense_account"), max_length=20)
        expense_account = Account.objects.filter(account_q(request), id=account_id, account_type="expense").first() or expense_account_for_text(request, category)
        if amount <= 0:
            error = "AI expense needs an amount greater than zero."
        else:
            bill = post_expense_bill(
                request,
                bill_date=timezone.localdate(),
                vendor_name=vendor_name,
                category=category,
                amount=amount,
                status=status,
                payment_method=payment_method,
                expense_account=expense_account,
                reference="AI assistant",
                notes=prompt,
                source_prefix="ai",
            )
            audit_log(request, "ai.expense_created", "VendorBill", bill.id, f"AI recorded {status} expense for {bill.vendor_name}")
            message = f"Expense recorded for {vendor_name}: {money(amount, currency)}."

    elif request.method == "POST" and action == "record_payment":
        invoice_id = clean_text(request.POST.get("invoice_id"), max_length=20)
        try:
            invoice = owned_invoice(request, int(invoice_id))
        except (ValueError, Http404):
            invoice = None
        amount = decimal_value(request.POST.get("amount"))
        if invoice and amount <= 0:
            amount = invoice_total_amount(invoice)
        method = clean_text(request.POST.get("method"), "bank", 20)
        if invoice is None:
            error = "Select a valid invoice before AI records payment."
        elif amount <= 0:
            error = "Payment amount must be greater than zero."
        else:
            try:
                receipt = post_customer_receipt(
                    request,
                    invoice=invoice,
                    payment_date=timezone.localdate(),
                    payer_name=invoice.client_name,
                    amount=amount,
                    method=method,
                    reference=f"AI match {invoice_number(invoice)}",
                    notes=prompt,
                )
                audit_log(request, "ai.payment_matched", "PaymentReceipt", receipt.id, f"AI matched payment to {invoice_number(invoice)}")
                message = f"Payment matched and posted to {invoice_number(invoice)}."
            except ValueError as exc:
                error = str(exc)

    if request.method == "POST" and action == "analyze":
        lowered = prompt.lower()
        if any(keyword in lowered for keyword in ["setup", "business type", "i run", "we run", "start", "profile"]):
            business_type = detect_business_type(prompt)
            preset = business_type_preset(business_type)
            suggestion_html = f"""
            <article class="ai-suggestion-card">
              <span>AI setup assistant</span>
              <h2>{escape(preset['label'])}</h2>
              <p>{escape(preset['summary'])}</p>
              <form method="post" class="inline-form">
                {csrf_input(request)}
                <input type="hidden" name="action" value="apply_setup" />
                <input type="hidden" name="business_type" value="{escape(business_type)}" />
                <button class="button primary" type="submit">Apply this setup</button>
              </form>
            </article>
            """
        elif any(keyword in lowered for keyword in ["invoice", "bill customer", "create bill"]):
            parsed = ai_parse_invoice_prompt(prompt)
            subtotal = (parsed["quantity"] * parsed["rate"]).quantize(Decimal("0.01"))
            total = invoice_total_text(subtotal, parsed["gst_rate"], parsed["gst_rate"] > 0, currency)
            suggestion_html = f"""
            <article class="ai-suggestion-card">
              <span>AI invoice creator</span>
              <h2>{escape(parsed['client_name'])}</h2>
              <p>{escape(parsed['description'])}: {escape(format_quantity(parsed['quantity']))} x {escape(money(parsed['rate'], currency))}. Estimated total {escape(total)}.</p>
              <form method="post" class="ai-approval-form">
                {csrf_input(request)}
                <input type="hidden" name="action" value="create_invoice" />
                <input type="hidden" name="client_name" value="{escape(parsed['client_name'])}" />
                <input type="hidden" name="description" value="{escape(parsed['description'])}" />
                <input type="hidden" name="quantity" value="{escape(format_quantity(parsed['quantity']))}" />
                <input type="hidden" name="rate" value="{escape(str(parsed['rate']))}" />
                <input type="hidden" name="due_days" value="{escape(str(parsed['due_days']))}" />
                <input type="hidden" name="gst_rate" value="{escape(str(parsed['gst_rate']))}" />
                <button class="button primary" type="submit">Create invoice</button>
                <a class="button secondary" href="/dashboard/invoices/new/">Edit in invoice builder</a>
              </form>
            </article>
            """
        elif any(keyword in lowered for keyword in ["expense", "paid", "bill from", "vendor", "purchase"]):
            parsed = ai_parse_expense_prompt(request, prompt)
            suggestion_html = f"""
            <article class="ai-suggestion-card">
              <span>AI expense categorizer</span>
              <h2>{escape(parsed['vendor_name'])}</h2>
              <p>Category: {escape(parsed['category'])}. Account: {escape(parsed['expense_account'].code)} - {escape(parsed['expense_account'].name)}. Amount: {escape(money(parsed['amount'], currency))}.</p>
              <form method="post" class="ai-approval-form">
                {csrf_input(request)}
                <input type="hidden" name="action" value="record_expense" />
                <input type="hidden" name="vendor_name" value="{escape(parsed['vendor_name'])}" />
                <input type="hidden" name="category" value="{escape(parsed['category'])}" />
                <input type="hidden" name="amount" value="{escape(str(parsed['amount']))}" />
                <input type="hidden" name="status" value="{escape(parsed['status'])}" />
                <input type="hidden" name="payment_method" value="{escape(parsed['payment_method'])}" />
                <input type="hidden" name="expense_account" value="{escape(str(parsed['expense_account'].id))}" />
                <input type="hidden" name="prompt" value="{escape(prompt)}" />
                <button class="button primary" type="submit">Record expense</button>
                <a class="button secondary" href="/dashboard/expenses/new/">Edit expense form</a>
              </form>
            </article>
            """
        elif any(keyword in lowered for keyword in ["received", "payment", "paid by", "collected"]):
            parsed = ai_parse_payment_prompt(prompt)
            matches = ai_match_payment_invoices(request, parsed["payer_name"], parsed["amount"])
            if matches:
                match_rows = []
                for invoice in matches:
                    match_rows.append(
                        f"""
                        <form method="post" class="ai-match-row">
                          {csrf_input(request)}
                          <input type="hidden" name="action" value="record_payment" />
                          <input type="hidden" name="invoice_id" value="{invoice.id}" />
                          <input type="hidden" name="amount" value="{escape(str(parsed['amount'] or invoice_total_amount(invoice)))}" />
                          <input type="hidden" name="method" value="{escape(parsed['method'])}" />
                          <input type="hidden" name="prompt" value="{escape(prompt)}" />
                          <div><strong>{escape(invoice_number(invoice))}</strong><p>{escape(invoice.client_name)} - {escape(invoice_total_display(invoice))}</p></div>
                          <button class="button primary" type="submit">Post payment</button>
                        </form>
                        """
                    )
                suggestion_html = f"""
                <article class="ai-suggestion-card">
                  <span>AI payment matching</span>
                  <h2>Possible invoice matches</h2>
                  <p>Detected payer {escape(parsed['payer_name'] or 'unknown')} and amount {escape(money(parsed['amount'], currency))}.</p>
                  <div class="ai-match-list">{''.join(match_rows)}</div>
                </article>
                """
            else:
                suggestion_html = '<article class="ai-suggestion-card"><span>AI payment matching</span><h2>No matching open invoice found</h2><p>Try including customer name and exact amount, or use the payment form.</p><a class="button secondary" href="/dashboard/payments/new/">Open payment form</a></article>'
        else:
            summary_items = "".join(f"<li>{escape(item)}</li>" for item in ai_dashboard_summary(request))
            suggestion_html = f"""
            <article class="ai-suggestion-card">
              <span>AI dashboard summary</span>
              <h2>Business summary</h2>
              <ul>{summary_items}</ul>
            </article>
            """

    summary_items = "".join(f"<li>{escape(item)}</li>" for item in ai_dashboard_summary(request))
    message_html = f'<p class="form-success">{escape(message)}</p>' if message else ""
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    examples = [
        "I run a travel agency. Set up my accounts.",
        "Create invoice for ABC Travels, 3 Dubai tour packages at 25000 each, GST 18, due in 7 days.",
        "Paid 1800 to Airtel for internet by bank.",
        "Received 5000 from John by UPI.",
        "Show my business summary.",
    ]
    example_html = "".join(f"<button type=\"submit\" name=\"prompt\" value=\"{escape(example)}\">{escape(example)}</button>" for example in examples)
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero ai-hero">
        <p class="eyebrow">RozLedger AI</p>
        <h1>Ask, review, approve.</h1>
        <p>Use plain English for setup, invoices, expenses, summaries and payment matching. AI suggestions are never posted until you approve them.</p>
        {message_html}{error_html}
        <form method="post" class="ai-command-form">
          {csrf_input(request)}
          <input type="hidden" name="action" value="analyze" />
          <label>What do you want RozLedger AI to do?<textarea name="prompt" rows="4" placeholder="Type a command like: create invoice for ABC, 2 services at 5000 each">{escape(prompt)}</textarea></label>
          <button class="button primary" type="submit">Analyze</button>
        </form>
        <form method="post" class="ai-example-grid">
          {csrf_input(request)}
          <input type="hidden" name="action" value="analyze" />
          {example_html}
        </form>
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Suggestion</p><h2>AI result</h2></div>
        {suggestion_html or '<article class="ai-suggestion-card"><span>Ready</span><h2>Enter a command above</h2><p>RozLedger AI can set up business defaults, draft invoices, categorize expenses, explain dashboard numbers and match payments to invoices.</p></article>'}
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Live summary</p><h2>Current business health</h2></div>
        <article class="ai-suggestion-card"><ul>{summary_items}</ul></article>
      </section>
    </main>
    """
    return page_shell("AI assistant", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def business_setup(request: HttpRequest) -> HttpResponse:
    profile = get_business_profile(request)
    selected_type = profile.business_type if profile else "service"
    message = ""
    if request.method == "POST":
        selected_type = clean_text(request.POST.get("business_type"), "service", 30)
        if selected_type not in BUSINESS_TYPE_PRESETS:
            selected_type = "other"
        if profile:
            profile.business_type = selected_type
            profile.owner = request.user
            profile.save(update_fields=["business_type", "owner", "updated_at"])
        else:
            profile = BusinessProfile.objects.create(
                market=current_market(request),
                owner=request.user,
                owner_email=current_account_email(request),
                business_type=selected_type,
                business_name=request.user.first_name or current_account_email(request).split("@", 1)[0],
            )
        added = apply_business_type_accounts(request, selected_type)
        audit_log(request, "business_setup.applied", "BusinessProfile", profile.id, f"Applied {business_type_preset(selected_type)['label']} setup")
        message = f"Business setup applied. {added} account(s) added to your chart."

    preset = business_type_preset(selected_type)
    preset_cards = []
    for key, candidate in BUSINESS_TYPE_PRESETS.items():
        preset_cards.append(
            f"""
            <article class="setup-preset-card {'selected' if key == selected_type else ''}">
              <span>{escape(candidate['label'])}</span>
              <p>{escape(candidate['summary'])}</p>
            </article>
            """
        )
    requirement_items = "".join(f"<li>{escape(item)}</li>" for item in preset["requirements"])
    sales_items = "".join(f"<li>{escape(item)}</li>" for item in preset["sales"])
    inventory_items_html = "".join(f"<li>{escape(item)}</li>" for item in preset["inventory"])
    account_items = "".join(f"<li>{escape(code)} - {escape(name)}</li>" for code, name, _account_type, _normal in preset["accounts"]) or "<li>Default chart of accounts only</li>"
    message_html = f'<p class="form-success">{escape(message)}</p>' if message else ""
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Business setup</p>
        <h1>Start with the right modules.</h1>
        <p>Choose the closest business type. RozLedger will show the basic requirements and add useful accounting heads without disturbing existing accounts.</p>
        {message_html}
        <form method="post" class="dashboard-form setup-form">
          {csrf_input(request)}
          <label>Business type<select name="business_type">{business_type_options(selected_type)}</select></label>
          <button class="button primary" type="submit">Apply setup</button>
          <a class="button secondary" href="/dashboard/business-profile/">Edit business profile</a>
        </form>
      </section>
      <section class="setup-preset-grid">{''.join(preset_cards)}</section>
      <section class="setup-detail-grid">
        <article>
          <span>Required basics</span>
          <h2>{escape(preset['label'])}</h2>
          <ul>{requirement_items}</ul>
        </article>
        <article>
          <span>Common invoice/service types</span>
          <h2>Sales setup</h2>
          <ul>{sales_items}</ul>
        </article>
        <article>
          <span>Inventory setup</span>
          <h2>What to track</h2>
          <ul>{inventory_items_html}</ul>
        </article>
        <article>
          <span>Accounts added</span>
          <h2>Chart of accounts</h2>
          <ul>{account_items}</ul>
        </article>
      </section>
    </main>
    """
    return page_shell("Business setup", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def inventory(request: HttpRequest) -> HttpResponse:
    error = ""
    message = ""
    if request.method == "POST":
        action = clean_text(request.POST.get("action"), max_length=30)
        if action == "item":
            name = clean_text(request.POST.get("name"), max_length=180)
            if not name:
                error = "Item name is required."
            else:
                item_type = clean_text(request.POST.get("item_type"), "trading", 30)
                if item_type not in dict(InventoryItem._meta.get_field("item_type").choices):
                    item_type = "trading"
                item = InventoryItem.objects.create(
                    market=current_market(request),
                    owner=request.user,
                    owner_email=current_account_email(request),
                    sku=clean_text(request.POST.get("sku"), max_length=80),
                    name=name,
                    category=clean_text(request.POST.get("category"), max_length=120),
                    item_type=item_type,
                    unit=clean_text(request.POST.get("unit"), "pcs", 30),
                    sales_rate=decimal_value(request.POST.get("sales_rate")),
                    purchase_rate=decimal_value(request.POST.get("purchase_rate")),
                    reorder_level=decimal_value(request.POST.get("reorder_level")),
                    track_inventory=request.POST.get("track_inventory") == "on",
                )
                opening_quantity = decimal_value(request.POST.get("opening_quantity"))
                if item.track_inventory and opening_quantity > 0:
                    StockMovement.objects.create(
                        market=item.market,
                        owner=request.user,
                        owner_email=item.owner_email,
                        item=item,
                        movement_type="opening",
                        quantity=opening_quantity,
                        unit_cost=item.purchase_rate,
                        reference="Opening stock",
                    )
                audit_log(request, "inventory.item_created", "InventoryItem", item.id, f"Created inventory item {item.name}")
                message = "Inventory item saved."
        elif action == "movement":
            item_id = clean_text(request.POST.get("item_id"), max_length=20)
            try:
                item = InventoryItem.objects.get(id=item_id, **{"owner_email": current_account_email(request), "market": current_market(request)})
            except InventoryItem.DoesNotExist:
                error = "Select a valid inventory item."
            else:
                quantity = decimal_value(request.POST.get("quantity"))
                if quantity <= 0:
                    error = "Movement quantity must be greater than zero."
                else:
                    movement_type = clean_text(request.POST.get("movement_type"), "purchase", 30)
                    if movement_type not in dict(StockMovement._meta.get_field("movement_type").choices):
                        movement_type = "purchase"
                    raw_date = clean_text(request.POST.get("movement_date"), max_length=20)
                    movement_date = timezone.localdate()
                    if raw_date:
                        try:
                            movement_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                        except ValueError:
                            movement_date = timezone.localdate()
                    movement = StockMovement.objects.create(
                        market=current_market(request),
                        owner=request.user,
                        owner_email=current_account_email(request),
                        item=item,
                        movement_type=movement_type,
                        movement_date=movement_date,
                        quantity=quantity,
                        unit_cost=decimal_value(request.POST.get("unit_cost")),
                        reference=clean_text(request.POST.get("reference"), max_length=120),
                        notes=clean_text(request.POST.get("notes")),
                    )
                    audit_log(request, "inventory.stock_movement", "StockMovement", movement.id, f"Posted {movement.get_movement_type_display()} for {item.name}")
                    message = "Stock movement posted."

    items = list(InventoryItem.objects.filter(account_q(request), is_active=True).prefetch_related("movements"))
    movements = StockMovement.objects.filter(account_q(request)).select_related("item")[:20]
    item_rows = []
    for item in items:
        quantity = stock_quantity(item)
        item_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(stock_status(item, quantity))}</span>
                <h2>{escape(item.name)}</h2>
                <p>{escape(item.sku or item.get_item_type_display())}<br />{escape(format_quantity(quantity))} {escape(item.unit)} on hand</p>
                <p>Sale: {escape(money(item.sales_rate, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))} / Purchase: {escape(money(item.purchase_rate, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))}</p>
              </div>
            </article>
            """
        )
    if not item_rows:
        item_rows.append('<article class="dashboard-card empty-state compact-card"><span>Inventory</span><h2>No items yet</h2><p>Create your first product, service, raw material, finished good or package item.</p></article>')
    movement_rows = []
    for movement in movements:
        movement_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{movement.movement_date:%d %b %Y}</span>
                <h2>{escape(movement.item.name)}</h2>
                <p>{escape(movement.get_movement_type_display())}: {escape(format_quantity(movement.quantity))} {escape(movement.item.unit)}<br />{escape(movement.reference or 'No reference')}</p>
              </div>
            </article>
            """
        )
    if not movement_rows:
        movement_rows.append('<article class="dashboard-card empty-state compact-card"><span>Stock ledger</span><h2>No stock movement yet</h2><p>Opening stock, purchases, sales, production and adjustments will appear here.</p></article>')

    item_type_options = "".join(f'<option value="{value}">{escape(label)}</option>' for value, label in InventoryItem._meta.get_field("item_type").choices)
    movement_type_options = "".join(f'<option value="{value}">{escape(label)}</option>' for value, label in StockMovement._meta.get_field("movement_type").choices)
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    message_html = f'<p class="form-success">{escape(message)}</p>' if message else ""
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Inventory</p>
        <h1>Products, services and stock ledger.</h1>
        <p>Track trading goods, raw materials, finished goods, consumables, travel packages and repeatable services from one place.</p>
        {error_html}{message_html}
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Item master</p><h2>Add product or service</h2></div>
        <form method="post" class="dashboard-form inventory-form">
          {csrf_input(request)}
          <input type="hidden" name="action" value="item" />
          <label>Name<input name="name" placeholder="Product, raw material, finished good or service" required /></label>
          <label>SKU/code<input name="sku" placeholder="Optional item code" /></label>
          <label>Type<select name="item_type">{item_type_options}</select></label>
          <label>Category<input name="category" placeholder="Materials, finished goods, packages" /></label>
          <label>Unit<input name="unit" value="pcs" placeholder="pcs, kg, hour, package" /></label>
          <label>Sales rate<input name="sales_rate" type="number" min="0" step="0.01" /></label>
          <label>Purchase rate<input name="purchase_rate" type="number" min="0" step="0.01" /></label>
          <label>Opening quantity<input name="opening_quantity" type="number" min="0" step="0.01" /></label>
          <label>Reorder level<input name="reorder_level" type="number" min="0" step="0.01" /></label>
          <label class="checkbox-row"><input name="track_inventory" type="checkbox" checked /> Track stock quantity</label>
          <button class="button primary" type="submit">Save item</button>
        </form>
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Stock movement</p><h2>Post inward, outward or adjustment</h2></div>
        <form method="post" class="dashboard-form inventory-form">
          {csrf_input(request)}
          <input type="hidden" name="action" value="movement" />
          <label>Item<select name="item_id">{inventory_item_options(items)}</select></label>
          <label>Movement<select name="movement_type">{movement_type_options}</select></label>
          <label>Date<input name="movement_date" type="date" value="{timezone.localdate():%Y-%m-%d}" /></label>
          <label>Quantity<input name="quantity" type="number" min="0.01" step="0.01" required /></label>
          <label>Unit cost<input name="unit_cost" type="number" min="0" step="0.01" /></label>
          <label>Reference<input name="reference" placeholder="Invoice, bill, production batch or note" /></label>
          <label class="full-row">Notes<textarea name="notes" rows="2"></textarea></label>
          <button class="button primary" type="submit">Post movement</button>
        </form>
      </section>
      <section class="dashboard-section"><div class="section-head"><p class="eyebrow">Current stock</p><h2>Inventory items</h2></div><div class="dashboard-grid">{''.join(item_rows)}</div></section>
      <section class="dashboard-section"><div class="section-head"><p class="eyebrow">Stock ledger</p><h2>Recent movements</h2></div><div class="dashboard-grid">{''.join(movement_rows)}</div></section>
    </main>
    """
    return page_shell("Inventory", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def voucher_new(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    _unit, default_godown, default_group = ensure_default_inventory_masters(request)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    items = list(InventoryItem.objects.filter(account_q(request), is_active=True).order_by("name"))
    godowns = list(Godown.objects.filter(account_q(request), is_active=True).order_by("name"))
    accounts = list(Account.objects.filter(account_q(request), is_active=True).order_by("code"))
    bank_cash_accounts = [account for account in accounts if account.code in {"1000", "1010"}]
    recent_vouchers = Voucher.objects.filter(account_q(request)).prefetch_related("ledger_lines", "inventory_lines")[:10]
    values = {
        "voucher_type": clean_text(request.GET.get("type"), "sales", 30),
        "voucher_date": f"{timezone.localdate():%Y-%m-%d}",
        "party_name": "",
        "item_id": "",
        "item_name": "",
        "godown_id": str(default_godown.id),
        "quantity": "",
        "rate": "",
        "narration": "",
        "amount": "",
        "primary_account": "",
        "secondary_account": "",
    }
    error = ""
    message = ""
    allowed_vouchers = {"sales", "purchase", "expense", "receipt", "payment", "contra", "journal"}
    if values["voucher_type"] not in allowed_vouchers:
        values["voucher_type"] = "sales"

    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        if values["voucher_type"] not in allowed_vouchers:
            values["voucher_type"] = "sales"
        voucher_date = parse_form_date(values["voucher_date"])
        amount_value = decimal_value(values["amount"])
        primary_account = next((account for account in accounts if str(account.id) == values["primary_account"]), None)
        secondary_account = next((account for account in accounts if str(account.id) == values["secondary_account"]), None)
        quantity = decimal_value(values["quantity"])
        rate = decimal_value(values["rate"])
        amount = (quantity * rate).quantize(Decimal("0.01"))
        item = None
        if values["voucher_type"] in {"sales", "purchase"} and values["item_id"]:
            item = InventoryItem.objects.filter(account_q(request), id=values["item_id"], is_active=True).first()
        if values["voucher_type"] in {"sales", "purchase"} and item is None and values["item_name"]:
            item = InventoryItem.objects.create(
                market=current_market(request),
                owner=request.user,
                owner_email=current_account_email(request),
                stock_group=default_group,
                name=values["item_name"],
                sku="",
                category=default_group.name,
                item_type="trading",
                unit="pcs",
                sales_rate=rate if values["voucher_type"] == "sales" else Decimal("0"),
                purchase_rate=rate if values["voucher_type"] == "purchase" else Decimal("0"),
                reorder_level=Decimal("0"),
                track_inventory=True,
            )
            items.append(item)
        godown = next((candidate for candidate in godowns if str(candidate.id) == values["godown_id"]), default_godown)
        if not values["party_name"]:
            error = "Party name is required."
        elif values["voucher_type"] in {"sales", "purchase"} and item is None:
            error = "Select or create an inventory item."
        elif values["voucher_type"] in {"sales", "purchase"} and (quantity <= 0 or rate <= 0):
            error = "Quantity and rate must be greater than zero."
        elif values["voucher_type"] not in {"sales", "purchase"} and amount_value <= 0:
            error = "Voucher amount must be greater than zero."
        elif values["voucher_type"] not in {"sales", "purchase"} and primary_account is None:
            error = "Choose the main ledger account."
        elif values["voucher_type"] in {"contra", "journal"} and secondary_account is None:
            error = "Choose the second ledger account."
        elif values["voucher_type"] in {"contra", "journal"} and primary_account and secondary_account and primary_account.id == secondary_account.id:
            error = "Debit and credit accounts must be different."
        elif values["voucher_type"] == "receipt" and secondary_account and secondary_account.code not in {"1000", "1010"}:
            error = "Receipt second ledger must be Cash or Bank."
        else:
            try:
                if values["voucher_type"] == "purchase":
                    ledger_lines = [
                        {"account": account_by_code(request, "1210"), "description": f"Inventory purchase - {item.name}", "debit": amount, "credit": Decimal("0")},
                        {"account": account_by_code(request, "2000"), "description": values["party_name"], "debit": Decimal("0"), "credit": amount},
                    ]
                elif values["voucher_type"] == "sales":
                    ledger_lines = [
                        {"account": account_by_code(request, "1100"), "description": values["party_name"], "debit": amount, "credit": Decimal("0")},
                        {"account": account_by_code(request, "4120"), "description": f"Product sale - {item.name}", "debit": Decimal("0"), "credit": amount},
                    ]
                elif values["voucher_type"] == "expense":
                    secondary_account = secondary_account or next((account for account in bank_cash_accounts if account.code == "1010"), None) or account_by_code(request, "1010")
                    ledger_lines = [
                        {"account": primary_account, "description": values["party_name"], "debit": amount_value, "credit": Decimal("0")},
                        {"account": secondary_account, "description": values["party_name"], "debit": Decimal("0"), "credit": amount_value},
                    ]
                elif values["voucher_type"] == "payment":
                    secondary_account = secondary_account or next((account for account in bank_cash_accounts if account.code == "1010"), None) or account_by_code(request, "1010")
                    ledger_lines = [
                        {"account": primary_account, "description": values["party_name"], "debit": amount_value, "credit": Decimal("0")},
                        {"account": secondary_account, "description": values["party_name"], "debit": Decimal("0"), "credit": amount_value},
                    ]
                elif values["voucher_type"] == "receipt":
                    cash_or_bank = secondary_account if secondary_account and secondary_account.code in {"1000", "1010"} else None
                    cash_or_bank = cash_or_bank or next((account for account in bank_cash_accounts if account.code == "1010"), None) or account_by_code(request, "1010")
                    ledger_lines = [
                        {"account": cash_or_bank, "description": values["party_name"], "debit": amount_value, "credit": Decimal("0")},
                        {"account": primary_account, "description": values["party_name"], "debit": Decimal("0"), "credit": amount_value},
                    ]
                elif values["voucher_type"] == "contra":
                    ledger_lines = [
                        {"account": primary_account, "description": "Contra transfer in", "debit": amount_value, "credit": Decimal("0")},
                        {"account": secondary_account, "description": "Contra transfer out", "debit": Decimal("0"), "credit": amount_value},
                    ]
                else:
                    ledger_lines = [
                        {"account": primary_account, "description": values["narration"], "debit": amount_value, "credit": Decimal("0")},
                        {"account": secondary_account, "description": values["narration"], "debit": Decimal("0"), "credit": amount_value},
                    ]
                voucher = create_voucher_with_lines(
                    request,
                    voucher_type=values["voucher_type"],
                    party_name=values["party_name"],
                    narration=values["narration"],
                    voucher_date=voucher_date,
                    ledger_lines=ledger_lines,
                    inventory_lines=[{"item": item, "godown": godown, "quantity": quantity, "rate": rate, "description": item.name}] if values["voucher_type"] in {"sales", "purchase"} else [],
                )
                audit_log(request, "voucher.posted", "Voucher", voucher.id, f"Posted {voucher.get_voucher_type_display()} {voucher.voucher_number}")
                message = f"{voucher.get_voucher_type_display()} voucher {voucher.voucher_number} posted."
                values.update({"party_name": "", "quantity": "", "rate": "", "amount": "", "narration": "", "item_id": str(item.id) if item else ""})
            except ValueError as exc:
                error = str(exc)

    item_cards = []
    for item in items[:12]:
        quantity = stock_quantity(item)
        item_cards.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(stock_status(item, quantity))}</span>
                <h2>{escape(item.name)}</h2>
                <p>{escape(format_quantity(quantity))} {escape(item.unit)} on hand<br />FIFO value {escape(money(fifo_stock_value(item), currency))}</p>
              </div>
            </article>
            """
        )
    if not item_cards:
        item_cards.append('<article class="dashboard-card empty-state compact-card"><span>Stock item</span><h2>No items yet</h2><p>Create an item from the voucher screen by entering item name.</p></article>')

    voucher_rows = []
    for voucher in recent_vouchers:
        voucher_rows.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(voucher.get_voucher_type_display())}</span>
                <h2>{escape(voucher.voucher_number)}</h2>
                <p>{escape(voucher.party_name or 'No party')} - {escape(money(voucher.total_amount, currency))}<br />{voucher.voucher_date:%d %b %Y}</p>
              </div>
              <div class="dashboard-actions">
                <a class="button secondary" href="/dashboard/vouchers/{voucher.id}/">Open voucher</a>
              </div>
            </article>
            """
        )
    if not voucher_rows:
        voucher_rows.append('<article class="dashboard-card empty-state compact-card"><span>Voucher</span><h2>No vouchers yet</h2><p>Post purchase and sales vouchers to build your accounting and FIFO stock ledger.</p></article>')

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    message_html = f'<p class="form-success">{escape(message)}</p>' if message else ""
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Voucher engine</p>
        <h1>Post vouchers with clean accounting control.</h1>
        <p>Record sales, purchases, expenses, payments, receipts, contra transfers and journals from one screen. Stock vouchers update FIFO inventory; accounting vouchers post balanced ledger entries.</p>
        {error_html}{message_html}
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Voucher entry</p><h2>Day book posting</h2></div>
        <form method="post" class="dashboard-form voucher-form" data-voucher-form>
          {csrf_input(request)}
          <label>Voucher type<select name="voucher_type">{voucher_type_options(values['voucher_type'])}</select></label>
          <label>Date<input name="voucher_date" type="date" value="{escape(values['voucher_date'])}" /></label>
          <label>Party name<input name="party_name" value="{escape(values['party_name'])}" placeholder="Customer, supplier or narration party" required /></label>
          <label data-ledger-field>Amount<input name="amount" type="number" min="0.01" step="0.01" value="{escape(values['amount'])}" placeholder="Voucher amount" /></label>
          <label data-ledger-field>Main ledger<select name="primary_account">{account_options_with_blank(accounts, values['primary_account'], 'Select debit or receipt credit ledger')}</select></label>
          <label data-ledger-field>Cash/bank or second ledger<select name="secondary_account">{account_options_with_blank(accounts, values['secondary_account'], 'Optional for expense, payment or receipt')}</select></label>
          <p class="form-hint full-row" data-ledger-field>Expense and payment: debit the main ledger and credit Cash/Bank. Receipt: debit Cash/Bank and credit the main ledger. Contra and journal require both ledgers.</p>
          <label data-stock-field>Existing item<select name="item_id">{inventory_item_options(items, values['item_id'])}</select></label>
          <label data-stock-field>New item name<input name="item_name" value="{escape(values['item_name'])}" placeholder="Create item if not in list" /></label>
          <label data-stock-field>Godown/location<select name="godown_id">{godown_options(godowns, values['godown_id'])}</select></label>
          <label data-stock-field>Quantity<input name="quantity" type="number" min="0.01" step="0.01" value="{escape(values['quantity'])}" placeholder="For sales/purchase stock" /></label>
          <label data-stock-field>Rate<input name="rate" type="number" min="0.01" step="0.01" value="{escape(values['rate'])}" placeholder="For sales/purchase stock" /></label>
          <p class="form-hint full-row" data-stock-field>Sales and purchase vouchers use item, godown, quantity and rate. Purchase adds FIFO stock; sales consumes FIFO stock and posts cost of goods sold.</p>
          <label class="full-row">Narration<textarea name="narration" rows="2" placeholder="Optional voucher narration">{escape(values['narration'])}</textarea></label>
          <button class="button primary" type="submit">Post voucher</button>
        </form>
      </section>
      <section class="dashboard-section"><div class="section-head"><p class="eyebrow">Stock valuation</p><h2>FIFO stock snapshot</h2></div><div class="dashboard-grid">{''.join(item_cards)}</div></section>
      <section class="dashboard-section"><div class="section-head"><p class="eyebrow">Day book</p><h2>Recent vouchers</h2></div><div class="dashboard-grid">{''.join(voucher_rows)}</div></section>
    </main>
    <script>
      (() => {{
        const form = document.querySelector("[data-voucher-form]");
        if (!form) return;
        const typeField = form.querySelector('[name="voucher_type"]');
        const stockFields = form.querySelectorAll("[data-stock-field]");
        const ledgerFields = form.querySelectorAll("[data-ledger-field]");
        const syncVoucherFields = () => {{
          const stockVoucher = ["sales", "purchase"].includes(typeField.value);
          stockFields.forEach((field) => {{ field.hidden = !stockVoucher; }});
          ledgerFields.forEach((field) => {{ field.hidden = stockVoucher; }});
        }};
        typeField.addEventListener("change", syncVoucherFields);
        syncVoucherFields();
      }})();
    </script>
    """
    return page_shell("Voucher engine", body, request)


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
        "business_type": profile.business_type if profile else "service",
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
            "business_type": clean_text(request.POST.get("business_type"), "service", 30),
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
        if values["business_type"] not in BUSINESS_TYPE_PRESETS:
            values["business_type"] = "other"
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
          <label class="full-row">Business type<select name="business_type">{business_type_options(values['business_type'])}</select></label>
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
        amount = invoice_outstanding_amount(invoice)
        if amount <= 0:
            continue
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
    for bill in VendorBill.objects.filter(account_q(request)).exclude(status="paid").order_by("due_date", "bill_date"):
        due_date = bill.due_date or bill.bill_date
        bucket = aging_bucket((today - due_date).days)
        amount = vendor_bill_outstanding_amount(bill)
        if amount <= 0:
            continue
        ap_totals[bucket] += amount
        ap_rows.append(
            f"""
            <tr>
              <td><strong>{escape(bill.vendor_name)}</strong><span>{escape(bill.category)}</span></td>
              <td>{escape(bill.bill_date.strftime('%d %b %Y'))}</td>
              <td>{escape(due_date.strftime('%d %b %Y'))}</td>
              <td>{escape(bucket)}</td>
              <td class="amount-cell">{escape(money(amount, currency))}</td>
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

    posted_entries = JournalEntry.objects.filter(account_q(request), is_posted=True)
    trial_rows = []
    for account in Account.objects.filter(account_q(request), is_active=True).order_by("code"):
        debit = sum((line.debit for line in JournalLine.objects.filter(entry__in=posted_entries, account=account)), Decimal("0")).quantize(Decimal("0.01"))
        credit = sum((line.credit for line in JournalLine.objects.filter(entry__in=posted_entries, account=account)), Decimal("0")).quantize(Decimal("0.01"))
        if debit == 0 and credit == 0:
            continue
        trial_rows.append(
            f"""
            <tr>
              <td>{escape(account.code)}</td>
              <td>{escape(account.name)}</td>
              <td>{escape(account.get_account_type_display())}</td>
              <td class="amount-cell">{escape(money(debit, currency)) if debit else '-'}</td>
              <td class="amount-cell">{escape(money(credit, currency)) if credit else '-'}</td>
            </tr>
            """
        )
    if not trial_rows:
        trial_rows.append('<tr><td colspan="5" class="empty-report-row">No posted ledger lines yet.</td></tr>')

    tax_lines = JournalLine.objects.filter(entry__in=posted_entries, account__code__in=["2100", "2110", "2120", "2130"])
    tax_collected = sum((line.credit for line in tax_lines), Decimal("0")).quantize(Decimal("0.01"))
    tax_reduced = sum((line.debit for line in tax_lines), Decimal("0")).quantize(Decimal("0.01"))
    tax_payable = (tax_collected - tax_reduced).quantize(Decimal("0.01"))

    sales_by_customer = []
    for name in sorted(set(Invoice.objects.filter(account_q(request)).values_list("client_name", flat=True))):
        invoices_for_customer = Invoice.objects.filter(account_q(request), client_name=name)
        gross = sum((invoice_total_amount(invoice) for invoice in invoices_for_customer), Decimal("0")).quantize(Decimal("0.01"))
        credits = sum((credit.total_amount for credit in CustomerCreditNote.objects.filter(account_q(request), client_name=name)), Decimal("0")).quantize(Decimal("0.01"))
        sales_by_customer.append(
            f"""
            <tr>
              <td>{escape(name)}</td>
              <td class="amount-cell">{escape(money(gross, currency))}</td>
              <td class="amount-cell">{escape(money(credits, currency))}</td>
              <td class="amount-cell">{escape(money(gross - credits, currency))}</td>
            </tr>
            """
        )
    if not sales_by_customer:
        sales_by_customer.append('<tr><td colspan="4" class="empty-report-row">No customer sales yet.</td></tr>')

    expense_by_vendor = []
    for name in sorted(set(VendorBill.objects.filter(account_q(request)).values_list("vendor_name", flat=True))):
        bills_for_vendor = VendorBill.objects.filter(account_q(request), vendor_name=name)
        gross = sum((bill.amount for bill in bills_for_vendor), Decimal("0")).quantize(Decimal("0.01"))
        debits = sum((debit.amount for debit in VendorDebitNote.objects.filter(account_q(request), vendor_name=name)), Decimal("0")).quantize(Decimal("0.01"))
        expense_by_vendor.append(
            f"""
            <tr>
              <td>{escape(name)}</td>
              <td class="amount-cell">{escape(money(gross, currency))}</td>
              <td class="amount-cell">{escape(money(debits, currency))}</td>
              <td class="amount-cell">{escape(money(gross - debits, currency))}</td>
            </tr>
            """
        )
    if not expense_by_vendor:
        expense_by_vendor.append('<tr><td colspan="4" class="empty-report-row">No vendor expenses yet.</td></tr>')

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
            <thead><tr><th>Invoice / customer</th><th>Invoice date</th><th>Due date</th><th>Aging</th><th>Balance</th></tr></thead>
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
            <thead><tr><th>Vendor / category</th><th>Bill date</th><th>Due date</th><th>Aging</th><th>Balance</th></tr></thead>
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
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Trial balance</p>
          <h2>Debit and credit by ledger account</h2>
          <p>Review posted balances account by account before preparing final reports.</p>
        </div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Type</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{''.join(trial_rows)}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Balance sheet</p>
          <h2>Assets, liabilities and equity snapshot</h2>
          <p>Based on posted journal entries in this account.</p>
        </div>
        <div class="report-statement">
          <div><span>Assets</span><strong>{escape(money(totals['assets'], currency))}</strong></div>
          <div><span>Liabilities</span><strong>{escape(money(totals['liabilities'], currency))}</strong></div>
          <div><span>Owner equity</span><strong>{escape(money(totals['equity'], currency)) if 'equity' in totals else escape(money(Decimal('0'), currency))}</strong></div>
          <div class="statement-total"><span>Current profit</span><strong>{escape(money(profit, currency))}</strong></div>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Tax summary</p>
          <h2>{'Sales tax' if current_market(request) == 'US' else 'GST'} payable movement</h2>
        </div>
        <div class="report-statement">
          <div><span>Tax collected on invoices</span><strong>{escape(money(tax_collected, currency))}</strong></div>
          <div><span>Tax reduced by credit notes</span><strong>{escape(money(tax_reduced, currency))}</strong></div>
          <div class="statement-total"><span>Net tax payable</span><strong>{escape(money(tax_payable, currency))}</strong></div>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Sales</p><h2>Sales by customer</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Customer</th><th>Gross invoices</th><th>Credit notes</th><th>Net sales</th></tr></thead>
            <tbody>{''.join(sales_by_customer)}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Expenses</p><h2>Expenses by vendor</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Vendor</th><th>Gross bills</th><th>Debit notes</th><th>Net expense</th></tr></thead>
            <tbody>{''.join(expense_by_vendor)}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Financial reports", body, request)


def statement_table_rows(rows: list[dict[str, Any]], currency: str, *, debit_label: str, credit_label: str) -> str:
    if not rows:
        return '<tr><td colspan="6" class="empty-report-row">No statement entries yet.</td></tr>'
    html_rows = []
    for row in rows:
        reference = escape(row["reference"] or row["kind"])
        if row.get("link"):
            reference = f'<a href="{escape(row["link"])}">{reference}</a>'
        html_rows.append(
            f"""
            <tr>
              <td>{row['date']:%d %b %Y}</td>
              <td><strong>{escape(row['kind'])}</strong><span>{escape(row['description'] or '')}</span></td>
              <td>{reference}</td>
              <td class="amount-cell">{escape(money(row['debit'], currency)) if row['debit'] else '-'}</td>
              <td class="amount-cell">{escape(money(row['credit'], currency)) if row['credit'] else '-'}</td>
              <td class="amount-cell">{escape(money(row['balance'], currency))}</td>
            </tr>
            """
        )
    return "".join(html_rows)


@login_required
@require_GET
def customer_ledger(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    names = customer_statement_names(request)
    selected = clean_text(request.GET.get("customer"), max_length=180)
    if selected and selected.lower() not in {name.lower() for name in names}:
        selected = ""
    if not selected and names:
        selected = names[0]
    rows, invoiced, received, balance = customer_statement_entries(request, selected) if selected else ([], Decimal("0"), Decimal("0"), Decimal("0"))
    customer_cards = "".join(
        f"""
        <article class="dashboard-card compact-card">
          <div>
            <span>Customer</span>
            <h2>{escape(name)}</h2>
          </div>
          <div class="dashboard-actions">
            <a class="button secondary" href="/dashboard/ledger/customers/?customer={quote_plus(name)}">View statement</a>
          </div>
        </article>
        """
        for name in names[:12]
    )
    if not customer_cards:
        customer_cards = '<article class="dashboard-card empty-state compact-card"><span>Customer ledger</span><h2>No customers yet</h2><p>Create invoices or clients to build customer statements.</p></article>'
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Customer ledger</p>
        <h1>Customer statement</h1>
        <p>Review invoices, receipts, credit notes, partial payments and current balance for each customer.</p>
        <div class="hero-actions">
          <a class="button secondary" href="/dashboard/">Back to dashboard</a>
          <a class="button primary" href="/dashboard/payments/new/">Record receipt</a>
        </div>
      </section>
      <section class="dashboard-section">
        <form method="get" class="dashboard-form invoice-server-form">
          <label>Customer<select name="customer">{statement_name_options(names, selected, 'Select customer')}</select></label>
          <button class="button primary" type="submit">Open statement</button>
        </form>
      </section>
      <section class="report-kpi-grid" aria-label="Customer statement summary">
        <article><span>Customer</span><strong>{escape(selected or 'None selected')}</strong></article>
        <article><span>Invoiced</span><strong>{escape(money(invoiced, currency))}</strong></article>
        <article><span>Receipts / credits</span><strong>{escape(money(received, currency))}</strong></article>
        <article><span>Balance due</span><strong>{escape(money(balance, currency))}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Statement</p>
          <h2>{escape(selected or 'Select a customer')}</h2>
          <p>Debit increases receivable. Credit records customer receipts and credit notes.</p>
        </div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Date</th><th>Type</th><th>Reference</th><th>Invoice</th><th>Receipt / credit</th><th>Balance</th></tr></thead>
            <tbody>{statement_table_rows(rows, currency, debit_label='Invoice', credit_label='Receipt / credit')}</tbody>
          </table>
        </div>
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Customers</p><h2>Available statements</h2></div>
        <div class="dashboard-grid">{customer_cards}</div>
      </section>
    </main>
    """
    return page_shell("Customer ledger", body, request)


@login_required
@require_GET
def vendor_ledger(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    names = vendor_statement_names(request)
    selected = clean_text(request.GET.get("vendor"), max_length=180)
    if selected and selected.lower() not in {name.lower() for name in names}:
        selected = ""
    if not selected and names:
        selected = names[0]
    rows, billed, paid, balance = vendor_statement_entries(request, selected) if selected else ([], Decimal("0"), Decimal("0"), Decimal("0"))
    vendor_cards = "".join(
        f"""
        <article class="dashboard-card compact-card">
          <div>
            <span>Vendor</span>
            <h2>{escape(name)}</h2>
          </div>
          <div class="dashboard-actions">
            <a class="button secondary" href="/dashboard/ledger/vendors/?vendor={quote_plus(name)}">View statement</a>
          </div>
        </article>
        """
        for name in names[:12]
    )
    if not vendor_cards:
        vendor_cards = '<article class="dashboard-card empty-state compact-card"><span>Vendor ledger</span><h2>No vendors yet</h2><p>Create vendor bills to build vendor statements.</p></article>'
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Vendor ledger</p>
        <h1>Vendor statement</h1>
        <p>Review bills, partial payments and amount still payable for each vendor.</p>
        <div class="hero-actions">
          <a class="button secondary" href="/dashboard/">Back to dashboard</a>
          <a class="button primary" href="/dashboard/expenses/pay/">Pay vendor bill</a>
        </div>
      </section>
      <section class="dashboard-section">
        <form method="get" class="dashboard-form invoice-server-form">
          <label>Vendor<select name="vendor">{statement_name_options(names, selected, 'Select vendor')}</select></label>
          <button class="button primary" type="submit">Open statement</button>
        </form>
      </section>
      <section class="report-kpi-grid" aria-label="Vendor statement summary">
        <article><span>Vendor</span><strong>{escape(selected or 'None selected')}</strong></article>
        <article><span>Billed</span><strong>{escape(money(billed, currency))}</strong></article>
        <article><span>Paid</span><strong>{escape(money(paid, currency))}</strong></article>
        <article><span>Balance payable</span><strong>{escape(money(balance, currency))}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head">
          <p class="eyebrow">Statement</p>
          <h2>{escape(selected or 'Select a vendor')}</h2>
          <p>Credit increases payable. Debit records payment to vendor.</p>
        </div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Date</th><th>Type</th><th>Reference</th><th>Payment</th><th>Bill</th><th>Balance</th></tr></thead>
            <tbody>{statement_table_rows(rows, currency, debit_label='Payment', credit_label='Bill')}</tbody>
          </table>
        </div>
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Vendors</p><h2>Available statements</h2></div>
        <div class="dashboard-grid">{vendor_cards}</div>
      </section>
    </main>
    """
    return page_shell("Vendor ledger", body, request)


@login_required
@require_GET
def receipt_pdf(request: HttpRequest, receipt_id: int) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    currency = "$" if receipt.market == "US" else RUPEE_SYMBOL
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=42, bottomMargin=40)
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("ReceiptBody", parent=styles["BodyText"], fontSize=10, leading=14)
    small_style = ParagraphStyle("ReceiptSmall", parent=body_style, fontSize=8.5, leading=11, textColor=colors.HexColor("#5b6964"))
    heading_style = ParagraphStyle("ReceiptHeading", parent=styles["Heading1"], fontSize=20, leading=24, spaceAfter=8)
    label_style = ParagraphStyle("ReceiptLabel", parent=small_style, textColor=colors.white)
    accent = colors.HexColor("#126b4f")
    profile = get_business_profile(request)

    def para(value: str, style=body_style) -> Paragraph:
        return Paragraph(escape(value or "").replace("\n", "<br/>"), style)

    invoice_ref = invoice_number(receipt.invoice) if receipt.invoice else "Direct receipt"
    voucher_ref = receipt.voucher.voucher_number if receipt.voucher else receipt.reference
    story = [
        Table([[""]], colWidths=[500], rowHeights=[5], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)])),
        Spacer(1, 18),
        Paragraph("PAYMENT ACKNOWLEDGEMENT", heading_style),
        para(f"{profile.business_name} - {profile.business_address}" if profile else "Generated by RozLedger for the account holder's records.", small_style),
        Spacer(1, 18),
        Table(
            [
                [para("Receipt date", small_style), para(f"{receipt.payment_date:%d %b %Y}")],
                [para("Received from", small_style), para(receipt.payer_name)],
                [para("Amount received", small_style), para(money(receipt.amount, currency))],
                [para("Method", small_style), para(receipt.get_method_display())],
                [para("Invoice/reference", small_style), para(invoice_ref)],
                [para("Voucher", small_style), para(voucher_ref or "Not linked")],
                [para("Payment reference", small_style), para(receipt.reference or "Not provided")],
            ],
            colWidths=[150, 350],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7f5")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("PADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            ),
        ),
    ]
    if receipt.notes:
        story.extend([Spacer(1, 14), Paragraph("Notes", heading_style), para(receipt.notes)])
    story.extend([Spacer(1, 22), para("This acknowledgement records payment receipt only. Verify tax and legal details with a qualified professional.", small_style)])
    doc.build(story)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="rozledger-receipt-{receipt.id}.pdf"'
    return response


@login_required
@require_GET
def vendor_bill_detail(request: HttpRequest, bill_id: int) -> HttpResponse:
    bill = owned_vendor_bill(request, bill_id)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    upload_cards = []
    for draft in bill.upload_drafts.all():
        preview = ""
        content_type = "application/pdf" if draft.document.name.lower().endswith(".pdf") else image_content_type(draft.document.name)
        if content_type.startswith("image/"):
            preview = f'<img class="bill-attachment-preview" src="/dashboard/expenses/{bill.id}/attachments/{draft.id}/" alt="{escape(draft.original_filename or "Bill attachment")}" />'
        elif content_type == "application/pdf":
            preview = f'<iframe class="bill-attachment-frame" src="/dashboard/expenses/{bill.id}/attachments/{draft.id}/" title="{escape(draft.original_filename or "Bill PDF")}"></iframe>'
        upload_cards.append(
            f"""
            <article class="dashboard-card compact-card">
              <div>
                <span>{escape(draft.get_status_display())}</span>
                <h2>{escape(draft.original_filename or 'Uploaded bill')}</h2>
                <p>{escape(draft.extracted_text[:220] or 'No extracted text available.')}</p>
                {preview}
              </div>
              <div class="dashboard-actions">
                <a class="button secondary" href="/dashboard/expenses/{bill.id}/attachments/{draft.id}/" target="_blank" rel="noopener">Open attachment</a>
              </div>
            </article>
            """
        )
    if not upload_cards:
        upload_cards.append('<article class="dashboard-card empty-state compact-card"><span>Attachment</span><h2>No upload linked</h2><p>Upload a bill photo/PDF and confirm posting to link an attachment here.</p><a class="button secondary" href="/dashboard/expenses/upload/">Upload bill</a></article>')
    payment_rows = "".join(
        f"""
        <tr>
          <td>{payment.payment_date:%d %b %Y}</td>
          <td>{voucher_link(payment.voucher)}</td>
          <td>{escape(payment.reference or 'Not provided')}</td>
          <td>{escape(payment.get_method_display())}</td>
          <td class="amount-cell">{escape(money(payment.amount, currency))}</td>
          <td><a href="/dashboard/vendor-payments/{payment.id}/reverse/">Reverse</a></td>
        </tr>
        """
        for payment in bill.payments.all()
    ) or '<tr><td colspan="6" class="empty-report-row">No payments posted yet.</td></tr>'
    debit_rows = "".join(
        f"""
        <tr>
          <td>{debit_note.debit_date:%d %b %Y}</td>
          <td><a href="/dashboard/debit-notes/{debit_note.id}/">{escape(debit_note.debit_note_number)}</a></td>
          <td>{escape(debit_note.reason)}</td>
          <td>{voucher_link(debit_note.voucher)}</td>
          <td class="amount-cell">{escape(money(debit_note.amount, currency))}</td>
        </tr>
        """
        for debit_note in bill.debit_notes.select_related("voucher").all()
    ) or '<tr><td colspan="5" class="empty-report-row">No debit notes posted against this vendor bill.</td></tr>'
    source_actions = "".join(
        part
        for part in [
            f'<a class="button secondary" href="/dashboard/vouchers/{bill.voucher.id}/">Bill voucher</a>' if bill.voucher else "",
            f'<a class="button secondary" href="/dashboard/accounting/journal/{bill.journal_entry.id}/">Bill journal</a>' if bill.journal_entry else "",
            f'<a class="button secondary" href="/dashboard/vouchers/{bill.payment_voucher.id}/">Latest payment voucher</a>' if bill.payment_voucher else "",
        ]
    )
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Vendor bill</p>
        <h1>{escape(bill.vendor_name)}</h1>
        <p>{escape(bill.category)} - total {escape(money(bill.amount, currency))}, paid {escape(money(vendor_bill_amount_paid(bill), currency))}, balance {escape(money(vendor_bill_outstanding_amount(bill), currency))}.</p>
        <div class="hero-actions">
          <a class="button secondary" href="/dashboard/#payables">Back to payables</a>
          <a class="button primary" href="/dashboard/expenses/pay/?bill={bill.id}">Pay bill</a>
          <a class="button secondary" href="/dashboard/expenses/{bill.id}/debit-notes/new/">Issue debit note</a>
          <a class="button secondary" href="/dashboard/ledger/vendors/?vendor={quote_plus(bill.vendor_name)}">Vendor statement</a>
          {source_actions}
        </div>
      </section>
      <section class="report-kpi-grid">
        <article><span>Status</span><strong>{escape(bill.get_status_display())}</strong></article>
        <article><span>Bill date</span><strong>{bill.bill_date:%d %b %Y}</strong></article>
        <article><span>Due date</span><strong>{bill.due_date.strftime('%d %b %Y') if bill.due_date else 'Not set'}</strong></article>
        <article><span>Reference</span><strong>{escape(bill.reference or 'Not set')}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Payments</p><h2>Payment history</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Date</th><th>Voucher</th><th>Reference</th><th>Method</th><th>Amount</th><th>Correction</th></tr></thead>
            <tbody>{payment_rows}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Corrections</p><h2>Debit notes</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Date</th><th>Debit note</th><th>Reason</th><th>Voucher</th><th>Amount</th></tr></thead>
            <tbody>{debit_rows}</tbody>
          </table>
        </div>
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">Attachments</p><h2>Uploaded bill preview</h2></div>
        <div class="dashboard-grid">{''.join(upload_cards)}</div>
      </section>
    </main>
    """
    return page_shell("Vendor bill", body, request)


@login_required
@require_GET
def vendor_bill_attachment(request: HttpRequest, bill_id: int, draft_id: int) -> HttpResponse:
    bill = owned_vendor_bill(request, bill_id)
    draft = bill.upload_drafts.filter(id=draft_id).first()
    if not draft or not draft.document:
        raise Http404("Attachment not found")
    content_type = "application/pdf" if draft.document.name.lower().endswith(".pdf") else image_content_type(draft.document.name)
    response = FileResponse(draft.document.open("rb"), content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{draft.original_filename or Path(draft.document.name).name}"'
    return response


@login_required
@require_GET
def invoice_accounting_detail(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    currency = invoice.currency_symbol
    voucher = invoice.sales_voucher
    receipts = invoice.payments.select_related("voucher", "journal_entry").all()
    credit_notes = invoice.credit_notes.select_related("voucher", "journal_entry").all()
    outstanding = invoice_outstanding_amount(invoice)
    credit_action = f'<a class="button secondary" href="/dashboard/invoices/{invoice.id}/credit-notes/new/">Issue credit note</a>' if outstanding > 0 else ""
    receipt_rows = "".join(
        f"""
        <tr>
          <td>{receipt.payment_date:%d %b %Y}</td>
          <td><a href="/dashboard/payments/{receipt.id}/">{escape(receipt.reference or receipt.payer_name)}</a></td>
          <td>{escape(receipt.get_method_display())}</td>
          <td>{voucher_link(receipt.voucher)}</td>
          <td>{journal_entry_link(receipt.journal_entry)}</td>
          <td class="amount-cell">{escape(money(receipt.amount, currency))}</td>
        </tr>
        """
        for receipt in receipts
    ) or '<tr><td colspan="6" class="empty-report-row">No receipts posted against this invoice yet.</td></tr>'
    credit_rows = "".join(
        f"""
        <tr>
          <td>{credit_note.credit_date:%d %b %Y}</td>
          <td><a href="/dashboard/credit-notes/{credit_note.id}/">{escape(credit_note.credit_note_number)}</a></td>
          <td>{escape(credit_note.reason)}</td>
          <td>{voucher_link(credit_note.voucher)}</td>
          <td>{journal_entry_link(credit_note.journal_entry)}</td>
          <td class="amount-cell">{escape(money(credit_note.total_amount, currency))}</td>
        </tr>
        """
        for credit_note in credit_notes
    ) or '<tr><td colspan="6" class="empty-report-row">No credit notes issued against this invoice.</td></tr>'
    correction_note = (
        "This invoice has posted accounting entries. Use receipts, credit notes or a correcting invoice for changes that affect money."
        if voucher or receipts
        else "This invoice has not posted accounting yet and can be edited or deleted."
    )
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Invoice accounting</p>
        <h1>{escape(invoice_number(invoice))}</h1>
        <p>{escape(invoice.client_name)} - total {escape(invoice_total_display(invoice))}, received {escape(money(invoice_amount_received(invoice), currency))}, credited {escape(money(invoice_amount_credited(invoice), currency))}, outstanding {escape(money(outstanding, currency))}.</p>
        <div class="hero-actions">
          <a class="button primary" href="/invoice/{escape(invoice.public_token)}/" target="_blank" rel="noopener">Open invoice</a>
          <a class="button secondary" href="/dashboard/payments/new/?invoice={invoice.id}">Record payment</a>
          {credit_action}
          <a class="button secondary" href="/dashboard/invoices/{invoice.id}/edit/">Edit invoice</a>
          <a class="button secondary" href="/dashboard/">Back to dashboard</a>
        </div>
      </section>
      <section class="report-kpi-grid">
        <article><span>Status</span><strong>{escape(invoice.get_status_display())}</strong></article>
        <article><span>Sales voucher</span><strong>{voucher_link(voucher)}</strong></article>
        <article><span>Sales journal</span><strong>{journal_entry_link(voucher.journal_entry if voucher else None)}</strong></article>
        <article><span>Correction rule</span><strong>{escape('Locked' if voucher or receipts else 'Open')}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Control</p><h2>Correction safety</h2><p>{escape(correction_note)}</p></div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Sales posting</p><h2>Voucher ledger lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{voucher_ledger_table(voucher, currency)}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Settlement</p><h2>Receipts against this invoice</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Date</th><th>Receipt</th><th>Method</th><th>Voucher</th><th>Journal</th><th>Amount</th></tr></thead>
            <tbody>{receipt_rows}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Corrections</p><h2>Credit notes against this invoice</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Date</th><th>Credit note</th><th>Reason</th><th>Voucher</th><th>Journal</th><th>Amount</th></tr></thead>
            <tbody>{credit_rows}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Invoice accounting", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def credit_note_new(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    currency = invoice.currency_symbol
    outstanding = invoice_outstanding_amount(invoice)
    values = {
        "credit_date": f"{timezone.localdate():%Y-%m-%d}",
        "total_amount": f"{outstanding}",
        "reason": "Invoice correction",
        "notes": "",
    }
    error = ""
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        total_amount = decimal_value(values["total_amount"])
        if not values["reason"]:
            error = "Reason is required."
        else:
            try:
                credit_note = post_customer_credit_note(
                    request,
                    invoice=invoice,
                    credit_date=parse_form_date(values["credit_date"]),
                    total_amount=total_amount,
                    reason=values["reason"],
                    notes=values["notes"],
                )
                audit_log(request, "credit_note.created", "CustomerCreditNote", credit_note.id, f"Issued {credit_note.credit_note_number} for {invoice_number(invoice)}")
                return redirect(f"/dashboard/credit-notes/{credit_note.id}/")
            except ValueError as exc:
                error = str(exc)
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Correction</p>
        <h1>Issue customer credit note</h1>
        <p class="account-copy">Credit notes reduce the invoice receivable without deleting accounting history. Maximum credit available for {escape(invoice_number(invoice))}: {escape(money(outstanding, currency))}.</p>
        {error_html}
        <div class="selected-invoice-panel">
          <span>Selected invoice</span>
          <strong>{escape(invoice_number(invoice))}</strong>
          <p>{escape(invoice.client_name)} - total {escape(invoice_total_display(invoice))} / outstanding {escape(money(outstanding, currency))}</p>
        </div>
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Credit date<input name="credit_date" type="date" value="{escape(values['credit_date'])}" required /></label>
          <label>Credit amount<input name="total_amount" type="number" min="0.01" max="{outstanding}" step="0.01" value="{escape(values['total_amount'])}" required /></label>
          <label class="full-row">Reason<input name="reason" value="{escape(values['reason'])}" placeholder="Wrong amount, discount, cancelled work" required /></label>
          <label class="full-row">Notes<textarea name="notes" rows="3" placeholder="Optional internal note">{escape(values['notes'])}</textarea></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post credit note</button>
            <a class="button secondary" href="/dashboard/invoices/{invoice.id}/accounting/">Back to invoice accounting</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Issue credit note", body, request)


@login_required
@require_GET
def credit_note_detail(request: HttpRequest, credit_note_id: int) -> HttpResponse:
    credit_note = owned_credit_note(request, credit_note_id)
    currency = "$" if credit_note.market == "US" else RUPEE_SYMBOL
    invoice = credit_note.invoice
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Credit note</p>
        <h1>{escape(credit_note.credit_note_number)}</h1>
        <p>{escape(money(credit_note.total_amount, currency))} credited to {escape(credit_note.client_name)} for {escape(invoice_number(invoice))}.</p>
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/invoices/{invoice.id}/accounting/">Open invoice accounting</a>
          <a class="button secondary" href="/dashboard/credit-notes/{credit_note.id}/download.pdf">Download PDF</a>
          <a class="button secondary" href="/dashboard/ledger/customers/?customer={quote_plus(credit_note.client_name)}">Customer ledger</a>
          <a class="button secondary" href="/dashboard/">Back to dashboard</a>
        </div>
      </section>
      <section class="report-kpi-grid">
        <article><span>Date</span><strong>{credit_note.credit_date:%d %b %Y}</strong></article>
        <article><span>Taxable credit</span><strong>{escape(money(credit_note.taxable_amount, currency))}</strong></article>
        <article><span>Tax credit</span><strong>{escape(money(credit_note.tax_amount, currency))}</strong></article>
        <article><span>Total credit</span><strong>{escape(money(credit_note.total_amount, currency))}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Reason</p><h2>{escape(credit_note.reason)}</h2><p>{escape(credit_note.notes or 'No additional note saved.')}</p></div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Accounting</p><h2>Credit note voucher ledger lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{voucher_ledger_table(credit_note.voucher, currency)}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Credit note detail", body, request)


@login_required
@require_GET
def credit_note_pdf(request: HttpRequest, credit_note_id: int) -> HttpResponse:
    credit_note = owned_credit_note(request, credit_note_id)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    currency = "$" if credit_note.market == "US" else RUPEE_SYMBOL
    profile = get_business_profile(request)
    invoice = credit_note.invoice
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=42, bottomMargin=40)
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("CreditBody", parent=styles["BodyText"], fontSize=10, leading=14)
    small_style = ParagraphStyle("CreditSmall", parent=body_style, fontSize=8.5, leading=11, textColor=colors.HexColor("#5b6964"))
    heading_style = ParagraphStyle("CreditHeading", parent=styles["Heading1"], fontSize=20, leading=24, spaceAfter=8)
    accent = colors.HexColor("#126b4f")

    def para(value: str, style=body_style) -> Paragraph:
        return Paragraph(escape(value or "").replace("\n", "<br/>"), style)

    story = [
        Table([[""]], colWidths=[500], rowHeights=[5], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)])),
        Spacer(1, 18),
        Paragraph("CREDIT NOTE", heading_style),
        para(f"{profile.business_name} - {profile.business_address}" if profile else "Generated by RozLedger.", small_style),
        Spacer(1, 18),
        Table(
            [
                [para("Credit note number", small_style), para(credit_note.credit_note_number)],
                [para("Credit date", small_style), para(f"{credit_note.credit_date:%d %b %Y}")],
                [para("Customer", small_style), para(credit_note.client_name)],
                [para("Original invoice", small_style), para(invoice_number(invoice))],
                [para("Reason", small_style), para(credit_note.reason)],
                [para("Taxable credit", small_style), para(money(credit_note.taxable_amount, currency))],
                [para("Tax credit", small_style), para(money(credit_note.tax_amount, currency))],
                [para("Total credit", small_style), para(money(credit_note.total_amount, currency))],
            ],
            colWidths=[150, 350],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7f5")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0dd")),
                    ("PADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            ),
        ),
        Spacer(1, 18),
        para(credit_note.notes or "This credit note adjusts the original invoice balance.", body_style),
        Spacer(1, 22),
        para("Generated by RozLedger www.rozledger.in / www.rozledger.com", small_style),
    ]
    doc.build(story)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="rozledger-credit-note-{credit_note.id}.pdf"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def vendor_debit_note_new(request: HttpRequest, bill_id: int) -> HttpResponse:
    bill = owned_vendor_bill(request, bill_id)
    currency = "$" if bill.market == "US" else RUPEE_SYMBOL
    outstanding = vendor_bill_outstanding_amount(bill)
    values = {
        "debit_date": f"{timezone.localdate():%Y-%m-%d}",
        "amount": f"{outstanding}",
        "reason": "Vendor bill adjustment",
        "notes": "",
    }
    error = ""
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        try:
            debit_note = post_vendor_debit_note(
                request,
                bill=bill,
                debit_date=parse_form_date(values["debit_date"]),
                amount=decimal_value(values["amount"]),
                reason=values["reason"],
                notes=values["notes"],
            )
            audit_log(request, "debit_note.created", "VendorDebitNote", debit_note.id, f"Issued {debit_note.debit_note_number} for vendor bill {bill.id}")
            return redirect(f"/dashboard/debit-notes/{debit_note.id}/")
        except ValueError as exc:
            error = str(exc)
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Correction</p>
        <h1>Issue vendor debit note</h1>
        <p class="account-copy">Debit notes reduce accounts payable without deleting the original vendor bill. Maximum available adjustment: {escape(money(outstanding, currency))}.</p>
        {error_html}
        <div class="selected-invoice-panel">
          <span>Selected vendor bill</span>
          <strong>{escape(bill.vendor_name)}</strong>
          <p>{escape(bill.category)} - total {escape(money(bill.amount, currency))} / outstanding {escape(money(outstanding, currency))}</p>
        </div>
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Debit note date<input name="debit_date" type="date" value="{escape(values['debit_date'])}" required /></label>
          <label>Debit note amount<input name="amount" type="number" min="0.01" max="{outstanding}" step="0.01" value="{escape(values['amount'])}" required /></label>
          <label class="full-row">Reason<input name="reason" value="{escape(values['reason'])}" placeholder="Vendor discount, return, wrong bill" required /></label>
          <label class="full-row">Notes<textarea name="notes" rows="3" placeholder="Optional internal note">{escape(values['notes'])}</textarea></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post debit note</button>
            <a class="button secondary" href="/dashboard/expenses/{bill.id}/">Back to vendor bill</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Issue vendor debit note", body, request)


@login_required
@require_GET
def vendor_debit_note_detail(request: HttpRequest, debit_note_id: int) -> HttpResponse:
    debit_note = owned_debit_note(request, debit_note_id)
    currency = "$" if debit_note.market == "US" else RUPEE_SYMBOL
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Vendor debit note</p>
        <h1>{escape(debit_note.debit_note_number)}</h1>
        <p>{escape(money(debit_note.amount, currency))} adjusted from {escape(debit_note.vendor_name)}.</p>
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/expenses/{debit_note.bill.id}/">Open vendor bill</a>
          <a class="button secondary" href="/dashboard/ledger/vendors/?vendor={quote_plus(debit_note.vendor_name)}">Vendor ledger</a>
        </div>
      </section>
      <section class="report-kpi-grid">
        <article><span>Date</span><strong>{debit_note.debit_date:%d %b %Y}</strong></article>
        <article><span>Vendor</span><strong>{escape(debit_note.vendor_name)}</strong></article>
        <article><span>Amount</span><strong>{escape(money(debit_note.amount, currency))}</strong></article>
        <article><span>Voucher</span><strong>{voucher_link(debit_note.voucher)}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Reason</p><h2>{escape(debit_note.reason)}</h2><p>{escape(debit_note.notes or 'No additional note saved.')}</p></div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Accounting</p><h2>Debit note voucher ledger lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{voucher_ledger_table(debit_note.voucher, currency)}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Vendor debit note", body, request)


@login_required
@require_GET
def receipt_detail(request: HttpRequest, receipt_id: int) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id)
    currency = "$" if receipt.market == "US" else RUPEE_SYMBOL
    available_reversal = (receipt.amount - payment_receipt_amount_reversed(receipt)).quantize(Decimal("0.01"))
    invoice_copy = (
        f'<a href="/dashboard/invoices/{receipt.invoice.id}/accounting/">{escape(invoice_number(receipt.invoice))} - {escape(receipt.invoice.client_name)}</a>'
        if receipt.invoice
        else "Direct receipt"
    )
    reversal_action = f'<a class="button secondary" href="/dashboard/payments/{receipt.id}/reverse/">Reverse receipt</a>' if available_reversal > 0 else ""
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Receipt</p>
        <h1>{escape(receipt.payer_name)}</h1>
        <p>{escape(money(receipt.amount, currency))} received by {escape(receipt.get_method_display())} on {receipt.payment_date:%d %b %Y}.</p>
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/payments/{receipt.id}/receipt.pdf">Download acknowledgement</a>
          {reversal_action}
          <a class="button secondary" href="/dashboard/ledger/customers/?customer={quote_plus(receipt.payer_name)}">Customer ledger</a>
          <a class="button secondary" href="/dashboard/#receipts">Back to receipts</a>
        </div>
      </section>
      <section class="report-kpi-grid">
        <article><span>Invoice</span><strong>{invoice_copy}</strong></article>
        <article><span>Voucher</span><strong>{voucher_link(receipt.voucher)}</strong></article>
        <article><span>Journal</span><strong>{journal_entry_link(receipt.journal_entry)}</strong></article>
        <article><span>Available to reverse</span><strong>{escape(money(available_reversal, currency))}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Accounting</p><h2>Receipt voucher ledger lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{voucher_ledger_table(receipt.voucher, currency)}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Notes</p><h2>Payment note</h2><p>{escape(receipt.notes or 'No note saved.')}</p></div>
      </section>
    </main>
    """
    return page_shell("Receipt detail", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def receipt_reversal_new(request: HttpRequest, receipt_id: int) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id)
    currency = "$" if receipt.market == "US" else RUPEE_SYMBOL
    available = (receipt.amount - payment_receipt_amount_reversed(receipt)).quantize(Decimal("0.01"))
    values = {"reversal_date": f"{timezone.localdate():%Y-%m-%d}", "amount": f"{available}", "reason": "Wrong receipt posting", "notes": ""}
    error = ""
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        try:
            reversal = post_customer_receipt_reversal(
                request,
                receipt=receipt,
                reversal_date=parse_form_date(values["reversal_date"]),
                amount=decimal_value(values["amount"]),
                reason=values["reason"],
                notes=values["notes"],
            )
            audit_log(request, "payment_reversal.created", "PaymentReversal", reversal.id, f"Reversed receipt {receipt.id}")
            return redirect(f"/dashboard/reversals/{reversal.id}/")
        except ValueError as exc:
            error = str(exc)
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Correction</p>
        <h1>Reverse customer receipt</h1>
        <p class="account-copy">This posts the opposite accounting entry and keeps the original receipt visible. Available to reverse: {escape(money(available, currency))}.</p>
        {error_html}
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Reversal date<input name="reversal_date" type="date" value="{escape(values['reversal_date'])}" required /></label>
          <label>Amount<input name="amount" type="number" min="0.01" max="{available}" step="0.01" value="{escape(values['amount'])}" required /></label>
          <label class="full-row">Reason<input name="reason" value="{escape(values['reason'])}" required /></label>
          <label class="full-row">Notes<textarea name="notes" rows="3">{escape(values['notes'])}</textarea></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post reversal</button>
            <a class="button secondary" href="/dashboard/payments/{receipt.id}/">Back to receipt</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Reverse receipt", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def vendor_payment_reversal_new(request: HttpRequest, payment_id: int) -> HttpResponse:
    payment = owned_vendor_payment(request, payment_id)
    currency = "$" if payment.market == "US" else RUPEE_SYMBOL
    available = (payment.amount - vendor_payment_amount_reversed(payment)).quantize(Decimal("0.01"))
    values = {"reversal_date": f"{timezone.localdate():%Y-%m-%d}", "amount": f"{available}", "reason": "Wrong vendor payment posting", "notes": ""}
    error = ""
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        try:
            reversal = post_vendor_payment_reversal(
                request,
                payment=payment,
                reversal_date=parse_form_date(values["reversal_date"]),
                amount=decimal_value(values["amount"]),
                reason=values["reason"],
                notes=values["notes"],
            )
            audit_log(request, "payment_reversal.created", "PaymentReversal", reversal.id, f"Reversed vendor payment {payment.id}")
            return redirect(f"/dashboard/reversals/{reversal.id}/")
        except ValueError as exc:
            error = str(exc)
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Correction</p>
        <h1>Reverse vendor payment</h1>
        <p class="account-copy">This restores payable/cash balances with a reversal voucher. Available to reverse: {escape(money(available, currency))}.</p>
        {error_html}
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Reversal date<input name="reversal_date" type="date" value="{escape(values['reversal_date'])}" required /></label>
          <label>Amount<input name="amount" type="number" min="0.01" max="{available}" step="0.01" value="{escape(values['amount'])}" required /></label>
          <label class="full-row">Reason<input name="reason" value="{escape(values['reason'])}" required /></label>
          <label class="full-row">Notes<textarea name="notes" rows="3">{escape(values['notes'])}</textarea></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post reversal</button>
            <a class="button secondary" href="/dashboard/expenses/{payment.bill.id}/">Back to vendor bill</a>
          </div>
        </form>
      </section>
    </main>
    """
    return page_shell("Reverse vendor payment", body, request)


@login_required
@require_GET
def payment_reversal_detail(request: HttpRequest, reversal_id: int) -> HttpResponse:
    reversal = owned_reversal(request, reversal_id)
    currency = "$" if reversal.market == "US" else RUPEE_SYMBOL
    source = "Customer receipt" if reversal.customer_receipt_id else "Vendor payment"
    source_link = f"/dashboard/payments/{reversal.customer_receipt_id}/" if reversal.customer_receipt_id else f"/dashboard/expenses/{reversal.vendor_payment.bill_id}/"
    voucher_action = f'<a class="button secondary" href="/dashboard/vouchers/{reversal.voucher.id}/">Open voucher</a>' if reversal.voucher else ""
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Payment reversal</p>
        <h1>{escape(reversal.reversal_number)}</h1>
        <p>{escape(money(reversal.amount, currency))} reversed for {escape(reversal.party_name)}.</p>
        <div class="hero-actions">
          <a class="button primary" href="{source_link}">Open source</a>
          {voucher_action}
        </div>
      </section>
      <section class="report-kpi-grid">
        <article><span>Type</span><strong>{escape(source)}</strong></article>
        <article><span>Date</span><strong>{reversal.reversal_date:%d %b %Y}</strong></article>
        <article><span>Amount</span><strong>{escape(money(reversal.amount, currency))}</strong></article>
        <article><span>Journal</span><strong>{journal_entry_link(reversal.journal_entry)}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Reason</p><h2>{escape(reversal.reason)}</h2><p>{escape(reversal.notes or 'No additional note saved.')}</p></div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Accounting</p><h2>Reversal voucher ledger lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{voucher_ledger_table(reversal.voucher, currency)}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Payment reversal", body, request)


@login_required
@require_GET
def voucher_detail(request: HttpRequest, voucher_id: int) -> HttpResponse:
    voucher = owned_voucher(request, voucher_id)
    currency = "$" if voucher.market == "US" else RUPEE_SYMBOL
    source_links = []
    for invoice in Invoice.objects.filter(account_q(request), sales_voucher=voucher)[:5]:
        source_links.append(f'<a class="button secondary" href="/dashboard/invoices/{invoice.id}/accounting/">Invoice {escape(invoice_number(invoice))}</a>')
    for receipt in PaymentReceipt.objects.filter(account_q(request), voucher=voucher)[:5]:
        source_links.append(f'<a class="button secondary" href="/dashboard/payments/{receipt.id}/">Receipt {escape(receipt.reference or str(receipt.id))}</a>')
    for credit_note in CustomerCreditNote.objects.filter(account_q(request), voucher=voucher)[:5]:
        source_links.append(f'<a class="button secondary" href="/dashboard/credit-notes/{credit_note.id}/">Credit note {escape(credit_note.credit_note_number)}</a>')
    for debit_note in VendorDebitNote.objects.filter(account_q(request), voucher=voucher)[:5]:
        source_links.append(f'<a class="button secondary" href="/dashboard/debit-notes/{debit_note.id}/">Debit note {escape(debit_note.debit_note_number)}</a>')
    for reversal in PaymentReversal.objects.filter(account_q(request), voucher=voucher)[:5]:
        source_links.append(f'<a class="button secondary" href="/dashboard/reversals/{reversal.id}/">Reversal {escape(reversal.reversal_number)}</a>')
    for bill in VendorBill.objects.filter(account_q(request)).filter(Q(voucher=voucher) | Q(payment_voucher=voucher))[:5]:
        source_links.append(f'<a class="button secondary" href="/dashboard/expenses/{bill.id}/">Bill {escape(bill.reference or bill.vendor_name)}</a>')
    for payment in VendorBillPayment.objects.filter(account_q(request), voucher=voucher).select_related("bill")[:5]:
        source_links.append(f'<a class="button secondary" href="/dashboard/expenses/{payment.bill.id}/">Vendor payment {escape(payment.reference or str(payment.id))}</a>')
    source_html = "".join(source_links) or '<span class="form-hint">No source document linked.</span>'
    journal_action = f'<a class="button secondary" href="/dashboard/accounting/journal/{voucher.journal_entry.id}/">Open journal</a>' if voucher.journal_entry else ""
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Voucher</p>
        <h1>{escape(voucher.voucher_number)}</h1>
        <p>{escape(voucher.get_voucher_type_display())} for {escape(voucher.party_name or 'No party')} on {voucher.voucher_date:%d %b %Y}. Total {escape(money(voucher.total_amount, currency))}.</p>
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/vouchers/new/">Post another voucher</a>
          {journal_action}
          <a class="button secondary" href="/dashboard/search/?type=vouchers&q={quote_plus(voucher.voucher_number)}">Search</a>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Source</p><h2>Linked records</h2><div class="dashboard-actions">{source_html}</div></div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Ledger</p><h2>Debit and credit lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{voucher_ledger_table(voucher, currency)}</tbody>
          </table>
        </div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Inventory</p><h2>Stock lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Item</th><th>Location</th><th>Description</th><th>Qty</th><th>Rate</th><th>Amount</th></tr></thead>
            <tbody>{voucher_inventory_table(voucher, currency)}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Voucher detail", body, request)


@login_required
@require_GET
def journal_detail(request: HttpRequest, entry_id: int) -> HttpResponse:
    entry = owned_journal_entry(request, entry_id)
    currency = "$" if entry.market == "US" else RUPEE_SYMBOL
    voucher_links = "".join(f'<a class="button secondary" href="/dashboard/vouchers/{voucher.id}/">{escape(voucher.voucher_number)}</a>' for voucher in entry.vouchers.all())
    receipt_links = "".join(f'<a class="button secondary" href="/dashboard/payments/{receipt.id}/">Receipt {escape(receipt.reference or str(receipt.id))}</a>' for receipt in entry.payment_receipts.all())
    credit_note_links = "".join(f'<a class="button secondary" href="/dashboard/credit-notes/{credit_note.id}/">Credit note {escape(credit_note.credit_note_number)}</a>' for credit_note in entry.customer_credit_notes.all())
    debit_note_links = "".join(f'<a class="button secondary" href="/dashboard/debit-notes/{debit_note.id}/">Debit note {escape(debit_note.debit_note_number)}</a>' for debit_note in entry.vendor_debit_notes.all())
    reversal_links = "".join(f'<a class="button secondary" href="/dashboard/reversals/{reversal.id}/">Reversal {escape(reversal.reversal_number)}</a>' for reversal in entry.payment_reversals.all())
    bill_links = "".join(f'<a class="button secondary" href="/dashboard/expenses/{bill.id}/">Bill {escape(bill.reference or bill.vendor_name)}</a>' for bill in entry.vendor_bills.all())
    source_html = voucher_links + receipt_links + credit_note_links + debit_note_links + reversal_links + bill_links or '<span class="form-hint">Manual or source record not linked.</span>'
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Journal entry</p>
        <h1>{escape(entry.memo)}</h1>
        <p>{entry.entry_date:%d %b %Y} - source {escape(entry.source)} - debit {escape(money(entry.total_debit, currency))} / credit {escape(money(entry.total_credit, currency))}.</p>
        <div class="hero-actions">
          <a class="button primary" href="/dashboard/accounting/journal/new/">Record journal</a>
          <a class="button secondary" href="/dashboard/#accounting">Back to accounting</a>
        </div>
      </section>
      <section class="report-kpi-grid">
        <article><span>Status</span><strong>{'Balanced' if entry.is_balanced else 'Out of balance'}</strong></article>
        <article><span>Posted</span><strong>{'Yes' if entry.is_posted else 'No'}</strong></article>
        <article><span>Total debit</span><strong>{escape(money(entry.total_debit, currency))}</strong></article>
        <article><span>Total credit</span><strong>{escape(money(entry.total_credit, currency))}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Source</p><h2>Linked records</h2><div class="dashboard-actions">{source_html}</div></div>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Ledger</p><h2>Journal lines</h2></div>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Code</th><th>Account</th><th>Description</th><th>Debit</th><th>Credit</th></tr></thead>
            <tbody>{journal_lines_table(entry, currency)}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Journal detail", body, request)


# ---------------------------------------------------------------------------
# Feature registry + knowledge base — the single source of truth that powers
# the top-bar search, the search results page and the Help Center, so a user
# can reach every function (and its how-to) just by typing what they want.
# ---------------------------------------------------------------------------

FEATURE_REGISTRY = [
    {"title": "Dashboard", "url": "/dashboard/", "category": "Overview", "keywords": "home overview summary kpi receivables payables", "staff": False},
    {"title": "Workflows guide", "url": "/dashboard/workflows/", "category": "Overview", "keywords": "workflow steps how to guide process order", "staff": False},
    {"title": "Create invoice", "url": "/dashboard/invoices/new/", "category": "Sales", "keywords": "invoice tax invoice new sales bill gst create document", "staff": False},
    {"title": "Create quotation", "url": "/dashboard/invoices/new/", "category": "Sales", "keywords": "quotation quote estimate proposal price offer", "staff": False},
    {"title": "Create proforma invoice", "url": "/dashboard/invoices/new/", "category": "Sales", "keywords": "proforma pro forma advance payment request", "staff": False},
    {"title": "Payments received", "url": "/dashboard/payments/new/", "category": "Sales", "keywords": "payment receipt collect money received customer paid", "staff": False},
    {"title": "Customer ledger", "url": "/dashboard/ledger/customers/", "category": "Sales", "keywords": "customer ledger statement receivable account balance", "staff": False},
    {"title": "Expenses & bills", "url": "/dashboard/expenses/new/", "category": "Purchases", "keywords": "expense vendor bill purchase pay supplier cost spend", "staff": False},
    {"title": "Upload expense receipt", "url": "/dashboard/expenses/upload/", "category": "Purchases", "keywords": "upload scan receipt ocr bill photo pdf capture", "staff": False},
    {"title": "Vendor ledger", "url": "/dashboard/ledger/vendors/", "category": "Purchases", "keywords": "vendor ledger statement payable supplier balance", "staff": False},
    {"title": "Vouchers", "url": "/dashboard/vouchers/new/", "category": "Accounting", "keywords": "voucher journal payment receipt contra entry manual", "staff": False},
    {"title": "Chart of accounts", "url": "/dashboard/#accounting", "category": "Accounting", "keywords": "chart accounts ledger codes account head", "staff": False},
    {"title": "Inventory & stock", "url": "/dashboard/inventory/", "category": "Inventory", "keywords": "inventory stock products items fifo goods reorder", "staff": False},
    {"title": "Reports", "url": "/dashboard/reports/", "category": "Reports", "keywords": "reports profit loss balance sheet trial balance tax gst aging cash flow", "staff": False},
    {"title": "Audit trail", "url": "/dashboard/audit/", "category": "Reports", "keywords": "audit trail history log activity changes who", "staff": False},
    {"title": "Bank reconciliation", "url": "/dashboard/reconciliation/", "category": "Tools", "keywords": "reconcile bank reconciliation statement match", "staff": False},
    {"title": "AI assistant", "url": "/dashboard/ai/", "category": "Tools", "keywords": "ai assistant insights help ask question", "staff": False},
    {"title": "Search records", "url": "/dashboard/search/", "category": "Tools", "keywords": "search find records lookup", "staff": False},
    {"title": "Business setup", "url": "/dashboard/setup/", "category": "Settings", "keywords": "setup onboarding configure start getting started", "staff": False},
    {"title": "Business profile", "url": "/dashboard/business-profile/", "category": "Settings", "keywords": "profile business gstin logo bank details address", "staff": False},
    {"title": "Billing & plan", "url": "/dashboard/billing/pro/", "category": "Settings", "keywords": "billing plan pro upgrade subscription pay limit", "staff": False},
    {"title": "Help & guides", "url": "/dashboard/help/", "category": "Help", "keywords": "help support knowledge guide how to docs library faq learn", "staff": False},
    {"title": "GSTN e-Invoice API", "url": "/dashboard/gstn/", "category": "Settings", "keywords": "gstn gsp einvoice irn api credentials e-invoice", "staff": True},
    {"title": "Monitoring", "url": "/dashboard/monitoring/", "category": "Admin", "keywords": "monitoring health status admin uptime", "staff": True},
]

KB_ARTICLES = [
    {
        "slug": "getting-started",
        "title": "Getting started with RozLedger",
        "category": "Getting started",
        "summary": "Set up your business and create your first invoice in minutes.",
        "keywords": "start setup onboarding first begin new account",
        "action": ("Set up business profile", "/dashboard/business-profile/"),
        "body": """
<p>RozLedger turns your sales and purchases into clean, GST-ready books. Here is the fastest way to get going.</p>
<ol>
<li><strong>Set up your business.</strong> Open <a href="/dashboard/business-profile/">Business profile</a> and add your name, address, GSTIN, logo and bank details. These appear on every invoice.</li>
<li><strong>Create your first document.</strong> Go to <a href="/dashboard/invoices/new/">Create invoice</a>. You can make a quotation, a proforma invoice or a tax invoice from the same screen.</li>
<li><strong>Record money received.</strong> When a customer pays, open <a href="/dashboard/payments/new/">Payments received</a> and link the payment to the invoice.</li>
<li><strong>Track expenses.</strong> Add supplier bills under <a href="/dashboard/expenses/new/">Expenses &amp; bills</a> so your profit is accurate.</li>
<li><strong>Watch your numbers.</strong> <a href="/dashboard/reports/">Reports</a> show profit &amp; loss, GST summary, receivables and more.</li>
</ol>
<p>Everything posts to a proper double-entry ledger automatically, so your accountant gets correct books with no extra work.</p>
""",
    },
    {
        "slug": "find-anything",
        "title": "Find anything with the top search",
        "category": "Getting started",
        "summary": "Use the search bar at the top of every page to jump to any function, record or guide.",
        "keywords": "search find navigate stuck lost where command go to",
        "action": ("Open search", "/dashboard/search/"),
        "body": """
<p>The search box at the top of every page is the fastest way to move around RozLedger. You never have to remember where a feature lives.</p>
<p>Type anything and press <strong>Search</strong>. You will get three kinds of results:</p>
<ul>
<li><strong>Go to</strong> — jump straight to any function or screen (for example type "quotation", "reconcile" or "GST report").</li>
<li><strong>Help articles</strong> — guides that match your words.</li>
<li><strong>Records</strong> — your invoices, bills, receipts, vouchers and notes that match.</li>
</ul>
<p>So if you ever feel stuck, just search for what you want to do — the matching screen and the guide both come up.</p>
""",
    },
    {
        "slug": "create-tax-invoice",
        "title": "Create a GST tax invoice",
        "category": "Invoices",
        "summary": "Make a legal tax invoice that posts to your books and splits GST.",
        "keywords": "invoice tax invoice create gst sales bill line items",
        "action": ("Create invoice", "/dashboard/invoices/new/"),
        "body": """
<p>A tax invoice is the legal GST document you give a customer. It records the sale in your books and the GST you owe.</p>
<ol>
<li>Open <a href="/dashboard/invoices/new/">Create invoice</a> and keep <strong>Document type</strong> as "Tax invoice".</li>
<li>Pick a template and confirm your business details (they come from your profile).</li>
<li>Add the customer, then add one or more line items with description, quantity and rate.</li>
<li>Set the GST rate and, for India, the place of supply and whether it is intra-state or inter-state.</li>
<li>Save. RozLedger posts the sale to your ledger, splits the GST and gives you a shareable link and PDF.</li>
</ol>
<p>You can email or WhatsApp the link, or download the PDF for your records.</p>
""",
    },
    {
        "slug": "quotation-proforma-invoice",
        "title": "Quotations, proforma invoices and tax invoices",
        "category": "Invoices",
        "summary": "The full sales cycle: quote a price, send a proforma, then convert to a tax invoice.",
        "keywords": "quotation quote proforma estimate convert document type sales cycle",
        "action": ("Create a quotation", "/dashboard/invoices/new/"),
        "body": """
<p>RozLedger supports the full sales cycle: <strong>Quotation &rarr; Proforma invoice &rarr; Tax invoice</strong>. All three are made from the <a href="/dashboard/invoices/new/">Create invoice</a> screen using the <strong>Document type</strong> selector.</p>
<ul>
<li><strong>Quotation</strong> (number starts with QTN) — a price offer. It does not affect your books or GST.</li>
<li><strong>Proforma invoice</strong> (PI) — a payment request before supply. Also does not post to your books.</li>
<li><strong>Tax invoice</strong> (RL) — the legal document. This is the only one that records revenue, a receivable and GST.</li>
</ul>
<p>When a customer accepts, open the document on your dashboard and use <strong>Convert to invoice</strong> (a quotation can also become a proforma first). Converting to a tax invoice is the moment it posts to your ledger — so your books only ever count confirmed sales.</p>
""",
    },
    {
        "slug": "gst-cgst-sgst-igst",
        "title": "How GST is calculated: CGST, SGST, IGST",
        "category": "GST & tax",
        "summary": "Intra-state sales split into CGST + SGST; inter-state sales charge IGST.",
        "keywords": "gst cgst sgst igst tax split place of supply intra inter state",
        "action": ("Open GST reports", "/dashboard/reports/"),
        "body": """
<p>RozLedger splits GST automatically based on the place of supply, so your returns are correct.</p>
<ul>
<li><strong>Intra-state</strong> (customer in your state): GST is split into <strong>CGST</strong> and <strong>SGST</strong>, each half of the rate. An 18% invoice becomes 9% CGST + 9% SGST.</li>
<li><strong>Inter-state</strong> (customer in another state): the full rate is charged as <strong>IGST</strong>.</li>
</ul>
<p>On the invoice screen choose the <strong>supply type</strong> and <strong>place of supply</strong>. Each part posts to its own ledger account (CGST/SGST/IGST payable), and the <a href="/dashboard/reports/">GST summary report</a> totals what you owe for the period.</p>
""",
    },
    {
        "slug": "record-payment",
        "title": "Record a payment received",
        "category": "Payments",
        "summary": "Link customer payments to invoices and keep receivables accurate.",
        "keywords": "payment receipt received money collect partial paid customer",
        "action": ("Record a payment", "/dashboard/payments/new/"),
        "body": """
<p>Recording payments keeps your receivables accurate and marks invoices as paid.</p>
<ol>
<li>Open <a href="/dashboard/payments/new/">Payments received</a>.</li>
<li>Choose the customer's tax invoice from the list (quotations and proformas are not shown — only real invoices can receive payment).</li>
<li>Enter the amount (full or partial) and the method, then save.</li>
</ol>
<p>RozLedger reduces the outstanding balance, updates the invoice status, and posts the receipt to your cash/bank ledger. Partial payments are supported — the balance stays open until fully paid.</p>
""",
    },
    {
        "slug": "expenses-bills",
        "title": "Add expenses and vendor bills",
        "category": "Purchases",
        "summary": "Record what you spend so profit and GST input credit are correct.",
        "keywords": "expense vendor bill purchase supplier pay cost upload receipt",
        "action": ("Add an expense", "/dashboard/expenses/new/"),
        "body": """
<p>Recording what you spend makes your profit and GST input credit accurate.</p>
<ol>
<li>Open <a href="/dashboard/expenses/new/">Expenses &amp; bills</a> and add the vendor, category, amount and any GST.</li>
<li>Or use <a href="/dashboard/expenses/upload/">Upload expense receipt</a> to capture a bill from a photo or PDF.</li>
<li>Mark it paid now, or leave it as a bill payable and pay later.</li>
</ol>
<p>See what you owe each supplier in the <a href="/dashboard/ledger/vendors/">Vendor ledger</a>.</p>
""",
    },
    {
        "slug": "inventory",
        "title": "Track inventory and stock",
        "category": "Inventory",
        "summary": "Create products, post stock movements and keep FIFO cost layers.",
        "keywords": "inventory stock products items fifo goods reorder cost",
        "action": ("Open inventory", "/dashboard/inventory/"),
        "body": """
<p>If you sell goods, track stock so your cost of sales is right.</p>
<ol>
<li>Open <a href="/dashboard/inventory/">Inventory &amp; stock</a> and create products or services.</li>
<li>Post stock inward (purchases) and outward (sales). RozLedger keeps FIFO cost layers.</li>
<li>Low-stock items are flagged on your dashboard so you can reorder in time.</li>
</ol>
""",
    },
    {
        "slug": "reports",
        "title": "Understand your reports",
        "category": "Reports",
        "summary": "Profit & loss, balance sheet, GST summary, aging and cash flow — always up to date.",
        "keywords": "reports profit loss balance sheet trial balance gst aging cash flow",
        "action": ("Open reports", "/dashboard/reports/"),
        "body": """
<p><a href="/dashboard/reports/">Reports</a> turn your entries into the numbers you and your accountant need:</p>
<ul>
<li><strong>Profit &amp; loss</strong> — income minus expenses for the period.</li>
<li><strong>Balance sheet &amp; trial balance</strong> — the financial position of the business.</li>
<li><strong>GST summary</strong> — tax collected and payable, ready for returns.</li>
<li><strong>Receivables &amp; payables aging</strong> — who owes you and whom you owe, by age.</li>
<li><strong>Cash flow</strong> — money in and out.</li>
</ul>
<p>Because every invoice, bill and payment posts automatically, these reports are always up to date.</p>
""",
    },
    {
        "slug": "reconcile",
        "title": "Reconcile your bank",
        "category": "Reports",
        "summary": "Match RozLedger against your bank statement to keep books trustworthy.",
        "keywords": "reconcile bank reconciliation statement match audit",
        "action": ("Start reconciling", "/dashboard/reconciliation/"),
        "body": """
<p>Reconciliation makes sure your RozLedger cash/bank balance matches your real bank statement.</p>
<ol>
<li>Open <a href="/dashboard/reconciliation/">Bank reconciliation</a>.</li>
<li>Match each statement line to a recorded payment or receipt.</li>
<li>Anything unmatched shows you a missing or duplicate entry to fix.</li>
</ol>
<p>Reconciling regularly keeps your books trustworthy and audit-ready.</p>
""",
    },
    {
        "slug": "corrections",
        "title": "Corrections: credit notes, debit notes and reversals",
        "category": "Corrections",
        "summary": "Never delete a posted record — correct it with an audit-safe entry.",
        "keywords": "credit note debit note reversal correction cancel refund return adjust",
        "action": ("Search records", "/dashboard/search/"),
        "body": """
<p>Never delete a posted document — correct it. RozLedger keeps an audit-safe trail.</p>
<ul>
<li><strong>Credit note</strong> — reduce or cancel a customer tax invoice (returns, discounts, errors). Open the invoice and choose <strong>Credit note</strong>.</li>
<li><strong>Debit note</strong> — reduce a vendor bill. Open the bill and choose <strong>Debit note</strong>.</li>
<li><strong>Reversal</strong> — undo a wrong payment or receipt.</li>
</ul>
<p>Each correction posts its own entry, so the original record and the fix are both preserved for audit.</p>
""",
    },
    {
        "slug": "gstn-einvoice",
        "title": "Connect GSTN for e-Invoice (IRN)",
        "category": "GST & tax",
        "summary": "Optionally connect a GST Suvidha Provider to generate IRN and signed QR codes.",
        "keywords": "gstn gsp einvoice e-invoice irn api connect turnover",
        "action": None,
        "body": """
<p>For businesses that must issue e-Invoices, RozLedger can connect to the GST network through a GST Suvidha Provider (GSP) to generate the IRN and signed QR code.</p>
<ol>
<li>A staff admin opens <strong>GSTN e-Invoice API</strong> settings and enters the GSP credentials (stored encrypted).</li>
<li>Use <strong>Test authentication</strong> to confirm the connection, and <strong>Validate GSTIN</strong> to check a customer's number.</li>
</ol>
<p>This is optional and only needed if your turnover requires e-Invoicing. Contact support if you are unsure whether it applies to you.</p>
""",
    },
    {
        "slug": "plans-billing",
        "title": "Plans, billing and limits",
        "category": "Account",
        "summary": "Free and paid plans, and how monthly invoice limits work.",
        "keywords": "plan billing pro free upgrade limit subscription price",
        "action": ("Open billing", "/dashboard/billing/pro/"),
        "body": f"""
<p>RozLedger has a free plan and a paid plan.</p>
<ul>
<li><strong>Free</strong> — up to {FREE_MONTHLY_INVOICE_LIMIT} saved invoices per month with full GST features.</li>
<li><strong>Paid</strong> — up to {PAID_MONTHLY_INVOICE_LIMIT} invoices per month.</li>
</ul>
<p>Manage your plan under <a href="/dashboard/billing/pro/">Billing &amp; plan</a>. We always contact you before any paid activation — nothing is charged automatically.</p>
""",
    },
]


def search_features(q: str, is_staff: bool = False) -> list[dict]:
    items = [f for f in FEATURE_REGISTRY if is_staff or not f["staff"]]
    tokens = [t for t in (q or "").lower().split() if t]
    if not tokens:
        return []
    return [f for f in items if any(tok in (f["title"] + " " + f["keywords"]).lower() for tok in tokens)]


def search_articles(q: str) -> list[dict]:
    tokens = [t for t in (q or "").lower().split() if t]
    if not tokens:
        return []
    return [a for a in KB_ARTICLES if any(tok in (a["title"] + " " + a["summary"] + " " + a["keywords"]).lower() for tok in tokens)]


def get_article(slug: str) -> dict | None:
    for article in KB_ARTICLES:
        if article["slug"] == slug:
            return article
    return None


@login_required
@require_GET
def global_search(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    q = clean_text(request.GET.get("q"), max_length=120)
    record_type = clean_text(request.GET.get("type"), "all", 30)
    rows = []
    if record_type in {"all", "invoices"}:
        queryset = Invoice.objects.filter(account_q(request))
        if q:
            queryset = queryset.filter(Q(client_name__icontains=q) | Q(service_name__icontains=q) | Q(business_name__icontains=q) | Q(total_text__icontains=q))
        for invoice in queryset[:20]:
            rows.append(("Invoice", invoice_number(invoice), invoice.client_name, invoice_total_display(invoice), f"/invoice/{invoice.public_token}/"))
    if record_type in {"all", "bills"}:
        queryset = VendorBill.objects.filter(account_q(request))
        if q:
            queryset = queryset.filter(Q(vendor_name__icontains=q) | Q(category__icontains=q) | Q(reference__icontains=q) | Q(notes__icontains=q))
        for bill in queryset[:20]:
            rows.append(("Vendor bill", bill.reference or str(bill.id), bill.vendor_name, money(bill.amount, currency), f"/dashboard/expenses/{bill.id}/"))
    if record_type in {"all", "payments"}:
        queryset = PaymentReceipt.objects.filter(account_q(request))
        if q:
            queryset = queryset.filter(Q(payer_name__icontains=q) | Q(reference__icontains=q) | Q(notes__icontains=q) | Q(invoice__client_name__icontains=q))
        for receipt in queryset[:20]:
            rows.append(("Receipt", receipt.reference or str(receipt.id), receipt.payer_name, money(receipt.amount, currency), f"/dashboard/payments/{receipt.id}/"))
    if record_type in {"all", "credits"}:
        queryset = CustomerCreditNote.objects.filter(account_q(request))
        if q:
            queryset = queryset.filter(Q(credit_note_number__icontains=q) | Q(client_name__icontains=q) | Q(reason__icontains=q) | Q(notes__icontains=q) | Q(invoice__client_name__icontains=q))
        for credit_note in queryset[:20]:
            rows.append(("Credit note", credit_note.credit_note_number, credit_note.client_name, money(credit_note.total_amount, currency), f"/dashboard/credit-notes/{credit_note.id}/"))
    if record_type in {"all", "debits"}:
        queryset = VendorDebitNote.objects.filter(account_q(request))
        if q:
            queryset = queryset.filter(Q(debit_note_number__icontains=q) | Q(vendor_name__icontains=q) | Q(reason__icontains=q) | Q(notes__icontains=q) | Q(bill__vendor_name__icontains=q))
        for debit_note in queryset[:20]:
            rows.append(("Debit note", debit_note.debit_note_number, debit_note.vendor_name, money(debit_note.amount, currency), f"/dashboard/debit-notes/{debit_note.id}/"))
    if record_type in {"all", "reversals"}:
        queryset = PaymentReversal.objects.filter(account_q(request))
        if q:
            queryset = queryset.filter(Q(reversal_number__icontains=q) | Q(party_name__icontains=q) | Q(reason__icontains=q) | Q(notes__icontains=q))
        for reversal in queryset[:20]:
            rows.append(("Reversal", reversal.reversal_number, reversal.party_name, money(reversal.amount, currency), f"/dashboard/reversals/{reversal.id}/"))
    if record_type in {"all", "vouchers"}:
        queryset = Voucher.objects.filter(account_q(request))
        if q:
            queryset = queryset.filter(Q(voucher_number__icontains=q) | Q(party_name__icontains=q) | Q(narration__icontains=q))
        for voucher in queryset[:20]:
            rows.append(("Voucher", voucher.voucher_number, voucher.party_name, money(voucher.total_amount, currency), f"/dashboard/vouchers/{voucher.id}/"))
    type_options = "".join(
        f'<option value="{value}" {"selected" if record_type == value else ""}>{label}</option>'
        for value, label in [("all", "All records"), ("invoices", "Invoices"), ("bills", "Vendor bills"), ("payments", "Receipts"), ("credits", "Credit notes"), ("debits", "Debit notes"), ("reversals", "Reversals"), ("vouchers", "Vouchers")]
    )
    result_rows = "".join(
        f"""
        <tr>
          <td>{escape(kind)}</td>
          <td><a href="{escape(link)}">{escape(reference)}</a></td>
          <td>{escape(name)}</td>
          <td class="amount-cell">{escape(amount)}</td>
        </tr>
        """
        for kind, reference, name, amount, link in rows
    ) or '<tr><td colspan="4" class="empty-report-row">No matching records.</td></tr>'

    feature_hits = search_features(q, request.user.is_staff) if q else []
    article_hits = search_articles(q) if q else []
    feature_section = ""
    if feature_hits:
        feature_cards = "".join(
            f'<a class="search-jump-card" href="{escape(feature["url"])}"><strong>{escape(feature["title"])}</strong><span>{escape(feature["category"])}</span></a>'
            for feature in feature_hits[:12]
        )
        feature_section = f"""
      <section class="dashboard-section">
        <h2 class="search-group-title">Go to</h2>
        <div class="search-jump-grid">{feature_cards}</div>
      </section>"""
    article_section = ""
    if article_hits:
        article_cards = "".join(
            f'<a class="search-jump-card" href="/dashboard/help/{escape(article["slug"])}/"><strong>{escape(article["title"])}</strong><span>{escape(article["summary"])}</span></a>'
            for article in article_hits[:8]
        )
        article_section = f"""
      <section class="dashboard-section">
        <h2 class="search-group-title">Help articles</h2>
        <div class="search-jump-grid">{article_cards}</div>
      </section>"""

    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Search</p>
        <h1>Find anything</h1>
        <p>Jump to any function, open a help guide, or find your invoices, bills, receipts and vouchers — all from one search.</p>
      </section>
      <section class="dashboard-section">
        <form method="get" class="dashboard-form invoice-server-form">
          <label>Search<input name="q" value="{escape(q)}" placeholder="Try: quotation, GST report, reconcile, a client name…" /></label>
          <label>Type<select name="type">{type_options}</select></label>
          <button class="button primary" type="submit">Search</button>
        </form>
      </section>
      {feature_section}
      {article_section}
      <section class="report-section">
        <h2 class="search-group-title">Records</h2>
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Type</th><th>Reference</th><th>Name</th><th>Amount</th></tr></thead>
            <tbody>{result_rows}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Search", body, request)


@login_required
@require_GET
def help_center(request: HttpRequest) -> HttpResponse:
    q = clean_text(request.GET.get("q"), max_length=120)
    articles = search_articles(q) if q else KB_ARTICLES
    categories: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for article in articles:
        if article["category"] not in grouped:
            categories.append(article["category"])
            grouped[article["category"]] = []
        grouped[article["category"]].append(article)
    if articles:
        sections = "".join(
            f"""
      <section class="dashboard-section">
        <h2 class="search-group-title">{escape(category)}</h2>
        <div class="search-jump-grid">{''.join(f'<a class="search-jump-card" href="/dashboard/help/{escape(item["slug"])}/"><strong>{escape(item["title"])}</strong><span>{escape(item["summary"])}</span></a>' for item in grouped[category])}</div>
      </section>"""
            for category in categories
        )
    else:
        sections = '<section class="dashboard-section"><p class="empty-report-row">No guides matched. Try another word, or contact support below.</p></section>'
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Help &amp; guides</p>
        <h1>Knowledge library</h1>
        <p>Step-by-step guides for every part of RozLedger. Search from the bar at the top of any page, or browse by topic below.</p>
      </section>
      <section class="dashboard-section">
        <form method="get" class="dashboard-form invoice-server-form">
          <label>Search guides<input name="q" value="{escape(q)}" placeholder="invoice, GST, payment, reconcile…" /></label>
          <button class="button primary" type="submit">Search</button>
        </form>
      </section>
      {sections}
      <section class="dashboard-section help-contact">
        <h2 class="search-group-title">Still need help?</h2>
        <p>Chat with us on <a href="https://wa.me/919516022222" rel="noopener">WhatsApp</a> or visit the <a href="/contact/">Contact</a> page — we are happy to help.</p>
      </section>
    </main>
    """
    return page_shell("Help & guides", body, request)


@login_required
@require_GET
def help_article(request: HttpRequest, slug: str) -> HttpResponse:
    article = get_article(slug)
    if not article:
        raise Http404("Help article not found")
    action = article.get("action")
    action_html = ""
    if action:
        label, url = action
        action_html = f'<a class="button primary" href="{escape(url)}">{escape(label)}</a>'
    related = search_features(article["keywords"], request.user.is_staff)[:6]
    related_html = ""
    if related:
        related_cards = "".join(
            f'<a class="search-jump-card" href="{escape(feature["url"])}"><strong>{escape(feature["title"])}</strong><span>{escape(feature["category"])}</span></a>'
            for feature in related
        )
        related_html = f"""
      <section class="dashboard-section">
        <h2 class="search-group-title">Related screens</h2>
        <div class="search-jump-grid">{related_cards}</div>
      </section>"""
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">{escape(article["category"])}</p>
        <h1>{escape(article["title"])}</h1>
        <p>{escape(article["summary"])}</p>
      </section>
      <section class="dashboard-section">
        <article class="help-article">{article["body"]}</article>
        <div class="help-article-actions">{action_html}<a class="button ghost" href="/dashboard/help/">All guides</a></div>
      </section>
      {related_html}
      <section class="dashboard-section help-contact">
        <p>Did this help? If not, chat on <a href="https://wa.me/919516022222" rel="noopener">WhatsApp</a> or visit <a href="/contact/">Contact</a>.</p>
      </section>
    </main>
    """
    return page_shell(article["title"], body, request)


@login_required
@require_GET
def audit_trail(request: HttpRequest) -> HttpResponse:
    q = clean_text(request.GET.get("q"), max_length=120)
    action = clean_text(request.GET.get("action"), max_length=80)
    logs = AuditLog.objects.filter(account_q(request))
    if q:
        logs = logs.filter(Q(summary__icontains=q) | Q(object_type__icontains=q) | Q(object_id__icontains=q) | Q(action__icontains=q))
    if action:
        logs = logs.filter(action=action)
    action_values = list(AuditLog.objects.filter(account_q(request)).values_list("action", flat=True).distinct().order_by("action"))
    action_options = '<option value="">All actions</option>' + "".join(f'<option value="{escape(value)}" {"selected" if action == value else ""}>{escape(value)}</option>' for value in action_values)
    rows = "".join(
        f"""
        <tr>
          <td>{timezone.localtime(log.created_at):%d %b %Y %I:%M %p}</td>
          <td>{escape(log.action)}</td>
          <td>{escape(log.object_type)} #{escape(log.object_id)}</td>
          <td>{escape(log.summary)}</td>
          <td>{escape(log.ip_address)}</td>
        </tr>
        """
        for log in logs[:100]
    ) or '<tr><td colspan="5" class="empty-report-row">No audit entries found.</td></tr>'
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Audit</p>
        <h1>Audit trail</h1>
        <p>Review key user actions, object references, timestamps and source IPs.</p>
      </section>
      <section class="dashboard-section">
        <form method="get" class="dashboard-form invoice-server-form">
          <label>Search<input name="q" value="{escape(q)}" placeholder="Action, object or summary" /></label>
          <label>Action<select name="action">{action_options}</select></label>
          <button class="button primary" type="submit">Filter</button>
        </form>
      </section>
      <section class="report-section">
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Time</th><th>Action</th><th>Object</th><th>Summary</th><th>IP</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </section>
    </main>
    """
    return page_shell("Audit trail", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def reconciliation(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    currency = "$" if current_market(request) == "US" else RUPEE_SYMBOL
    source = request.POST if request.method == "POST" else request.GET
    account_code = clean_text(source.get("account"), "1010", 20)
    if account_code not in {"1000", "1010"}:
        account_code = "1010"
    statement_balance = decimal_value(source.get("statement_balance"))
    entries = JournalEntry.objects.filter(account_q(request), is_posted=True)
    lines = JournalLine.objects.filter(entry__in=entries, account__code=account_code).select_related("entry", "account").order_by("entry__entry_date", "entry__created_at", "id")
    start_date = parse_form_date(source.get("from", "")) if source.get("from") else None
    end_date = parse_form_date(source.get("to", "")) if source.get("to") else None
    if start_date:
        lines = lines.filter(entry__entry_date__gte=start_date)
    if end_date:
        lines = lines.filter(entry__entry_date__lte=end_date)
    running = Decimal("0")
    rows = []
    selected_ids = set(source.getlist("line_ids")) if hasattr(source, "getlist") else set()
    selected_total = Decimal("0")
    saved_message = ""
    for line in lines:
        running = (running + line.debit - line.credit).quantize(Decimal("0.01"))
        line_amount = (line.debit - line.credit).quantize(Decimal("0.01"))
        checked = str(line.id) in selected_ids
        if checked:
            selected_total += line_amount
        rows.append(
            f"""
            <tr>
              <td><input type="checkbox" name="line_ids" value="{line.id}" {'checked' if checked else ''} /></td>
              <td>{line.entry.entry_date:%d %b %Y}</td>
              <td><a href="/dashboard/accounting/journal/{line.entry.id}/">{escape(line.entry.memo)}</a></td>
              <td>{escape(line.description)}</td>
              <td class="amount-cell">{escape(money(line.debit, currency)) if line.debit else '-'}</td>
              <td class="amount-cell">{escape(money(line.credit, currency)) if line.credit else '-'}</td>
              <td class="amount-cell">{escape(money(running, currency))}</td>
            </tr>
            """
        )
    if request.method == "POST":
        account = account_by_code(request, account_code)
        difference_for_save = (statement_balance - running).quantize(Decimal("0.01"))
        session = ReconciliationSession.objects.create(
            market=current_market(request),
            owner=request.user,
            owner_email=current_account_email(request),
            account=account,
            statement_date=end_date or timezone.localdate(),
            date_from=start_date,
            date_to=end_date,
            statement_balance=statement_balance,
            ledger_balance=running,
            difference=difference_for_save,
            notes=clean_text(request.POST.get("notes")),
        )
        selected_lines = JournalLine.objects.filter(id__in=[int(value) for value in selected_ids if str(value).isdigit()], entry__in=entries, account__code=account_code)
        ReconciliationLine.objects.bulk_create(
            ReconciliationLine(session=session, journal_line=line, amount=(line.debit - line.credit).quantize(Decimal("0.01")))
            for line in selected_lines
        )
        audit_log(request, "reconciliation.saved", "ReconciliationSession", session.id, f"Saved {account.code} reconciliation with difference {difference_for_save}")
        saved_message = f'<p class="dashboard-notice">Reconciliation saved with {selected_lines.count()} checked transaction(s).</p>'
    table_rows = "".join(rows) or '<tr><td colspan="7" class="empty-report-row">No cash/bank transactions found.</td></tr>'
    difference = (statement_balance - running).quantize(Decimal("0.01")) if source.get("statement_balance") else Decimal("0")
    account_options_html = "".join(f'<option value="{code}" {"selected" if account_code == code else ""}>{label}</option>' for code, label in [("1010", "Bank account"), ("1000", "Cash on hand")])
    recent_sessions = "".join(
        f"""
        <article class="dashboard-card compact-card">
          <div>
            <span>{session.statement_date:%d %b %Y}</span>
            <h2>{escape(session.account.name)}</h2>
            <p>Statement {escape(money(session.statement_balance, currency))} / Ledger {escape(money(session.ledger_balance, currency))} / Difference {escape(money(session.difference, currency))}</p>
          </div>
        </article>
        """
        for session in ReconciliationSession.objects.filter(account_q(request)).select_related("account")[:6]
    ) or '<article class="dashboard-card empty-state compact-card"><span>Reconciliation</span><h2>No saved reconciliations yet</h2><p>Tick transactions and save your first reconciliation.</p></article>'
    body = f"""
    <main class="dashboard-shell">
      <section class="dashboard-hero reports-hero">
        <p class="eyebrow">Reconciliation</p>
        <h1>Bank and cash reconciliation</h1>
        <p>Compare posted cash/bank ledger balance against your bank or cash statement balance.</p>
        {saved_message}
      </section>
      <section class="dashboard-section">
        <form method="get" class="dashboard-form invoice-server-form">
          <label>Account<select name="account">{account_options_html}</select></label>
          <label>From<input name="from" type="date" value="{escape(source.get('from', ''))}" /></label>
          <label>To<input name="to" type="date" value="{escape(source.get('to', ''))}" /></label>
          <label>Statement balance<input name="statement_balance" type="number" step="0.01" value="{escape(source.get('statement_balance', ''))}" /></label>
          <button class="button primary" type="submit">Reconcile</button>
        </form>
      </section>
      <section class="report-kpi-grid">
        <article><span>Ledger balance</span><strong>{escape(money(running, currency))}</strong></article>
        <article><span>Statement balance</span><strong>{escape(money(statement_balance, currency)) if source.get('statement_balance') else 'Not entered'}</strong></article>
        <article><span>Difference</span><strong>{escape(money(difference, currency)) if source.get('statement_balance') else 'Not calculated'}</strong></article>
        <article><span>Account</span><strong>{'Bank' if account_code == '1010' else 'Cash'}</strong></article>
      </section>
      <section class="report-section">
        <div class="section-head"><p class="eyebrow">Ledger lines</p><h2>{'Bank account' if account_code == '1010' else 'Cash on hand'}</h2></div>
        <form method="post">
          {csrf_input(request)}
          <input type="hidden" name="account" value="{escape(account_code)}" />
          <input type="hidden" name="from" value="{escape(source.get('from', ''))}" />
          <input type="hidden" name="to" value="{escape(source.get('to', ''))}" />
          <input type="hidden" name="statement_balance" value="{escape(source.get('statement_balance', ''))}" />
        <div class="report-table-wrap">
          <table class="report-table">
            <thead><tr><th>Clear</th><th>Date</th><th>Memo</th><th>Description</th><th>In</th><th>Out</th><th>Running balance</th></tr></thead>
            <tbody>{table_rows}</tbody>
          </table>
        </div>
          <div class="dashboard-actions section-actions">
            <label class="full-row">Notes<input name="notes" placeholder="Statement page, bank reference or reconciliation note" /></label>
            <button class="button primary" type="submit">Save reconciliation</button>
            <span class="form-hint">Checked net amount: {escape(money(selected_total, currency))}</span>
          </div>
        </form>
      </section>
      <section class="dashboard-section">
        <div class="section-head"><p class="eyebrow">History</p><h2>Saved reconciliations</h2></div>
        <div class="dashboard-grid">{recent_sessions}</div>
      </section>
    </main>
    """
    return page_shell("Reconciliation", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def payment_new(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    clients = list(Client.objects.filter(account_q(request)).order_by("name"))
    open_invoices = list(Invoice.objects.filter(account_q(request), document_type="tax_invoice").exclude(status="paid").order_by("-created_at"))
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
        "amount": f"{invoice_outstanding_amount(invoice)}" if invoice else "",
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
            values["amount"] = values["amount"] or f"{invoice_outstanding_amount(invoice)}"
        amount = decimal_value(values["amount"])
        if not values["payer_name"]:
            error = "Payer name is required."
        elif amount <= 0:
            error = "Payment amount must be greater than zero."
        else:
            try:
                receipt = post_customer_receipt(
                    request,
                    invoice=invoice,
                    payment_date=parse_form_date(values["payment_date"]),
                    payer_name=values["payer_name"],
                    amount=amount,
                    method=values["method"] if values["method"] in {"bank", "cash", "upi", "card", "check", "paypal", "stripe", "other"} else "bank",
                    reference=values["reference"],
                    notes=values["notes"],
                )
                audit_log(request, "payment_receipt.created", "PaymentReceipt", receipt.id, f"Recorded payment from {receipt.payer_name} for {receipt.amount}")
                return redirect("/dashboard/#receipts")
            except ValueError as exc:
                error = str(exc)

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    linked_invoice_html = ""
    if invoice:
        linked_invoice_html = f"""
        <div class="selected-invoice-panel">
          <span>Selected invoice</span>
          <strong>{escape(invoice_number(invoice))}</strong>
          <p>{escape(invoice.client_name)} - balance {escape(money(invoice_outstanding_amount(invoice), invoice.currency_symbol))} / total {escape(invoice_total_display(invoice))} - {invoice.created_at:%d %b %Y}</p>
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
            bill = post_expense_bill(
                request,
                bill_date=parse_form_date(values["bill_date"]),
                due_date=parse_form_date(values["due_date"]) if values["due_date"] else None,
                vendor_name=values["vendor_name"],
                category=values["category"] or expense_account.name,
                amount=amount,
                status=status,
                payment_method=values["payment_method"],
                expense_account=expense_account,
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
        <div class="upload-cta">
          <p>Have a paper or photo bill? Upload it and RozLedger will prefill the vendor, amount and date for you.</p>
          <a class="button secondary" href="/dashboard/expenses/upload/">Upload bill photo or PDF</a>
        </div>
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
def vendor_bill_payment(request: HttpRequest) -> HttpResponse:
    ensure_default_chart(request)
    unpaid_bills = list(VendorBill.objects.filter(account_q(request), status__in=["unpaid", "partially_paid"]).order_by("due_date", "-bill_date"))
    bill = None
    bill_id = clean_text(request.GET.get("bill") or request.POST.get("bill_id"), max_length=20)
    if bill_id:
        bill = next((candidate for candidate in unpaid_bills if str(candidate.id) == bill_id), None)
    values = {
        "payment_date": f"{timezone.localdate():%Y-%m-%d}",
        "bill_id": str(bill.id) if bill else "",
        "vendor_name": bill.vendor_name if bill else "",
        "amount": f"{vendor_bill_outstanding_amount(bill)}" if bill else "",
        "method": "bank",
        "reference": "",
        "notes": "",
    }
    error = ""
    if request.method == "POST":
        values = {key: clean_text(request.POST.get(key), max_length=240) for key in values}
        bill = next((candidate for candidate in unpaid_bills if str(candidate.id) == values["bill_id"]), None)
        amount = decimal_value(values["amount"])
        if bill:
            values["vendor_name"] = bill.vendor_name
        if bill is None:
            error = "Select a valid open vendor bill."
        elif amount <= 0:
            error = "Payment amount must be greater than zero."
        else:
            try:
                voucher = post_vendor_bill_payment(
                    request,
                    bill=bill,
                    payment_date=parse_form_date(values["payment_date"]),
                    amount=amount,
                    method=values["method"],
                    reference=values["reference"],
                    notes=values["notes"],
                )
                audit_log(request, "vendor_bill.paid", "VendorBill", bill.id, f"Paid {bill.vendor_name} using {voucher.voucher_number}")
                return redirect("/dashboard/#payables")
            except ValueError as exc:
                error = str(exc)

    selected_bill_html = ""
    if bill:
        selected_bill_html = f"""
        <div class="selected-invoice-panel">
          <span>Selected vendor bill</span>
          <strong>{escape(bill.vendor_name)}</strong>
          <p>{escape(bill.reference or bill.category)} - balance {escape(money(vendor_bill_outstanding_amount(bill), '$' if current_market(request) == 'US' else RUPEE_SYMBOL))} / total {escape(money(bill.amount, '$' if current_market(request) == 'US' else RUPEE_SYMBOL))} - bill date {bill.bill_date:%d %b %Y}</p>
        </div>
        """
    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Accounts payable</p>
        <h1>Pay vendor bill</h1>
        <p class="account-copy">Select an open vendor bill before posting. RozLedger creates a Payment voucher, debits accounts payable and credits cash or bank.</p>
        {error_html}
        {selected_bill_html}
        <form method="post" class="account-form invoice-server-form">
          {csrf_input(request)}
          <label>Vendor bill<select name="bill_id" id="vendor-bill-select">{vendor_bill_options(unpaid_bills, values['bill_id'])}</select></label>
          <label>Date<input name="payment_date" type="date" value="{escape(values['payment_date'])}" required /></label>
          <label>Vendor<input name="vendor_name" value="{escape(values['vendor_name'])}" readonly /></label>
          <label>Amount<input name="amount" type="number" min="0.01" step="0.01" value="{escape(values['amount'])}" required /></label>
          <label>Method<select name="method">{payment_method_options(values['method'])}</select></label>
          <label>Payment reference<input name="reference" value="{escape(values['reference'])}" placeholder="Bank ref, check no or transaction ID" /></label>
          <label class="full-row">Notes<textarea name="notes" rows="3" placeholder="Optional payment note">{escape(values['notes'])}</textarea></label>
          <div class="dashboard-actions">
            <button class="button primary" type="submit">Post vendor payment</button>
            <a class="button secondary" href="/dashboard/#payables">Back to dashboard</a>
          </div>
        </form>
        <script>
          (() => {{
            const bill = document.getElementById('vendor-bill-select');
            const vendor = document.querySelector('[name="vendor_name"]');
            const amount = document.querySelector('[name="amount"]');
            if (!bill || !vendor || !amount) return;
            const syncFromBill = () => {{
              const selected = bill.options[bill.selectedIndex];
              if (!selected || !selected.value) return;
              vendor.value = selected.dataset.vendor || '';
              amount.value = selected.dataset.amount || '';
            }};
            bill.addEventListener('change', syncFromBill);
            syncFromBill();
          }})();
        </script>
      </section>
    </main>
    """
    return page_shell("Pay vendor bill", body, request)


@login_required
@require_http_methods(["GET", "POST"])
def expense_upload(request: HttpRequest, token: str | None = None) -> HttpResponse:
    ensure_default_chart(request)
    expense_accounts = list(Account.objects.filter(account_q(request), is_active=True, account_type="expense"))
    default_account = next((account for account in expense_accounts if account.code == "5100"), expense_accounts[0] if expense_accounts else None)
    draft = None
    error = ""
    message = ""
    if token:
        draft = ExpenseUploadDraft.objects.filter(public_token=token, owner_email__iexact=current_account_email(request), market=current_market(request)).first()
        if draft is None:
            raise Http404("Expense upload draft not found")

    if request.method == "POST" and request.POST.get("action") == "upload":
        upload = request.FILES.get("document")
        upload_error = valid_expense_document_upload(upload)
        if upload_error:
            error = upload_error
        else:
            extracted_text = extract_expense_text_from_upload(upload)
            parsed = expense_draft_from_text(request, extracted_text)
            draft = ExpenseUploadDraft.objects.create(
                market=current_market(request),
                owner=request.user,
                owner_email=current_account_email(request),
                document=upload,
                original_filename=clean_text(getattr(upload, "name", ""), max_length=240),
                extracted_text=extracted_text,
                vendor_name=parsed["vendor_name"],
                category=parsed["category"],
                amount=parsed["amount"],
                bill_date=timezone.localdate(),
                bill_status=parsed["bill_status"],
                payment_method=parsed["payment_method"],
                reference=parsed["reference"],
                notes=parsed["notes"],
            )
            audit_log(request, "expense_upload.created", "ExpenseUploadDraft", draft.id, f"Uploaded bill draft {draft.original_filename}")
            return redirect("expense_upload_review", token=draft.public_token)

    if request.method == "POST" and request.POST.get("action") == "post" and draft:
        values = {
            "vendor_name": clean_text(request.POST.get("vendor_name"), max_length=180),
            "category": clean_text(request.POST.get("category"), max_length=180),
            "amount": decimal_value(request.POST.get("amount")),
            "bill_date": clean_text(request.POST.get("bill_date"), max_length=20),
            "due_date": clean_text(request.POST.get("due_date"), max_length=20),
            "bill_status": clean_text(request.POST.get("bill_status"), "paid", 20),
            "payment_method": clean_text(request.POST.get("payment_method"), "bank", 20),
            "expense_account": clean_text(request.POST.get("expense_account"), max_length=20),
            "reference": clean_text(request.POST.get("reference"), max_length=120),
            "notes": clean_text(request.POST.get("notes")),
            "confirm": clean_text(request.POST.get("confirm"), max_length=20),
        }
        expense_account = Account.objects.filter(account_q(request), id=values["expense_account"], account_type="expense").first() or default_account
        try:
            bill_date = datetime.strptime(values["bill_date"], "%Y-%m-%d").date() if values["bill_date"] else timezone.localdate()
        except ValueError:
            bill_date = timezone.localdate()
        try:
            due_date = datetime.strptime(values["due_date"], "%Y-%m-%d").date() if values["due_date"] else None
        except ValueError:
            due_date = None
        if values["confirm"].lower() != "yes":
            error = "Type YES in the confirmation box before posting this uploaded bill."
        elif not values["vendor_name"]:
            error = "Verify the vendor name before posting."
        elif values["amount"] <= 0:
            error = "Verify the bill amount before posting."
        elif expense_account is None:
            error = "Choose an expense account."
        else:
            bill = post_expense_bill(
                request,
                bill_date=bill_date,
                due_date=due_date,
                vendor_name=values["vendor_name"],
                category=values["category"] or expense_account.name,
                amount=values["amount"],
                status=values["bill_status"],
                payment_method=values["payment_method"],
                expense_account=expense_account,
                reference=values["reference"] or f"Upload {draft.id}",
                notes=values["notes"],
                source_prefix="upload",
            )
            draft.vendor_name = values["vendor_name"]
            draft.category = values["category"] or expense_account.name
            draft.amount = values["amount"]
            draft.bill_date = bill_date
            draft.due_date = due_date
            draft.bill_status = values["bill_status"] if values["bill_status"] in {"paid", "unpaid"} else "paid"
            draft.payment_method = values["payment_method"]
            draft.reference = values["reference"]
            draft.notes = values["notes"]
            draft.status = "posted"
            draft.vendor_bill = bill
            draft.save()
            audit_log(request, "expense_upload.posted", "VendorBill", bill.id, f"Posted uploaded bill for {bill.vendor_name}")
            return redirect("/dashboard/#payables")

    if draft:
        account_id = str(default_account.id if default_account else "")
        error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
        body = f"""
        <main class="account-shell wide-form">
          <section class="account-card finance-form-card">
            <p class="eyebrow">Bill upload verification</p>
            <h1>Verify before posting</h1>
            <p class="account-copy">RozLedger created a draft from the uploaded file. Please verify every field, type YES, then post it. Nothing is posted until you confirm.</p>
            {error_html}
            <div class="selected-invoice-panel">
              <span>Uploaded document</span>
              <strong>{escape(draft.original_filename or 'Bill upload')}</strong>
              <p>Extracted text: {escape(draft.extracted_text or 'No OCR text available yet. Please complete the fields manually.')}</p>
            </div>
            <form method="post" class="account-form invoice-server-form">
              {csrf_input(request)}
              <input type="hidden" name="action" value="post" />
              <label>Vendor name<input name="vendor_name" value="{escape(draft.vendor_name)}" required /></label>
              <label>Category<input name="category" value="{escape(draft.category or 'Office expenses')}" /></label>
              <label>Expense account<select name="expense_account">{account_options(expense_accounts, account_id)}</select></label>
              <label>Amount<input name="amount" type="number" min="0.01" step="0.01" value="{escape(str(draft.amount or ''))}" required /></label>
              <label>Bill date<input name="bill_date" type="date" value="{draft.bill_date:%Y-%m-%d}" /></label>
              <label>Due date<input name="due_date" type="date" value="{draft.due_date.strftime('%Y-%m-%d') if draft.due_date else ''}" /></label>
              <label>Status<select name="bill_status">{''.join(f'<option value="{value}" {"selected" if draft.bill_status == value else ""}>{label}</option>' for value, label in [("paid", "Paid now"), ("unpaid", "Unpaid vendor bill")])}</select></label>
              <label>Payment method<select name="payment_method">{payment_method_options(draft.payment_method)}</select></label>
              <label>Reference<input name="reference" value="{escape(draft.reference)}" placeholder="Bill number or payment reference" /></label>
              <label class="full-row">Notes<textarea name="notes" rows="3">{escape(draft.notes)}</textarea></label>
              <label class="full-row">Verification question: did you check vendor, amount, date and account?<input name="confirm" placeholder="Type YES to post" required /></label>
              <div class="dashboard-actions">
                <button class="button primary" type="submit">Confirm and post expense</button>
                <a class="button secondary" href="/dashboard/expenses/upload/">Upload another bill</a>
                <a class="button ghost" href="/dashboard/#payables">Cancel</a>
              </div>
            </form>
          </section>
        </main>
        """
        return page_shell("Verify bill upload", body, request)

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card finance-form-card">
        <p class="eyebrow">Mobile bill upload</p>
        <h1>Upload bill photo</h1>
        <p class="account-copy">Take a photo from your phone or upload a PDF. RozLedger will create a draft and ask you to verify before posting the expense or vendor bill.</p>
        {error_html}
        <form method="post" class="account-form" enctype="multipart/form-data">
          {csrf_input(request)}
          <input type="hidden" name="action" value="upload" />
          <label>Bill photo or PDF<input name="document" type="file" accept="image/*,application/pdf" capture="environment" required /></label>
          <button class="button primary" type="submit">Upload and create draft</button>
        </form>
      </section>
    </main>
    """
    return page_shell("Upload bill photo", body, request)


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
        "document_type": "tax_invoice",
        "template": profile.template if profile else "classic",
        "accent_color": profile.accent_color if profile else "#126b4f",
        "business_name": profile.business_name if profile else request.user.first_name or "Your business",
        "business_phone": profile.business_phone if profile else "",
        "business_address": profile.business_address if profile else "",
        "client_name": "",
        "client_phone": "",
        "client_address": "",
        "client_gstin": "",
        "place_of_supply": "",
        "supply_type": "intra",
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
        supply_type = values["supply_type"] if values["supply_type"] in {"intra", "inter"} else "intra"
        reverse_charge = request.POST.get("reverse_charge") == "on"
        document_type = values["document_type"] if values["document_type"] in {"tax_invoice", "proforma", "quotation"} else "tax_invoice"
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
                    document_type=document_type,
                    template=values["template"],
                    accent_color=values["accent_color"],
                    business_name=values["business_name"],
                    business_phone=values["business_phone"],
                    business_address=values["business_address"],
                    client_name=values["client_name"],
                    client_phone=values["client_phone"],
                    client_address=values["client_address"],
                    client_gstin=values["client_gstin"].upper(),
                    seller_gstin=(profile.gstin if profile else ""),
                    place_of_supply=values["place_of_supply"],
                    supply_type=supply_type,
                    reverse_charge=reverse_charge,
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
                post_invoice_sales_voucher(request, invoice)
                save_business_profile_from_invoice(invoice, request.user)
                save_client_from_invoice(invoice, request.user)
                audit_log(request, "invoice.created", "Invoice", invoice.id, f"Created invoice for {invoice.client_name} amount {invoice.total_text}")
                return redirect(f"/dashboard/?invoice=created#invoices")

    error_html = f'<p class="form-error">{escape(error)}</p>' if error else ""
    preview_logo = ""
    if profile and profile.business_logo:
        preview_logo = '<img class="invoice-preview-logo" data-preview-logo src="/dashboard/business-profile/logo/" alt="Business logo preview" />'
    supply_options = "".join(
        f'<option value="{value}" {"selected" if values["supply_type"] == value else ""}>{escape(label)}</option>'
        for value, label in [("intra", "Intra-state (CGST + SGST)"), ("inter", "Inter-state (IGST)")]
    )
    reverse_charge_checked = "checked" if request.POST.get("reverse_charge") == "on" else ""
    gst_supply_fields = "" if us_market else f"""
            <label>Place of supply<input name="place_of_supply" value="{escape(values['place_of_supply'])}" placeholder="State of supply, e.g. Kerala" /></label>
            <label>Supply type<select name="supply_type">{supply_options}</select></label>
            <label class="checkbox-row"><input name="reverse_charge" type="checkbox" {reverse_charge_checked} /> Reverse charge applies</label>"""
    document_type_options = "".join(
        f'<option value="{value}" {"selected" if values["document_type"] == value else ""}>{escape(label)}</option>'
        for value, label in [("tax_invoice", "Tax invoice"), ("proforma", "Proforma invoice"), ("quotation", "Quotation")]
    )
    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card invoice-builder-card">
        <div>
          <p class="eyebrow">New document</p>
          <h1>Create invoice, proforma or quotation</h1>
          <p class="account-copy">Pick the document type and a professional template. Quotations and proforma invoices do not post to your books; convert them to a tax invoice when the sale is confirmed.</p>
          {error_html}
        </div>
        <div class="invoice-builder-layout">
          <form method="post" action="/dashboard/invoices/new/" class="account-form invoice-server-form invoice-builder-form" enctype="multipart/form-data">
            {csrf_input(request)}
            <label>Document type<select name="document_type">{document_type_options}</select></label>
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
            {gst_supply_fields}
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


def owned_receipt(request: HttpRequest, receipt_id: int) -> PaymentReceipt:
    try:
        return PaymentReceipt.objects.select_related("invoice", "voucher", "journal_entry").get(Q(id=receipt_id) & account_q(request))
    except PaymentReceipt.DoesNotExist as exc:
        raise Http404("Receipt not found") from exc


def owned_credit_note(request: HttpRequest, credit_note_id: int) -> CustomerCreditNote:
    try:
        return CustomerCreditNote.objects.select_related("invoice", "voucher", "journal_entry").get(Q(id=credit_note_id) & account_q(request))
    except CustomerCreditNote.DoesNotExist as exc:
        raise Http404("Credit note not found") from exc


def owned_debit_note(request: HttpRequest, debit_note_id: int) -> VendorDebitNote:
    try:
        return VendorDebitNote.objects.select_related("bill", "voucher", "journal_entry").get(Q(id=debit_note_id) & account_q(request))
    except VendorDebitNote.DoesNotExist as exc:
        raise Http404("Debit note not found") from exc


def owned_reversal(request: HttpRequest, reversal_id: int) -> PaymentReversal:
    try:
        return PaymentReversal.objects.select_related("customer_receipt", "vendor_payment__bill", "voucher", "journal_entry").get(Q(id=reversal_id) & account_q(request))
    except PaymentReversal.DoesNotExist as exc:
        raise Http404("Payment reversal not found") from exc


def owned_vendor_payment(request: HttpRequest, payment_id: int) -> VendorBillPayment:
    try:
        return VendorBillPayment.objects.select_related("bill", "voucher").get(Q(id=payment_id) & account_q(request))
    except VendorBillPayment.DoesNotExist as exc:
        raise Http404("Vendor payment not found") from exc


def owned_vendor_bill(request: HttpRequest, bill_id: int) -> VendorBill:
    try:
        return VendorBill.objects.prefetch_related("payments__voucher", "upload_drafts").select_related("journal_entry", "voucher", "payment_voucher").get(Q(id=bill_id) & account_q(request))
    except VendorBill.DoesNotExist as exc:
        raise Http404("Vendor bill not found") from exc


def owned_voucher(request: HttpRequest, voucher_id: int) -> Voucher:
    try:
        return (
            Voucher.objects.select_related("journal_entry")
            .prefetch_related("ledger_lines__account", "inventory_lines__item", "inventory_lines__godown")
            .get(Q(id=voucher_id) & account_q(request))
        )
    except Voucher.DoesNotExist as exc:
        raise Http404("Voucher not found") from exc


def owned_journal_entry(request: HttpRequest, entry_id: int) -> JournalEntry:
    try:
        return JournalEntry.objects.prefetch_related("lines__account").get(Q(id=entry_id) & account_q(request))
    except JournalEntry.DoesNotExist as exc:
        raise Http404("Journal entry not found") from exc


@login_required
@require_http_methods(["GET", "POST"])
def invoice_edit(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    old_amounts = invoice_accounting_amounts(invoice)
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
        new_tax = (amount_before_gst * gst_rate / Decimal("100") if include_gst else Decimal("0")).quantize(Decimal("0.01"))
        new_amounts = {"subtotal": amount_before_gst, "tax": new_tax, "total": (amount_before_gst + new_tax).quantize(Decimal("0.01"))}
        financial_amount_changed = any(old_amounts[key] != new_amounts[key] for key in old_amounts)
        logo_upload = request.FILES.get("business_logo")
        logo_error = valid_logo_upload(logo_upload)
        if logo_error:
            return page_shell("Edit invoice", f'<main class="account-shell"><section class="account-card"><p class="form-error">{escape(logo_error)}</p><a class="button secondary" href="/dashboard/invoices/{invoice.id}/edit/">Back to edit invoice</a></section></main>', request)
        if financial_amount_changed and invoice.payments.exists():
            return page_shell(
                "Edit invoice",
                f'<main class="account-shell"><section class="account-card"><p class="form-error">This invoice already has receipt postings. Create a correction invoice or contact support before changing amount or tax.</p><a class="button secondary" href="/dashboard/invoices/{invoice.id}/edit/">Back to edit invoice</a></section></main>',
                request,
            )
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
            invoice.place_of_supply = clean_text(request.POST.get("place_of_supply"), max_length=80)
            supply_type_value = clean_text(request.POST.get("supply_type"), max_length=10)
            invoice.supply_type = supply_type_value if supply_type_value in {"intra", "inter"} else invoice.supply_type
            invoice.reverse_charge = request.POST.get("reverse_charge") == "on"
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
            if financial_amount_changed or not invoice.sales_voucher_id:
                post_invoice_sales_voucher(request, invoice, replace=bool(invoice.sales_voucher_id))
            save_business_profile_from_invoice(invoice, request.user)
            save_client_from_invoice(invoice, request.user)
            audit_log(request, "invoice.updated", "Invoice", invoice.id, f"Updated invoice for {invoice.client_name}")
            return redirect("/dashboard/")

    status_options = "".join(
        f'<option value="{value}" {"selected" if invoice.status == value else ""}>{label}</option>'
        for value, label in Invoice.STATUS_CHOICES
    )
    edit_item_rows = [{"description": item.description, "quantity": item.quantity, "rate": item.rate} for item in invoice_items(invoice)]
    delete_control = (
        f"""
        <div class="selected-invoice-panel">
          <span>Correction safety</span>
          <strong>Posted invoices cannot be deleted</strong>
          <p>This invoice has accounting postings or receipts. Keep the audit trail and use a correcting invoice or receipt adjustment workflow.</p>
          <a class="button secondary" href="/dashboard/invoices/{invoice.id}/accounting/">Open accounting trace</a>
        </div>
        """
        if invoice.sales_voucher_id or invoice.payments.exists() or invoice.credit_notes.exists()
        else f"""
        <form method="post" action="/dashboard/invoices/{invoice.id}/delete/" class="danger-form">
          {csrf_input(request)}
          <button class="button ghost" type="submit">Delete invoice</button>
        </form>
        """
    )
    edit_supply_options = "".join(
        f'<option value="{value}" {"selected" if invoice.supply_type == value else ""}>{escape(label)}</option>'
        for value, label in [("intra", "Intra-state (CGST + SGST)"), ("inter", "Inter-state (IGST)")]
    )
    edit_gst_supply_fields = "" if us_invoice else f"""
          <label>Place of supply<input name="place_of_supply" value="{escape(invoice.place_of_supply)}" placeholder="State of supply, e.g. Kerala" /></label>
          <label>Supply type<select name="supply_type">{edit_supply_options}</select></label>
          <label class="checkbox-row"><input name="reverse_charge" type="checkbox" {'checked' if invoice.reverse_charge else ''} /> Reverse charge applies</label>"""
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
          {edit_gst_supply_fields}
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
        {delete_control}
      </section>
    </main>
    """
    return page_shell("Edit invoice", body, request)


@login_required
@require_POST
def invoice_convert(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    target = clean_text(request.POST.get("target"), max_length=20)
    allowed = {
        "quotation": {"proforma", "tax_invoice"},
        "proforma": {"tax_invoice"},
    }
    if target not in allowed.get(invoice.document_type, set()):
        return redirect("/dashboard/#invoices")
    previous = invoice.document_type
    invoice.document_type = target
    if target == "tax_invoice":
        invoice.status = "sent"
    invoice.save(update_fields=["document_type", "status", "updated_at"])
    if target == "tax_invoice":
        post_invoice_sales_voucher(request, invoice)
    audit_log(request, "invoice.converted", "Invoice", invoice.id, f"Converted {previous} to {target} ({invoice_number(invoice)})")
    return redirect("/dashboard/?invoice=converted#invoices")


@login_required
@require_POST
def invoice_status(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    status = clean_text(request.POST.get("status"), max_length=20)
    if status == "paid" and invoice_outstanding_amount(invoice) > 0:
        return redirect(f"/dashboard/payments/new/?invoice={invoice.id}")
    if status in dict(Invoice.STATUS_CHOICES) and status not in {"paid", "partially_paid"}:
        invoice.status = status
        invoice.save(update_fields=["status", "updated_at"])
        audit_log(request, "invoice.status_changed", "Invoice", invoice.id, f"Changed invoice status to {status}")
    return redirect("/dashboard/")


@login_required
@require_POST
def invoice_delete(request: HttpRequest, invoice_id: int) -> HttpResponse:
    invoice = owned_invoice(request, invoice_id)
    if invoice.sales_voucher_id or invoice.payments.exists() or invoice.credit_notes.exists():
        audit_log(request, "invoice.delete_blocked", "Invoice", invoice.id, f"Blocked delete for posted invoice {invoice_number(invoice)}")
        return page_shell(
            "Invoice correction required",
            f"""
            <main class="account-shell">
              <section class="account-card">
                <p class="eyebrow">Correction safety</p>
                <h1>Invoice cannot be deleted</h1>
                <p class="account-copy">This invoice has posted accounting entries or receipts. RozLedger keeps the audit trail intact. Use a correcting invoice or contact support before changing posted financial history.</p>
                <div class="dashboard-actions">
                  <a class="button primary" href="/dashboard/invoices/{invoice.id}/accounting/">Open accounting trace</a>
                  <a class="button secondary" href="/dashboard/">Back to dashboard</a>
                </div>
              </section>
            </main>
            """,
            request,
        )
    audit_log(request, "invoice.deleted", "Invoice", invoice.id, f"Deleted invoice for {invoice.client_name}")
    invoice.delete()
    return redirect("/dashboard/")


@login_required
def razorpay_webhook_status_panel(request: HttpRequest) -> str:
    """Staff-only panel on the billing page: shows the webhook URL, gateway readiness and recent events."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return ""

    webhook_url = absolute_url(request, "/webhooks/razorpay/")
    config = PaymentGatewayConfig.razorpay()
    if config:
        gateway_state = "enabled" if config.enabled else "disabled"
        configured = "yes" if config.is_configured else "no"
        secret_state = "set" if config.webhook_secret else "MISSING"
        mode = config.get_mode_display()
        amount = f"{config.subscription_amount} {config.subscription_currency}"
        plan_id = config.razorpay_plan_id or "auto-creates on first subscribe"
    else:
        gateway_state = configured = secret_state = "no config row"
        mode = amount = plan_id = "—"

    recent = list(PaymentEvent.objects.all()[:8])
    if recent:
        rows = "".join(
            f"<tr><td>{escape(event.event_type)}</td><td>{escape(event.reference_id or '—')}</td>"
            f"<td>{event.created_at:%d %b %Y %H:%M}</td></tr>"
            for event in recent
        )
        events_block = (
            f"<table class=\"webhook-events\"><thead><tr><th>Event</th><th>Subscription</th>"
            f"<th>Received (server time)</th></tr></thead><tbody>{rows}</tbody></table>"
            f"<p class=\"webhook-caption\">Latest {len(recent)} of {PaymentEvent.objects.count()} received webhook event(s).</p>"
        )
    else:
        events_block = (
            "<p class=\"webhook-caption\">No webhook events received yet. After you create the Razorpay "
            "webhook and a subscription charges, deliveries appear here.</p>"
        )

    return f"""
        <div class="pro-status-panel webhook-status-panel">
          <h2>Razorpay webhook status <span class="staff-tag">staff only</span></h2>
          <p class="billing-meta">
            Webhook URL (set this in Razorpay): <code>{escape(webhook_url)}</code><br />
            Gateway: <strong>{escape(gateway_state)}</strong> &middot; Keys configured: <strong>{escape(configured)}</strong> &middot; Webhook secret: <strong>{escape(secret_state)}</strong><br />
            Mode: <strong>{escape(str(mode))}</strong> &middot; Amount: <strong>{escape(str(amount))}</strong> &middot; Plan id: <code>{escape(str(plan_id))}</code>
          </p>
          {events_block}
        </div>
    """


@login_required
@require_http_methods(["GET", "POST"])
def gstn_settings(request: HttpRequest) -> HttpResponse:
    if not request.user.is_staff:
        raise Http404("Not found")
    config = GstnApiConfig.for_market("IN")
    result_html = ""
    if request.method == "POST":
        action = clean_text(request.POST.get("action"), max_length=40)
        if not config or not config.is_configured:
            result_html = '<p class="form-error">Configure the GSTN API in Django admin (Gstn api configs) before testing.</p>'
        elif action == "test_auth":
            try:
                gstn_client.authenticate(config, force=True)
                result_html = '<p class="status-text">Authentication succeeded &mdash; a fresh auth token is cached.</p>'
            except gstn_client.GstnApiError as exc:
                result_html = f'<p class="form-error">{escape(str(exc))}</p>'
        elif action == "validate_gstin":
            gstin = clean_text(request.POST.get("gstin"), max_length=20).upper()
            if not gstin:
                result_html = '<p class="form-error">Enter a GSTIN to validate.</p>'
            else:
                try:
                    details = gstn_client.get_gstin_details(config, gstin)
                    legal = details.get("LegalName") or details.get("lgnm") or details.get("Lgnm") or ""
                    status = details.get("Status") or details.get("sts") or ""
                    pretty = escape(json.dumps(details, indent=2)[:2000])
                    result_html = (
                        f'<div class="pro-status-panel"><strong>GSTIN {escape(gstin)}</strong>'
                        f'<p>Legal name: {escape(str(legal)) or "&mdash;"} &middot; Status: {escape(str(status)) or "&mdash;"}</p>'
                        f'<pre class="webhook-events">{pretty}</pre></div>'
                    )
                except gstn_client.GstnApiError as exc:
                    result_html = f'<p class="form-error">{escape(str(exc))}</p>'

    if config:
        status_rows = (
            f"Provider: <strong>{escape(config.provider)}</strong> &middot; Mode: <strong>{escape(config.get_mode_display())}</strong><br />"
            f"Base URL: <code>{escape(config.base_url or 'not set')}</code><br />"
            f"GSTIN: <strong>{escape(config.gstin or '&mdash;')}</strong> &middot; Configured: <strong>{'yes' if config.is_configured else 'no'}</strong> &middot; Enabled: <strong>{'yes' if config.enabled else 'no'}</strong><br />"
            f"Auth token: <strong>{'valid' if config.token_valid else 'not cached / expired'}</strong>"
        )
    else:
        status_rows = "No GSTN API config yet. Create one in Django admin under <strong>Gstn api configs</strong>."

    body = f"""
    <main class="account-shell wide-form">
      <section class="account-card">
        <p class="eyebrow">GST e-Invoice / e-Way Bill</p>
        <h1>GSTN API settings <span class="staff-tag">staff only</span></h1>
        <p class="account-copy">Connection to the GST Suvidha Provider (WhiteBooks). Credentials are stored encrypted in Django admin; this page verifies the connection and validates a GSTIN.</p>
        <div class="pro-status-panel">
          <p class="billing-meta">{status_rows}</p>
        </div>
        {result_html}
        <div class="pro-workflow-grid">
          <article>
            <h2>Test authentication</h2>
            <form method="post" action="/dashboard/gstn/" class="account-form compact-form">
              {csrf_input(request)}
              <input type="hidden" name="action" value="test_auth" />
              <button class="button secondary" type="submit">Test authentication</button>
            </form>
          </article>
          <article>
            <h2>Validate a GSTIN</h2>
            <form method="post" action="/dashboard/gstn/" class="account-form compact-form">
              {csrf_input(request)}
              <input type="hidden" name="action" value="validate_gstin" />
              <label>GSTIN<input name="gstin" placeholder="e.g. 29AAGCB1286Q000" maxlength="20" required /></label>
              <button class="button primary" type="submit">Validate GSTIN</button>
            </form>
          </article>
        </div>
        <div class="dashboard-actions">
          <a class="button secondary" href="/admin/core/gstnapiconfig/">Edit credentials in admin</a>
          <a class="button ghost" href="/dashboard/">Back to dashboard</a>
        </div>
      </section>
    </main>
    """
    return page_shell("GSTN API settings", body, request)


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
    auto_checkout = bool(payment_gateway) and market == "IN"
    gateway_message = (
        f"{payment_gateway.get_gateway_display()} {payment_gateway.get_mode_display()} checkout is enabled. Pay securely and your Pro access activates automatically — no waiting for manual approval."
        if auto_checkout
        else f"{gateway_name} checkout is not enabled yet. This request will be reviewed and approved manually from Django admin."
    )
    request_button = ""
    if subscription.is_pro_active:
        request_button = '<a class="button primary" href="/dashboard/">Go to dashboard</a>'
    elif auto_checkout:
        request_button = f"""
          <form method="post" action="/dashboard/billing/subscribe/" class="account-form compact-form">
            {csrf_input(request)}
            <button class="button primary" type="submit">Subscribe with Razorpay &mdash; {escape(paid_price)}</button>
          </form>
        """
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
        {razorpay_webhook_status_panel(request)}
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


ACTIVATION_EVENTS = {"subscription.activated", "subscription.charged", "subscription.resumed"}
PAUSE_EVENTS = {"subscription.halted", "subscription.paused", "subscription.pending"}
CANCEL_EVENTS = {"subscription.cancelled", "subscription.completed", "subscription.expired"}


def apply_razorpay_subscription_event(subscription: PlanSubscription, event_type: str, payment_id: str = "") -> bool:
    """Update a PlanSubscription from a verified Razorpay subscription webhook. Returns True if changed."""
    now = timezone.now()
    if event_type in ACTIVATION_EVENTS:
        subscription.plan = "pro"
        subscription.status = "active"
        subscription.provider = "razorpay"
        if subscription.activated_at is None:
            subscription.activated_at = now
        # Each successful charge re-extends access ~one cycle (plus a grace buffer).
        subscription.expires_at = now + timedelta(days=32)
        subscription.paused_at = None
        subscription.cancelled_at = None
        if payment_id:
            subscription.last_payment_id = payment_id
        subscription.save(update_fields=[
            "plan", "status", "provider", "activated_at", "expires_at",
            "paused_at", "cancelled_at", "last_payment_id", "updated_at",
        ])
        return True
    if event_type in PAUSE_EVENTS:
        subscription.status = "paused"
        subscription.paused_at = now
        subscription.save(update_fields=["status", "paused_at", "updated_at"])
        return True
    if event_type in CANCEL_EVENTS:
        subscription.status = "cancelled"
        subscription.cancelled_at = now
        subscription.save(update_fields=["status", "cancelled_at", "updated_at"])
        return True
    return False


@login_required
@require_POST
def pro_subscribe(request: HttpRequest) -> HttpResponse:
    market = current_market(request)
    subscription = get_subscription(request)
    if subscription.is_pro_active:
        return redirect("/dashboard/billing/pro/?pro=active")

    config = PaymentGatewayConfig.active_razorpay() if market == "IN" else None
    if config is None:
        # No automated gateway for this market yet — keep the manual request path.
        return request_pro_activation(request)

    email = current_account_email(request)
    if is_rate_limited(request, "subscribe", limit=8, identity=f"user:{request.user.id}"):
        return rate_limit_response()

    try:
        plan_id = razorpay_client.ensure_monthly_plan(config)
        result = razorpay_client.create_subscription(config, plan_id, notes={"owner_email": email, "market": market})
    except razorpay_client.RazorpayError:
        subscription.plan = "pro"
        subscription.status = "requested"
        subscription.provider = "razorpay"
        subscription.requested_at = timezone.now()
        subscription.save(update_fields=["plan", "status", "provider", "requested_at", "updated_at"])
        audit_log(request, "subscription.request_failed", "PlanSubscription", subscription.id, "Razorpay subscription create failed; manual request recorded")
        return redirect("/dashboard/billing/pro/?pro=error")

    subscription.plan = "pro"
    subscription.status = "requested"
    subscription.provider = "razorpay"
    subscription.requested_at = timezone.now()
    subscription.razorpay_subscription_id = clean_text(result.get("id"), max_length=64)
    subscription.razorpay_customer_id = clean_text(result.get("customer_id"), max_length=64)
    subscription.save(update_fields=[
        "plan", "status", "provider", "requested_at",
        "razorpay_subscription_id", "razorpay_customer_id", "updated_at",
    ])
    audit_log(request, "subscription.checkout_started", "PlanSubscription", subscription.id, f"Razorpay subscription {subscription.razorpay_subscription_id} created")

    short_url = result.get("short_url")
    if short_url and isinstance(short_url, str) and short_url.startswith("https://"):
        return redirect(short_url)
    return redirect("/dashboard/billing/pro/?pro=error")


@csrf_exempt
@require_POST
def razorpay_webhook(request: HttpRequest) -> HttpResponse:
    config = PaymentGatewayConfig.razorpay()
    secret = config.webhook_secret if config else ""
    signature = request.headers.get("X-Razorpay-Signature", "")
    body = request.body

    if not razorpay_client.verify_webhook_signature(body, signature, secret):
        return HttpResponse("invalid signature", status=400)

    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse("invalid payload", status=400)

    event_type = clean_text(payload.get("event"), max_length=80)
    entities = payload.get("payload") or {}
    sub_entity = ((entities.get("subscription") or {}).get("entity")) or {}
    payment_entity = ((entities.get("payment") or {}).get("entity")) or {}
    subscription_id = clean_text(sub_entity.get("id"), max_length=64)
    payment_id = clean_text(payment_entity.get("id"), max_length=64)

    event_id = clean_text(request.headers.get("X-Razorpay-Event-Id"), max_length=120)
    if not event_id:
        event_id = clean_text(f"{event_type}:{subscription_id}:{payment_id}", max_length=120)

    with transaction.atomic():
        event, created = PaymentEvent.objects.get_or_create(
            event_id=event_id,
            defaults={
                "provider": "razorpay",
                "event_type": event_type,
                "reference_id": subscription_id,
                "summary": clean_text(f"{event_type} sub={subscription_id} pay={payment_id}", max_length=240),
            },
        )
        if not created:
            return JsonResponse({"status": "duplicate"}, status=200)

        if subscription_id:
            subscription = (
                PlanSubscription.objects.select_for_update()
                .filter(razorpay_subscription_id=subscription_id)
                .first()
            )
            if subscription:
                apply_razorpay_subscription_event(subscription, event_type, payment_id)

    return JsonResponse({"status": "ok"}, status=200)


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
    invoice_title = document_type_label(invoice)
    doc_note = ""
    if invoice.document_type == "proforma":
        doc_note = '<p style="margin:8px 0 0;color:#9f2f22;font-weight:700;font-size:13px;letter-spacing:.02em;">This is a proforma invoice and not a valid tax invoice.</p>'
    elif invoice.document_type == "quotation":
        doc_note = '<p style="margin:8px 0 0;color:#9f2f22;font-weight:700;font-size:13px;letter-spacing:.02em;">This is a quotation. Prices are an estimate and not a tax invoice.</p>'
    doc_word = {"quotation": "Quotation", "proforma": "Proforma"}.get(invoice.document_type, "Invoice")
    due_label = "Valid until" if invoice.document_type == "quotation" else "Due date"
    payment_link_label = "Payment link" if us_invoice else "UPI / payment link"
    subtotal = invoice_subtotal(invoice)
    if invoice.include_gst:
        tax_breakup_rows = "".join(
            f'<div><span>{escape(row_label)}</span><strong>{escape(invoice_money(invoice, amount))}</strong></div>'
            for row_label, amount in invoice_tax_breakup(invoice)
        )
    else:
        tax_breakup_rows = f'<div><span>{escape(tax_label)}</span><strong>Not charged</strong></div>'
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
            {doc_note}
          </div>
        </div>
        <aside class="invoice-number-card">
          <strong>{escape(invoice_number(invoice))}</strong>
          <span>{doc_word} date</span>
          <p>{invoice.created_at:%d %b %Y}</p>
          <span>{due_label}</span>
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
        {tax_breakup_rows}
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
    header_items = [Paragraph(document_type_label(invoice).upper(), title_style), Paragraph(escape(invoice.business_name), heading_style)]
    if invoice.business_phone:
        header_items.append(para(f"Phone: {invoice.business_phone}", small_style))
    if invoice.business_address:
        header_items.append(para(invoice.business_address, small_style))
    if invoice.document_type == "proforma":
        header_items.append(para("This is a proforma invoice and not a valid tax invoice.", small_style))
    elif invoice.document_type == "quotation":
        header_items.append(para("This is a quotation. Prices are an estimate and not a tax invoice.", small_style))
    doc_word = {"quotation": "Quotation", "proforma": "Proforma"}.get(invoice.document_type, "Invoice")
    due_label = "Valid until" if invoice.document_type == "quotation" else "Due date"
    meta_table = Table(
        [
            [para(f"{doc_word} no.", small_style), para(invoice_number(invoice), meta_value_style)],
            [para(f"{doc_word} date", small_style), para(f"{invoice.created_at:%d %b %Y}", meta_value_style)],
            [para(due_label, small_style), para(f"{due_date:%d %b %Y}", meta_value_style)],
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
    pdf_tax_rows = (
        [[para(row_label, right_style), para(invoice_money(invoice, amount), amount_bold_style)] for row_label, amount in invoice_tax_breakup(invoice)]
        if invoice.include_gst
        else [[para(invoice_tax_label(invoice), right_style), para("Not charged", amount_bold_style)]]
    )
    story.append(
        Table(
            [
                [para("Subtotal", right_style), para(invoice_money(invoice, subtotal), amount_bold_style)],
                *pdf_tax_rows,
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
        place_of_supply=clean_text(payload.get("place_of_supply"), max_length=80),
        supply_type=clean_supply_type(payload.get("supply_type")),
        document_type=clean_document_type(payload.get("document_type")),
        reverse_charge=bool(payload.get("reverse_charge")),
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
    if request.user.is_authenticated:
        post_invoice_sales_voucher(request, invoice)
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
