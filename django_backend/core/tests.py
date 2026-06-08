import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import PlanSubscriptionAdmin
from .models import Account, AuditLog, BusinessProfile, Client as SavedClient, CustomerCreditNote
from .models import ExpenseUploadDraft, InventoryItem, Invoice, InvoiceLineItem, JournalEntry, JournalLine, Lead, PaymentReceipt, PaymentReversal, PlanSubscription, ReconciliationSession, StockCostLayer, StockLayerConsumption, StockMovement, VendorBill, VendorBillPayment, VendorDebitNote, Voucher
from .views import invoice_outstanding_amount, vendor_bill_outstanding_amount, invoice_number, document_type_label


TEST_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "SECURE_SSL_REDIRECT": False,
    "DEFAULT_FROM_EMAIL": "RozLedger <cs@rozledger.in>",
    "ROZLEDGER_NOTIFY_EMAIL": "cs@rozledger.in",
    "ALLOWED_HOSTS": ["testserver", "rozledger.in", "rozledger.com", "www.rozledger.com"],
    "CACHES": {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "rozledger-tests",
        }
    },
}


@override_settings(**TEST_SETTINGS)
class PublicPagesTests(TestCase):
    def test_core_public_pages_load(self):
        for path in ["/", "/pricing/", "/contact/", "/blog/", "/api/health"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_dot_com_homepage_uses_us_positioning(self):
        response = self.client.get("/", HTTP_HOST="rozledger.com")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("US service businesses", content)
        self.assertIn("Sales tax", content)
        self.assertIn("chart of accounts", content)
        self.assertIn("balanced vouchers", content)
        self.assertNotIn("Built for India", content)

    def test_dot_in_homepage_keeps_india_positioning(self):
        response = self.client.get("/", HTTP_HOST="rozledger.in")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Made in India", content)
        self.assertIn("chart of accounts", content)
        self.assertIn("GST", content)

    def test_dot_com_content_uses_us_invoice_templates(self):
        response = self.client.get("/content/", HTTP_HOST="rozledger.com")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("US invoice and accounting templates", content)
        self.assertIn("Chart of accounts starter", content)
        self.assertIn("Handyman repair invoice", content)
        self.assertIn("Equipment rental invoice", content)
        self.assertNotIn("GST Invoice Format", content)

    def test_dot_in_content_keeps_india_template_library(self):
        response = self.client.get("/content/", HTTP_HOST="rozledger.in")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Invoice, GST", content)
        self.assertIn("Accounting Templates", content)
        self.assertIn("Chart of accounts starter", content)
        self.assertIn("GST Invoice Format", content)

    def test_dot_com_contact_uses_us_contact_details(self):
        response = self.client.get("/contact/", HTTP_HOST="rozledger.com")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("114 Crockett Rd", content)
        self.assertIn("King Of Prussia, PA 19406", content)
        self.assertIn("(215) 774-1500", content)
        self.assertNotIn("Palarivattom", content)

    def test_dot_in_contact_keeps_india_contact_details(self):
        response = self.client.get("/contact/", HTTP_HOST="rozledger.in")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Palarivattom", content)
        self.assertIn("+91 95160 22222", content)
        self.assertNotIn("114 Crockett Rd", content)

    def test_pricing_pages_are_market_specific(self):
        us_response = self.client.get("/pricing/", HTTP_HOST="rozledger.com")
        us_content = b"".join(us_response.streaming_content).decode("utf-8")
        india_response = self.client.get("/pricing/", HTTP_HOST="rozledger.in")
        india_content = b"".join(india_response.streaming_content).decode("utf-8")

        self.assertEqual(us_response.status_code, 200)
        self.assertIn("$3.99/month", us_content)
        self.assertIn("King Of Prussia, PA 19406", us_content)
        self.assertNotIn("Rs 299/month", us_content)
        self.assertEqual(india_response.status_code, 200)
        self.assertIn("Rs 299/month", india_content)
        self.assertIn("Palarivattom", india_content)
        self.assertNotIn("$3.99/month", india_content)


@override_settings(**TEST_SETTINGS)
class AccountWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_register_logs_customer_into_dashboard(self):
        session = self.client.session
        session["registration_captcha_answer"] = "12"
        session.save()
        response = self.client.post(
            reverse("register"),
            {
                "name": "Test Customer",
                "email": "customer@example.com",
                "password": "strong-password-123",
                "captcha_answer": "12",
                "next": "/dashboard/",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertTrue(User.objects.filter(username="customer@example.com").exists())

    def test_register_form_has_password_toggle_and_captcha(self):
        response = self.client.get(reverse("register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-password-toggle="register-password"')
        self.assertContains(response, "Security check:")
        self.assertContains(response, 'name="captcha_answer"')

    def test_register_rejects_wrong_captcha(self):
        session = self.client.session
        session["registration_captcha_answer"] = "12"
        session.save()

        response = self.client.post(
            reverse("register"),
            {
                "name": "Bot User",
                "email": "bot@example.com",
                "password": "strong-password-123",
                "captcha_answer": "13",
                "next": "/dashboard/",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please complete the security check correctly.")
        self.assertFalse(User.objects.filter(username="bot@example.com").exists())

    def test_login_form_has_password_show_toggle(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-password-toggle="login-password"')
        self.assertContains(response, ">Show</button>")

    def test_invoice_api_sets_logged_in_owner_and_creates_pdf(self):
        user = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="strong-password-123",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("create_invoice"),
            data=json.dumps(
                {
                    "owner_email": "different@example.com",
                    "template": "executive",
                    "accent_color": "#334155",
                    "business_name": "Owner Business",
                    "business_phone": "+91 95160 22222",
                    "business_address": "Owner Street\nKochi",
                    "client_name": "Alpha Client",
                    "client_phone": "+91 90000 11111",
                    "client_address": "Client Road\nMumbai",
                    "client_gstin": "32ABCDE1234F1Z5",
                    "service_name": "Monthly service",
                    "include_gst": True,
                    "amount_before_gst": "1000",
                    "gst_rate": "18",
                    "due_days": 7,
                    "total_text": "₹ 1180",
                    "upi_link": "upi://pay?pa=test@upi",
                    "bank_details": "Bank: Test Bank\nIFSC: TEST0001",
                    "thank_you_note": "Thank you for choosing us.",
                    "invoice_text": "Invoice text",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.owner, user)
        self.assertEqual(invoice.owner_email, "owner@example.com")
        self.assertEqual(invoice.template, "executive")
        self.assertEqual(invoice.accent_color, "#334155")
        self.assertEqual(invoice.business_phone, "+91 95160 22222")
        self.assertEqual(invoice.business_address, "Owner Street\nKochi")
        self.assertEqual(invoice.client_phone, "+91 90000 11111")
        self.assertEqual(invoice.client_gstin, "32ABCDE1234F1Z5")
        self.assertEqual(invoice.bank_details, "Bank: Test Bank\nIFSC: TEST0001")
        saved_client = SavedClient.objects.get(owner=user, owner_email="owner@example.com")
        self.assertEqual(saved_client.phone, "+91 90000 11111")
        self.assertEqual(saved_client.address, "Client Road\nMumbai")
        self.assertEqual(saved_client.gstin, "32ABCDE1234F1Z5")
        business_profile = BusinessProfile.objects.get(owner=user, owner_email="owner@example.com")
        self.assertEqual(business_profile.business_name, "Owner Business")
        self.assertEqual(business_profile.business_phone, "+91 95160 22222")
        self.assertEqual(business_profile.business_address, "Owner Street\nKochi")
        self.assertEqual(business_profile.bank_details, "Bank: Test Bank\nIFSC: TEST0001")

        print_response = self.client.get(reverse("invoice_print", args=[invoice.public_token]))
        self.assertContains(print_response, "Phone: +91 95160 22222")
        self.assertContains(print_response, "Phone: +91 90000 11111")
        pdf_response = self.client.get(reverse("invoice_pdf", args=[invoice.public_token]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_us_invoice_api_saves_us_currency_and_tax_label(self):
        response = self.client.post(
            reverse("create_invoice"),
            data=json.dumps(
                {
                    "owner_email": "us-owner@example.com",
                    "business_name": "River City Handyman",
                    "client_name": "Johnson Family",
                    "service_name": "Door repair",
                    "amount_before_gst": "375",
                    "gst_rate": "7.25",
                    "include_gst": True,
                    "tax_label": "Sales tax",
                    "currency_symbol": "$",
                    "total_text": "$ 402.19",
                    "invoice_text": "",
                }
            ),
            content_type="application/json",
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 201)
        invoice = Invoice.objects.get(owner_email="us-owner@example.com")
        self.assertEqual(invoice.currency_symbol, "$")
        self.assertEqual(invoice.tax_label, "Sales tax")
        self.assertIn("Sales tax", invoice.invoice_text)
        self.assertIn("$ 375.00", invoice.invoice_text)
        print_response = self.client.get(reverse("invoice_print", args=[invoice.public_token]))
        self.assertContains(print_response, "Sales tax")
        self.assertContains(print_response, "$ 402.19")
        self.assertContains(print_response, "www.rozledger.com")

    def test_free_plan_blocks_after_five_invoices_per_month(self):
        email = "free-limit@example.com"
        for index in range(5):
            Invoice.objects.create(
                market="US",
                owner_email=email,
                business_name="Free Business",
                client_name=f"Client {index}",
                service_name="Service",
                amount_before_gst="100.00",
                gst_rate="0.00",
                include_gst=False,
                total_text="$ 100.00",
                upi_link="",
                invoice_text="",
                currency_symbol="$",
                tax_label="Sales tax",
            )

        response = self.client.post(
            reverse("create_invoice"),
            data=json.dumps(
                {
                    "owner_email": email,
                    "business_name": "Free Business",
                    "client_name": "Blocked Client",
                    "service_name": "Service",
                    "amount_before_gst": "100",
                    "gst_rate": "0",
                    "include_gst": False,
                    "currency_symbol": "$",
                    "tax_label": "Sales tax",
                    "total_text": "$ 100.00",
                }
            ),
            content_type="application/json",
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Free plan allows 5 invoices per month", response.json()["error"])

    def test_us_quota_does_not_count_india_invoices_for_same_email(self):
        email = "dual-market@example.com"
        for index in range(5):
            Invoice.objects.create(
                market="IN",
                owner_email=email,
                business_name="India Business",
                client_name=f"India Client {index}",
                service_name="Service",
                amount_before_gst="100.00",
                gst_rate="0.00",
                include_gst=False,
                total_text="₹ 100.00",
                upi_link="",
                invoice_text="",
                currency_symbol="₹",
                tax_label="GST",
            )

        response = self.client.post(
            reverse("create_invoice"),
            data=json.dumps(
                {
                    "owner_email": email,
                    "business_name": "US Business",
                    "client_name": "US Client",
                    "service_name": "Service",
                    "amount_before_gst": "100",
                    "gst_rate": "0",
                    "include_gst": False,
                    "currency_symbol": "$",
                    "tax_label": "Sales tax",
                    "total_text": "$ 100.00",
                }
            ),
            content_type="application/json",
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Invoice.objects.get(client_name="US Client").market, "US")

    def test_paid_plan_allows_hundredth_invoice_but_blocks_next(self):
        user = User.objects.create_user(username="paid@example.com", email="paid@example.com", password="strong-password-123")
        PlanSubscription.objects.create(market="US", owner=user, owner_email=user.email, plan="pro", status="active", activated_at=timezone.now())
        for index in range(99):
            Invoice.objects.create(
                market="US",
                owner=user,
                owner_email=user.email,
                business_name="Paid Business",
                client_name=f"Client {index}",
                service_name="Service",
                amount_before_gst="100.00",
                gst_rate="0.00",
                include_gst=False,
                total_text="$ 100.00",
                upi_link="",
                invoice_text="",
                currency_symbol="$",
                tax_label="Sales tax",
            )
        self.client.force_login(user)

        payload = {
            "business_name": "Paid Business",
            "client_name": "Allowed Client",
            "service_name": "Service",
            "amount_before_gst": "100",
            "gst_rate": "0",
            "include_gst": False,
            "currency_symbol": "$",
            "tax_label": "Sales tax",
            "total_text": "$ 100.00",
        }
        allowed = self.client.post(reverse("create_invoice"), data=json.dumps(payload), content_type="application/json", HTTP_HOST="rozledger.com")
        blocked = self.client.post(reverse("create_invoice"), data=json.dumps(payload), content_type="application/json", HTTP_HOST="rozledger.com")

        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("paid plan allows 100 invoices per month", blocked.json()["error"])

    def test_dashboard_invoice_form_creates_saved_invoice_without_javascript(self):
        user = User.objects.create_user(
            username="form-owner@example.com",
            email="form-owner@example.com",
            password="strong-password-123",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Form Business",
                "template": "modern",
                "accent_color": "#7c3aed",
                "business_phone": "+91 95160 22222",
                "business_address": "Form Address",
                "client_name": "Form Client",
                "client_phone": "+91 90000 22222",
                "client_address": "Client Address",
                "client_gstin": "32ABCDE1234F1Z5",
                "service_name": "Form Service",
                "include_gst": "on",
                "amount_before_gst": "2000",
                "gst_rate": "18",
                "due_days": "5",
                "upi_link": "upi://pay?pa=form@upi",
                "bank_details": "Form Bank",
                "thank_you_note": "Thanks from form.",
                "business_logo": SimpleUploadedFile(
                    "logo.png",
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2p\xb8\x00\x00\x00\x00IEND\xaeB`\x82",
                    content_type="image/png",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard/?invoice=created#invoices")
        invoice = Invoice.objects.get(owner=user)
        self.assertEqual(invoice.owner_email, "form-owner@example.com")
        self.assertEqual(invoice.template, "modern")
        self.assertEqual(invoice.accent_color, "#7c3aed")
        self.assertEqual(invoice.total_text, "₹ 2360.00")
        self.assertTrue(invoice.include_gst)
        self.assertEqual(invoice.business_phone, "+91 95160 22222")
        self.assertEqual(invoice.client_phone, "+91 90000 22222")
        self.assertEqual(invoice.client_address, "Client Address")
        self.assertEqual(invoice.thank_you_note, "Thanks from form.")
        self.assertIn("Form Service", invoice.invoice_text)
        self.assertIsNotNone(invoice.sales_voucher)
        self.assertEqual(invoice.sales_voucher.voucher_type, "sales")
        self.assertEqual(invoice.sales_voucher.total_amount, Decimal("2360.00"))
        self.assertTrue(invoice.sales_voucher.journal_entry.lines.filter(account__code="1100", debit=Decimal("2360.00")).exists())
        self.assertTrue(invoice.sales_voucher.journal_entry.lines.filter(account__code="4000", credit=Decimal("2000.00")).exists())
        self.assertTrue(invoice.sales_voucher.journal_entry.lines.filter(account__code="2110", credit=Decimal("180.00")).exists())
        self.assertTrue(invoice.sales_voucher.journal_entry.lines.filter(account__code="2120", credit=Decimal("180.00")).exists())
        self.assertTrue(invoice.business_logo.name)
        saved_client = SavedClient.objects.get(owner=user, owner_email="form-owner@example.com")
        self.assertEqual(saved_client.phone, "+91 90000 22222")
        self.assertEqual(saved_client.address, "Client Address")
        self.assertEqual(saved_client.gstin, "32ABCDE1234F1Z5")
        business_profile = BusinessProfile.objects.get(owner=user, owner_email="form-owner@example.com")
        self.assertEqual(business_profile.business_name, "Form Business")
        self.assertEqual(business_profile.business_phone, "+91 95160 22222")
        self.assertEqual(business_profile.business_address, "Form Address")
        self.assertEqual(business_profile.bank_details, "Form Bank")
        self.assertTrue(business_profile.business_logo.name)

        logo_response = self.client.get(reverse("invoice_logo", args=[invoice.public_token]))
        self.assertEqual(logo_response.status_code, 200)
        self.assertEqual(logo_response["Content-Type"], "image/png")
        pdf_response = self.client.get(reverse("invoice_pdf", args=[invoice.public_token]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        print_response = self.client.get(reverse("invoice_print", args=[invoice.public_token]))
        self.assertContains(print_response, "invoice-template-modern")
        self.assertContains(print_response, "--invoice-accent: #7c3aed")
        self.assertContains(print_response, "Phone: +91 95160 22222")
        self.assertContains(print_response, "Phone: +91 90000 22222")

    def test_dashboard_invoice_form_creates_without_gst(self):
        user = User.objects.create_user("nogst@example.com", "nogst@example.com", "strong-password-123")
        self.client.force_login(user)

        response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "No GST Business",
                "client_name": "No GST Client",
                "service_name": "No GST Service",
                "amount_before_gst": "1500",
                "gst_rate": "18",
                "due_days": "5",
                "bank_details": "Bank account details",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(owner=user)
        self.assertFalse(invoice.include_gst)
        self.assertEqual(invoice.gst_rate, 0)
        self.assertEqual(invoice.total_text, "₹ 1500.00")
        self.assertIn("GST: Not charged", invoice.invoice_text)
        self.assertIsNotNone(invoice.sales_voucher)
        self.assertEqual(invoice.sales_voucher.total_amount, Decimal("1500.00"))
        self.assertFalse(invoice.sales_voucher.journal_entry.lines.filter(account__code="2100").exists())

    def test_dashboard_invoice_form_saves_quantity_rate_line_items(self):
        user = User.objects.create_user("items@example.com", "items@example.com", "strong-password-123")
        self.client.force_login(user)

        response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Line Item Business",
                "client_name": "Line Item Client",
                "item_description": ["Website setup", "Monthly support", ""],
                "item_quantity": ["2", "3", "1"],
                "item_rate": ["1500", "500", "0"],
                "include_gst": "on",
                "gst_rate": "18",
                "due_days": "7",
                "thank_you_note": "Thank you.",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(owner=user)
        self.assertEqual(invoice.service_name, "Website setup")
        self.assertEqual(invoice.amount_before_gst, Decimal("4500.00"))
        self.assertIn("5310.00", invoice.total_text)
        self.assertEqual(InvoiceLineItem.objects.filter(invoice=invoice).count(), 2)
        self.assertIn("2 x", invoice.invoice_text)
        self.assertIn("Monthly support", invoice.invoice_text)
        self.assertIsNotNone(invoice.sales_voucher)
        self.assertEqual(invoice.sales_voucher.total_amount, Decimal("5310.00"))

        print_response = self.client.get(reverse("invoice_print", args=[invoice.public_token]))
        self.assertContains(print_response, "Qty")
        self.assertContains(print_response, "Rate")
        self.assertContains(print_response, "Website setup")
        self.assertContains(print_response, "Monthly support")
        self.assertContains(print_response, "4500.00")
        pdf_response = self.client.get(reverse("invoice_pdf", args=[invoice.public_token]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_dot_com_dashboard_invoice_form_uses_us_tax_copy(self):
        user = User.objects.create_user("us-form@example.com", "us-form@example.com", "strong-password-123")
        self.client.force_login(user)

        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        response = self.client.get(reverse("invoice_new"), HTTP_HOST="rozledger.com")

        self.assertContains(dashboard_response, "Tax ID")
        self.assertNotContains(dashboard_response, "GSTIN")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales tax rate %")
        self.assertContains(response, "Line items")
        self.assertContains(response, "Description, quantity and rate")
        self.assertContains(response, "Payment link")
        self.assertContains(response, "Client tax ID")
        self.assertNotContains(response, "Client GSTIN")
        self.assertNotContains(response, "Include GST")
        self.assertNotContains(response, "Amount before GST")
        self.assertNotContains(response, "UPI/payment")

    def test_dot_com_dashboard_invoice_save_creates_us_invoice(self):
        user = User.objects.create_user("us-save@example.com", "us-save@example.com", "strong-password-123")
        self.client.force_login(user)

        response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "River City Handyman",
                "business_phone": "(555) 014-7780",
                "business_address": "100 Main Street\nAustin, TX",
                "client_name": "Johnson Family",
                "client_phone": "(555) 014-9000",
                "client_address": "25 Oak Road\nAustin, TX",
                "service_name": "Door repair",
                "include_gst": "on",
                "amount_before_gst": "100",
                "gst_rate": "7.25",
                "due_days": "7",
                "upi_link": "https://pay.example.com/invoice-1",
                "bank_details": "ACH details available on request.",
                "thank_you_note": "Thank you for your business.",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(owner=user)
        self.assertEqual(invoice.currency_symbol, "$")
        self.assertEqual(invoice.tax_label, "Sales tax")
        self.assertEqual(invoice.total_text, "$ 107.25")
        self.assertIn("Sales tax: 7.25%", invoice.invoice_text)
        self.assertIn("Payment link: https://pay.example.com/invoice-1", invoice.invoice_text)
        self.assertNotIn("GST", invoice.invoice_text)
        self.assertNotIn("UPI", invoice.invoice_text)
        self.assertIsNotNone(invoice.sales_voucher)
        self.assertEqual(invoice.sales_voucher.total_amount, Decimal("107.25"))
        self.assertTrue(invoice.sales_voucher.journal_entry.lines.filter(account__code="2100", credit=Decimal("7.25")).exists())

        print_response = self.client.get(reverse("invoice_print", args=[invoice.public_token]), HTTP_HOST="rozledger.com")
        self.assertContains(print_response, "Sales tax")
        self.assertContains(print_response, "Payment link")
        self.assertContains(print_response, "www.rozledger.com")
        self.assertNotContains(print_response, "GSTIN")
        self.assertNotContains(print_response, "UPI")

    def test_invoice_edit_replaces_sales_voucher_before_payment_and_blocks_after_receipt(self):
        user = User.objects.create_user("edit-accounting@example.com", "edit-accounting@example.com", "strong-password-123")
        self.client.force_login(user)
        self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Edit Business",
                "client_name": "Edit Client",
                "item_description": ["Initial service"],
                "item_quantity": ["1"],
                "item_rate": ["100"],
                "gst_rate": "0",
                "due_days": "7",
                "thank_you_note": "Thanks.",
            },
            HTTP_HOST="rozledger.com",
        )
        invoice = Invoice.objects.get(owner=user)
        old_voucher_id = invoice.sales_voucher_id
        old_journal_id = invoice.sales_voucher.journal_entry_id

        edit_response = self.client.post(
            reverse("invoice_edit", args=[invoice.id]),
            {
                "template": "classic",
                "accent_color": "#126b4f",
                "business_name": "Edit Business",
                "client_name": "Edit Client",
                "item_description": ["Updated service"],
                "item_quantity": ["1"],
                "item_rate": ["150"],
                "gst_rate": "0",
                "total_text": "",
                "status": "sent",
                "upi_link": "",
                "bank_details": "",
                "thank_you_note": "Thanks.",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(edit_response.status_code, 302)
        invoice.refresh_from_db()
        self.assertNotEqual(invoice.sales_voucher_id, old_voucher_id)
        self.assertFalse(Voucher.objects.filter(id=old_voucher_id).exists())
        self.assertFalse(JournalEntry.objects.filter(id=old_journal_id).exists())
        self.assertEqual(invoice.sales_voucher.total_amount, Decimal("150.00"))
        self.assertTrue(invoice.sales_voucher.journal_entry.lines.filter(account__code="1100", debit=Decimal("150.00")).exists())
        self.assertTrue(invoice.sales_voucher.journal_entry.lines.filter(account__code="4000", credit=Decimal("150.00")).exists())

        self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-05",
                "payer_name": "Edit Client",
                "amount": "150.00",
                "method": "bank",
            },
            HTTP_HOST="rozledger.com",
        )
        paid_voucher_id = invoice.sales_voucher_id
        blocked = self.client.post(
            reverse("invoice_edit", args=[invoice.id]),
            {
                "template": "classic",
                "accent_color": "#126b4f",
                "business_name": "Edit Business",
                "client_name": "Edit Client",
                "item_description": ["Blocked service"],
                "item_quantity": ["1"],
                "item_rate": ["200"],
                "gst_rate": "0",
                "total_text": "",
                "status": "paid",
                "upi_link": "",
                "bank_details": "",
                "thank_you_note": "Thanks.",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "already has receipt postings")
        invoice.refresh_from_db()
        self.assertEqual(invoice.sales_voucher_id, paid_voucher_id)
        self.assertEqual(invoice.sales_voucher.total_amount, Decimal("150.00"))

    def test_invoice_owner_isolation_returns_404_for_other_customer(self):
        owner = User.objects.create_user("owner@example.com", "owner@example.com", "password-123456")
        other = User.objects.create_user("other@example.com", "other@example.com", "password-123456")
        invoice = Invoice.objects.create(
            owner=owner,
            owner_email=owner.email,
            business_name="Owner Business",
            client_name="Private Client",
            service_name="Private Service",
            amount_before_gst="500.00",
            gst_rate="18.00",
            total_text="₹ 590",
            upi_link="",
            invoice_text="Private invoice",
        )

        self.client.force_login(other)
        response = self.client.post(reverse("invoice_status", args=[invoice.id]), {"status": "paid"})

        self.assertEqual(response.status_code, 404)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "sent")

    def test_pro_request_creates_pending_subscription(self):
        user = User.objects.create_user("pro@example.com", "pro@example.com", "password-123456")
        self.client.force_login(user)

        response = self.client.post(reverse("request_pro_activation"), follow=True)

        self.assertEqual(response.status_code, 200)
        subscription = PlanSubscription.objects.get(owner=user)
        self.assertEqual(subscription.owner_email, "pro@example.com")
        self.assertEqual(subscription.plan, "pro")
        self.assertEqual(subscription.status, "requested")
        self.assertContains(response, "Admin approval is pending")

    def test_subscriptions_are_separated_by_market_for_same_user(self):
        user = User.objects.create_user("dual-sub@example.com", "dual-sub@example.com", "password-123456")
        self.client.force_login(user)

        response = self.client.post(reverse("request_pro_activation"), HTTP_HOST="rozledger.com", follow=True)

        self.assertEqual(response.status_code, 200)
        us_subscription = PlanSubscription.objects.get(owner=user, market="US")
        self.assertEqual(us_subscription.status, "requested")
        self.assertFalse(PlanSubscription.objects.filter(owner=user, market="IN").exists())

        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")

        self.assertEqual(dashboard_response.status_code, 200)
        india_subscription = PlanSubscription.objects.get(owner=user, market="IN")
        self.assertEqual(india_subscription.status, "free")

    def test_dashboard_seeds_default_chart_of_accounts_by_market(self):
        user = User.objects.create_user("chart@example.com", "chart@example.com", "password-123456")
        self.client.force_login(user)

        india_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        us_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")

        self.assertEqual(india_response.status_code, 200)
        self.assertEqual(us_response.status_code, 200)
        self.assertContains(us_response, "app-sidebar")
        self.assertContains(us_response, "View reports")
        self.assertContains(us_response, "Payments received")
        self.assertTrue(Account.objects.filter(owner=user, market="IN", code="1100", name="Accounts receivable").exists())
        self.assertTrue(Account.objects.filter(owner=user, market="US", code="1100", name="Accounts receivable").exists())

    def test_dashboard_guides_new_customer_to_daily_workflows(self):
        user = User.objects.create_user("guided@example.com", "guided@example.com", "password-123456")
        self.client.force_login(user)

        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        workflow_response = self.client.get(reverse("workflow_guide"), HTTP_HOST="rozledger.in")

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Next best actions")
        self.assertContains(dashboard_response, "Create business profile")
        self.assertContains(dashboard_response, "Workflow map")
        self.assertContains(dashboard_response, "Audit-safe corrections")
        self.assertContains(dashboard_response, "/dashboard/workflows/")
        self.assertEqual(workflow_response.status_code, 200)
        self.assertContains(workflow_response, "Invoice to receipt")
        self.assertContains(workflow_response, "Expense, bill and vendor payment")
        self.assertContains(workflow_response, "Trust controls")
        self.assertContains(workflow_response, "not mock billing")

    def test_monitoring_section_is_staff_only_and_shows_launch_checks(self):
        customer = User.objects.create_user("monitor-customer@example.com", "monitor-customer@example.com", "password-123456")
        self.client.force_login(customer)
        blocked = self.client.get(reverse("monitoring"), HTTP_HOST="rozledger.in")

        self.assertEqual(blocked.status_code, 404)

        staff = User.objects.create_user("monitor-staff@example.com", "monitor-staff@example.com", "password-123456", is_staff=True)
        self.client.force_login(staff)
        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        response = self.client.get(reverse("monitoring"), HTTP_HOST="rozledger.in")

        self.assertContains(dashboard_response, "Monitoring")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Launch operations center")
        self.assertContains(response, "Database connection OK")
        self.assertContains(response, "Endpoints to monitor externally")
        self.assertContains(response, "External monitor")
        self.assertContains(response, "Application error alerts")
        self.assertContains(response, "Database backup alert")
        self.assertContains(response, "https://rozledger.in/api/health")
        self.assertContains(response, "Django admin")

    def test_acceptance_india_service_user_can_bill_collect_and_track_vendor_bill(self):
        user = User.objects.create_user("accept-india@example.com", "accept-india@example.com", "password-123456", first_name="Acceptance Owner")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")

        profile_response = self.client.post(
            reverse("business_profile"),
            {
                "business_type": "service",
                "business_name": "Acceptance Services",
                "business_phone": "+91 95160 22222",
                "business_address": "Palarivattom\nKochi",
                "gstin": "32ABCDE1234F1Z5",
                "upi_link": "upi://pay?pa=accept@upi",
                "bank_details": "Acceptance Bank\nIFSC: TEST0001",
                "thank_you_note": "Thank you for your business.",
                "template": "classic",
                "accent_color": "#126b4f",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(profile_response.status_code, 302)

        invoice_response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Acceptance Services",
                "business_phone": "+91 95160 22222",
                "business_address": "Palarivattom\nKochi",
                "client_name": "Acceptance Client",
                "client_phone": "+91 90000 11111",
                "client_address": "Client Street\nKochi",
                "service_name": "Monthly support",
                "item_description": ["Monthly support"],
                "item_quantity": ["2"],
                "item_rate": ["500"],
                "include_gst": "on",
                "gst_rate": "18",
                "due_days": "7",
                "upi_link": "upi://pay?pa=accept@upi",
                "bank_details": "Acceptance Bank",
                "thank_you_note": "Thank you for your business.",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(invoice_response.status_code, 302)
        invoice = Invoice.objects.get(owner=user, client_name="Acceptance Client")
        self.assertEqual(invoice.total_text, "₹ 1180.00")

        dashboard_with_ar = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        self.assertContains(dashboard_with_ar, "Collect open invoices")
        self.assertContains(dashboard_with_ar, "Outstanding customer balance")

        receipt_response = self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-07",
                "payer_name": "Acceptance Client",
                "amount": "500.00",
                "method": "upi",
                "reference": "UPI-ACCEPT-1",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(receipt_response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "partially_paid")

        expense_account = Account.objects.get(owner=user, market="IN", code="5100")
        bill_response = self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-07",
                "due_date": "2026-06-20",
                "vendor_name": "Acceptance Vendor",
                "category": "Office expenses",
                "expense_account": str(expense_account.id),
                "amount": "300.00",
                "status": "unpaid",
                "payment_method": "bank",
                "reference": "BILL-ACCEPT-1",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(bill_response.status_code, 302)

        dashboard_with_ap = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        self.assertContains(dashboard_with_ap, "Pay vendor bills")
        self.assertContains(dashboard_with_ap, "Business profile ready")
        reports_response = self.client.get(reverse("reports"), HTTP_HOST="rozledger.in")
        self.assertContains(reports_response, "Profit & Loss")
        self.assertContains(reports_response, "AR aging by invoice and customer")
        self.assertContains(reports_response, "AP aging by vendor")

    def test_acceptance_us_service_user_gets_no_gst_dashboard_and_workflow_copy(self):
        user = User.objects.create_user("accept-us@example.com", "accept-us@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")

        invoice_response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Acceptance Handyman",
                "business_phone": "(215) 774-1500",
                "business_address": "114 Crockett Rd\nKing Of Prussia, PA 19406",
                "client_name": "Acceptance Homeowner",
                "client_phone": "(555) 010-1000",
                "client_address": "Client House\nPA",
                "service_name": "Door repair",
                "item_description": ["Door repair"],
                "item_quantity": ["1"],
                "item_rate": ["250"],
                "gst_rate": "0",
                "due_days": "7",
                "upi_link": "https://pay.example.com/acceptance",
                "bank_details": "Payment link preferred",
            },
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(invoice_response.status_code, 302)
        invoice = Invoice.objects.get(owner=user, client_name="Acceptance Homeowner")
        self.assertEqual(invoice.currency_symbol, "$")
        self.assertEqual(invoice.tax_label, "Sales tax")
        self.assertNotIn("GST", invoice.invoice_text)

        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        workflow_response = self.client.get(reverse("workflow_guide"), HTTP_HOST="rozledger.com")
        self.assertContains(dashboard_response, "Tax ID")
        self.assertNotContains(dashboard_response, "GSTIN")
        self.assertContains(workflow_response, "Sales tax and payment links")
        self.assertContains(workflow_response, "US users")

    def test_customer_can_create_business_profile_before_invoice(self):
        user = User.objects.create_user("profile@example.com", "profile@example.com", "password-123456", first_name="Profile Owner")
        self.client.force_login(user)

        response = self.client.post(
            reverse("business_profile"),
            {
                "business_type": "trading",
                "business_name": "Profile Business",
                "business_phone": "+91 95160 22222",
                "business_address": "Profile Street\nKochi",
                "gstin": "32ABCDE1234F1Z5",
                "upi_link": "upi://pay?pa=profile@upi",
                "bank_details": "Profile Bank\nIFSC: PROF0001",
                "thank_you_note": "Thanks from profile.",
                "template": "service",
                "accent_color": "#285c9f",
                "business_logo": SimpleUploadedFile(
                    "profile-logo.png",
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2p\xb8\x00\x00\x00\x00IEND\xaeB`\x82",
                    content_type="image/png",
                ),
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 302)
        profile = BusinessProfile.objects.get(owner=user, market="IN")
        self.assertEqual(profile.business_name, "Profile Business")
        self.assertEqual(profile.business_type, "trading")
        self.assertEqual(profile.gstin, "32ABCDE1234F1Z5")
        self.assertEqual(profile.template, "service")
        self.assertTrue(profile.business_logo.name)
        logo_response = self.client.get(reverse("business_profile_logo"), HTTP_HOST="rozledger.in")
        self.assertEqual(logo_response.status_code, 200)
        self.assertEqual(logo_response["Content-Type"], "image/png")
        invoice_form = self.client.get(reverse("invoice_new"), HTTP_HOST="rozledger.in")
        self.assertContains(invoice_form, 'value="Profile Business"')
        self.assertContains(invoice_form, "Profile Street")
        self.assertContains(invoice_form, "Profile Bank")
        self.assertContains(invoice_form, "Selected template preview")
        self.assertContains(invoice_form, "Service Pro")
        self.assertContains(invoice_form, "invoice-live-preview")
        self.assertContains(invoice_form, "/dashboard/business-profile/logo/")
        self.assertContains(invoice_form, "Profile Business")

    def test_business_setup_applies_type_specific_accounts(self):
        user = User.objects.create_user("setup@example.com", "setup@example.com", "password-123456", first_name="Setup Owner")
        self.client.force_login(user)

        response = self.client.post(reverse("business_setup"), {"business_type": "manufacturing"}, HTTP_HOST="rozledger.in")

        self.assertEqual(response.status_code, 200)
        profile = BusinessProfile.objects.get(owner=user, market="IN")
        self.assertEqual(profile.business_type, "manufacturing")
        self.assertTrue(Account.objects.filter(owner=user, market="IN", code="1220", name="Raw material inventory").exists())
        self.assertTrue(Account.objects.filter(owner=user, market="IN", code="1230", name="Finished goods inventory").exists())
        self.assertContains(response, "Manufacturing")
        self.assertContains(response, "Raw materials")

    def test_inventory_page_creates_item_and_stock_movement(self):
        user = User.objects.create_user("inventory@example.com", "inventory@example.com", "password-123456")
        self.client.force_login(user)

        item_response = self.client.post(
            reverse("inventory"),
            {
                "action": "item",
                "name": "Premium Widget",
                "sku": "PW-001",
                "item_type": "trading",
                "category": "Widgets",
                "unit": "pcs",
                "sales_rate": "250",
                "purchase_rate": "150",
                "opening_quantity": "10",
                "reorder_level": "3",
                "track_inventory": "on",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(item_response.status_code, 200)
        item = InventoryItem.objects.get(owner=user, sku="PW-001")
        self.assertEqual(item.name, "Premium Widget")
        self.assertEqual(StockMovement.objects.filter(item=item, movement_type="opening").count(), 1)
        self.assertContains(item_response, "10 pcs on hand")

        movement_response = self.client.post(
            reverse("inventory"),
            {
                "action": "movement",
                "item_id": str(item.id),
                "movement_type": "sale",
                "movement_date": "2026-06-05",
                "quantity": "4",
                "unit_cost": "150",
                "reference": "INV-1",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(movement_response.status_code, 200)
        self.assertEqual(StockMovement.objects.filter(item=item).count(), 2)
        self.assertContains(movement_response, "6 pcs on hand")
        self.assertContains(movement_response, "INV-1")

    def test_voucher_engine_posts_purchase_and_fifo_sales(self):
        user = User.objects.create_user("fifo@example.com", "fifo@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")

        first_purchase = self.client.post(
            reverse("voucher_new"),
            {
                "voucher_type": "purchase",
                "voucher_date": "2026-06-06",
                "party_name": "Supplier One",
                "item_name": "Widget",
                "quantity": "10",
                "rate": "100",
                "narration": "First purchase",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(first_purchase.status_code, 200)
        item = InventoryItem.objects.get(owner=user, name="Widget")
        self.assertEqual(StockCostLayer.objects.filter(item=item).count(), 1)

        second_purchase = self.client.post(
            reverse("voucher_new"),
            {
                "voucher_type": "purchase",
                "voucher_date": "2026-06-06",
                "party_name": "Supplier Two",
                "item_id": str(item.id),
                "quantity": "5",
                "rate": "120",
                "narration": "Second purchase",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(second_purchase.status_code, 200)
        self.assertEqual(StockCostLayer.objects.filter(item=item).count(), 2)

        sale = self.client.post(
            reverse("voucher_new"),
            {
                "voucher_type": "sales",
                "voucher_date": "2026-06-06",
                "party_name": "Retail Customer",
                "item_id": str(item.id),
                "quantity": "12",
                "rate": "200",
                "narration": "FIFO sale",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(sale.status_code, 200)
        self.assertContains(sale, "Sales voucher")
        layers = list(StockCostLayer.objects.filter(item=item).order_by("unit_cost"))
        self.assertEqual(layers[0].remaining_quantity, Decimal("0.00"))
        self.assertEqual(layers[1].remaining_quantity, Decimal("3.00"))
        self.assertEqual(StockLayerConsumption.objects.filter(sale_line__item=item).count(), 2)
        sales_voucher = Voucher.objects.filter(owner=user, voucher_type="sales").first()
        self.assertIsNotNone(sales_voucher)
        self.assertEqual(sales_voucher.total_amount, Decimal("2400.00"))
        entry = sales_voucher.journal_entry
        self.assertEqual(entry.total_debit, entry.total_credit)
        self.assertTrue(entry.lines.filter(account__code="5010", debit=Decimal("1240.00")).exists())
        self.assertTrue(entry.lines.filter(account__code="1210", credit=Decimal("1240.00")).exists())

    def test_voucher_engine_rejects_fifo_sale_without_stock(self):
        user = User.objects.create_user("fifo-short@example.com", "fifo-short@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        item = InventoryItem.objects.create(market="IN", owner=user, owner_email=user.email, name="No Stock Item", item_type="trading", unit="pcs", track_inventory=True)

        response = self.client.post(
            reverse("voucher_new"),
            {
                "voucher_type": "sales",
                "voucher_date": "2026-06-06",
                "party_name": "Retail Customer",
                "item_id": str(item.id),
                "quantity": "1",
                "rate": "200",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Insufficient FIFO stock")
        self.assertFalse(Voucher.objects.filter(owner=user, voucher_type="sales").exists())

    def test_voucher_engine_posts_accounting_voucher_types(self):
        user = User.objects.create_user("vouchers@example.com", "vouchers@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        expense = Account.objects.get(owner=user, market="IN", code="5100")
        bank = Account.objects.get(owner=user, market="IN", code="1010")
        cash = Account.objects.get(owner=user, market="IN", code="1000")
        receivable = Account.objects.get(owner=user, market="IN", code="1100")
        payable = Account.objects.get(owner=user, market="IN", code="2000")
        equity = Account.objects.get(owner=user, market="IN", code="3000")

        cases = [
            ("expense", "Airtel", "500.00", expense, bank, [(expense.code, "500.00", "0.00"), (bank.code, "0.00", "500.00")]),
            ("payment", "Supplier Payment", "750.00", payable, bank, [(payable.code, "750.00", "0.00"), (bank.code, "0.00", "750.00")]),
            ("receipt", "Customer Receipt", "1000.00", receivable, cash, [(cash.code, "1000.00", "0.00"), (receivable.code, "0.00", "1000.00")]),
            ("contra", "Cash Deposit", "300.00", cash, bank, [(cash.code, "300.00", "0.00"), (bank.code, "0.00", "300.00")]),
            ("journal", "Owner Adjustment", "200.00", expense, equity, [(expense.code, "200.00", "0.00"), (equity.code, "0.00", "200.00")]),
        ]

        for voucher_type, party_name, amount, primary, secondary, expected_lines in cases:
            with self.subTest(voucher_type=voucher_type):
                response = self.client.post(
                    reverse("voucher_new"),
                    {
                        "voucher_type": voucher_type,
                        "voucher_date": "2026-06-06",
                        "party_name": party_name,
                        "amount": amount,
                        "primary_account": str(primary.id),
                        "secondary_account": str(secondary.id),
                        "narration": f"{voucher_type} posting",
                    },
                    HTTP_HOST="rozledger.in",
                )
                self.assertEqual(response.status_code, 200)
                voucher = Voucher.objects.get(owner=user, voucher_type=voucher_type)
                self.assertEqual(voucher.total_amount, Decimal(amount))
                self.assertEqual(voucher.journal_entry.total_debit, Decimal(amount))
                self.assertEqual(voucher.journal_entry.total_credit, Decimal(amount))
                for account_code, debit, credit in expected_lines:
                    self.assertTrue(
                        voucher.journal_entry.lines.filter(account__code=account_code, debit=Decimal(debit), credit=Decimal(credit)).exists(),
                        f"Missing {voucher_type} line for {account_code}",
                    )

    def test_voucher_engine_rejects_receipt_with_non_cash_second_ledger(self):
        user = User.objects.create_user("receipt-guard@example.com", "receipt-guard@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        receivable = Account.objects.get(owner=user, market="IN", code="1100")
        revenue = Account.objects.get(owner=user, market="IN", code="4000")

        response = self.client.post(
            reverse("voucher_new"),
            {
                "voucher_type": "receipt",
                "voucher_date": "2026-06-06",
                "party_name": "Customer Receipt",
                "amount": "1000",
                "primary_account": str(receivable.id),
                "secondary_account": str(revenue.id),
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Receipt second ledger must be Cash or Bank.")
        self.assertFalse(Voucher.objects.filter(owner=user, voucher_type="receipt").exists())

    def test_ai_assistant_applies_setup_and_creates_invoice(self):
        user = User.objects.create_user("ai-setup@example.com", "ai-setup@example.com", "password-123456", first_name="AI Owner")
        self.client.force_login(user)

        analyze = self.client.post(reverse("ai_assistant"), {"action": "analyze", "prompt": "I run a travel agency. Set up my accounts."}, HTTP_HOST="rozledger.in")
        self.assertEqual(analyze.status_code, 200)
        self.assertContains(analyze, "AI setup assistant")
        self.assertContains(analyze, "Travel &amp; tour operator")

        apply = self.client.post(reverse("ai_assistant"), {"action": "apply_setup", "business_type": "travel"}, HTTP_HOST="rozledger.in")
        self.assertEqual(apply.status_code, 200)
        self.assertEqual(BusinessProfile.objects.get(owner=user).business_type, "travel")
        self.assertTrue(Account.objects.filter(owner=user, code="4130", name="Travel package income").exists())

        invoice = self.client.post(
            reverse("ai_assistant"),
            {
                "action": "create_invoice",
                "client_name": "ABC Travels",
                "description": "Dubai tour package",
                "quantity": "3",
                "rate": "25000",
                "due_days": "7",
                "gst_rate": "18",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(invoice.status_code, 200)
        saved = Invoice.objects.get(owner=user, client_name="ABC Travels")
        self.assertEqual(saved.amount_before_gst, Decimal("75000.00"))
        self.assertEqual(saved.line_items.count(), 1)
        self.assertIsNotNone(saved.sales_voucher)
        self.assertEqual(saved.sales_voucher.total_amount, Decimal("88500.00"))
        self.assertContains(invoice, "created for ABC Travels")

    def test_ai_assistant_records_expense_and_matches_payment(self):
        user = User.objects.create_user("ai-flow@example.com", "ai-flow@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")

        expense = self.client.post(
            reverse("ai_assistant"),
            {
                "action": "record_expense",
                "vendor_name": "Airtel",
                "category": "internet",
                "amount": "1800",
                "status": "paid",
                "payment_method": "bank",
                "expense_account": str(Account.objects.get(owner=user, market="US", code="5100").id),
                "prompt": "Paid 1800 to Airtel for internet by bank.",
            },
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(expense.status_code, 200)
        bill = VendorBill.objects.get(owner=user, vendor_name="Airtel")
        self.assertEqual(bill.amount, Decimal("1800.00"))
        self.assertIsNotNone(bill.voucher)
        self.assertEqual(bill.voucher.voucher_type, "expense")
        self.assertEqual(bill.journal_entry.source, "voucher_expense")

        invoice = Invoice.objects.create(
            owner=user,
            owner_email=user.email,
            market="US",
            business_name="AI Business",
            client_name="John Smith",
            service_name="Consulting",
            include_gst=False,
            amount_before_gst=Decimal("5000.00"),
            gst_rate=Decimal("0"),
            tax_label="Sales tax",
            currency_symbol="$",
            total_text="$ 5000.00",
            upi_link="",
            invoice_text="Invoice text",
        )
        analyze_payment = self.client.post(reverse("ai_assistant"), {"action": "analyze", "prompt": "Received 5000 from John by bank."}, HTTP_HOST="rozledger.com")
        self.assertContains(analyze_payment, "Possible invoice matches")
        self.assertContains(analyze_payment, f"RL-{invoice.created_at:%Y%m}-{invoice.id:05d}")

        payment = self.client.post(
            reverse("ai_assistant"),
            {"action": "record_payment", "invoice_id": str(invoice.id), "amount": "5000", "method": "bank", "prompt": "Received 5000 from John by bank."},
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(payment.status_code, 200)
        receipt = PaymentReceipt.objects.get(owner=user, invoice=invoice)
        self.assertEqual(receipt.amount, Decimal("5000.00"))
        self.assertIsNotNone(receipt.voucher)
        self.assertEqual(receipt.voucher.voucher_type, "receipt")
        self.assertTrue(receipt.journal_entry.lines.filter(account__code="1100", credit=Decimal("5000.00")).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")

    def test_ai_assistant_shows_dashboard_summary(self):
        user = User.objects.create_user("ai-summary@example.com", "ai-summary@example.com", "password-123456")
        self.client.force_login(user)

        response = self.client.get(reverse("ai_assistant"), HTTP_HOST="rozledger.in")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live summary")
        self.assertContains(response, "AI dashboard summary", count=0)
        self.assertContains(response, "Receivables are")

    def test_business_profile_save_writes_audit_log(self):
        user = User.objects.create_user("audit-profile@example.com", "audit-profile@example.com", "password-123456")
        self.client.force_login(user)

        self.client.post(
            reverse("business_profile"),
            {"business_name": "Audit Business", "template": "classic", "accent_color": "#126b4f"},
            HTTP_HOST="rozledger.in",
        )

        self.assertTrue(AuditLog.objects.filter(owner=user, action="business_profile.saved", object_type="BusinessProfile").exists())

    def test_cross_user_invoice_edit_is_blocked(self):
        owner = User.objects.create_user("secure-owner@example.com", "secure-owner@example.com", "password-123456")
        attacker = User.objects.create_user("secure-attacker@example.com", "secure-attacker@example.com", "password-123456")
        invoice = Invoice.objects.create(
            owner=owner,
            owner_email=owner.email,
            market="IN",
            business_name="Secure Business",
            client_name="Secure Client",
            service_name="Secure Service",
            include_gst=False,
            amount_before_gst=Decimal("100.00"),
            gst_rate=Decimal("0"),
            total_text="₹ 100.00",
            upi_link="",
            invoice_text="Invoice text",
        )
        self.client.force_login(attacker)

        response = self.client.get(reverse("invoice_edit", args=[invoice.id]), HTTP_HOST="rozledger.in")

        self.assertEqual(response.status_code, 404)

    def test_login_rate_limit_blocks_repeated_attempts(self):
        cache.clear()
        for index in range(9):
            response = self.client.post(
                reverse("login"),
                {"email": "rate-limit@example.com", "password": f"wrong-{index}"},
                HTTP_HOST="rozledger.in",
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many attempts")

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
    def test_security_headers_are_present(self):
        response = self.client.get("/api/health", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_customer_can_add_custom_account(self):
        user = User.objects.create_user("account@example.com", "account@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")

        response = self.client.post(
            reverse("create_account"),
            {"code": "6000", "name": "Fuel expense", "account_type": "expense", "normal_balance": "debit"},
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 302)
        account = Account.objects.get(owner=user, market="US", code="6000")
        self.assertEqual(account.name, "Fuel expense")
        self.assertEqual(account.account_type, "expense")

    def test_customer_can_post_balanced_journal_entry(self):
        user = User.objects.create_user("journal@example.com", "journal@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        expense = Account.objects.get(owner=user, market="US", code="5400")
        bank = Account.objects.get(owner=user, market="US", code="1010")

        response = self.client.post(
            reverse("journal_new"),
            {
                "entry_date": "2026-06-05",
                "memo": "Paid software subscription",
                "debit_account": str(expense.id),
                "debit_amount": "29.00",
                "credit_account": str(bank.id),
                "credit_amount": "29.00",
                "description": "Monthly app subscription",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 302)
        entry = JournalEntry.objects.get(owner=user, market="US")
        self.assertEqual(entry.total_debit, entry.total_credit)
        self.assertEqual(entry.lines.count(), 2)

    def test_unbalanced_journal_entry_is_rejected(self):
        user = User.objects.create_user("unbalanced@example.com", "unbalanced@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        expense = Account.objects.get(owner=user, market="IN", code="5100")
        bank = Account.objects.get(owner=user, market="IN", code="1010")

        response = self.client.post(
            reverse("journal_new"),
            {
                "entry_date": "2026-06-05",
                "memo": "Bad entry",
                "debit_account": str(expense.id),
                "debit_amount": "100.00",
                "credit_account": str(bank.id),
                "credit_amount": "90.00",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Journal entry must balance")
        self.assertFalse(JournalEntry.objects.filter(owner=user).exists())

    def test_customer_can_record_payment_received_for_invoice(self):
        user = User.objects.create_user("receipt@example.com", "receipt@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        invoice = Invoice.objects.create(
            owner=user,
            owner_email=user.email,
            market="US",
            business_name="Receipt Business",
            client_name="Receipt Client",
            service_name="Consulting",
            include_gst=False,
            amount_before_gst=Decimal("100.00"),
            gst_rate=Decimal("0"),
            tax_label="Sales tax",
            currency_symbol="$",
            total_text="$ 100.00",
            upi_link="https://pay.example.com",
            invoice_text="Invoice text",
        )

        response = self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-05",
                "payer_name": "Receipt Client",
                "amount": "100.00",
                "method": "bank",
                "reference": "stripe_123",
                "notes": "Paid in full",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 302)
        payment_form = self.client.get(f"{reverse('payment_new')}?invoice={invoice.id}", HTTP_HOST="rozledger.com")
        invoice_ref = f"RL-{invoice.created_at:%Y%m}-{invoice.id:05d}"
        self.assertContains(payment_form, invoice_ref)
        self.assertContains(payment_form, "Invoice / reference")
        self.assertContains(payment_form, "Receipt Client")
        self.assertContains(payment_form, 'value="0.00"')
        self.assertNotContains(payment_form, 'data-amount="100.00"')

        receipt = PaymentReceipt.objects.get(owner=user, market="US")
        self.assertEqual(receipt.amount, Decimal("100.00"))
        self.assertEqual(receipt.invoice_id, invoice.id)
        self.assertIsNotNone(receipt.voucher)
        self.assertEqual(receipt.voucher.voucher_type, "receipt")
        self.assertEqual(receipt.voucher.journal_entry_id, receipt.journal_entry_id)
        receipt_pdf = self.client.get(reverse("receipt_pdf", args=[receipt.id]), HTTP_HOST="rozledger.com")
        self.assertEqual(receipt_pdf.status_code, 200)
        self.assertEqual(receipt_pdf["Content-Type"], "application/pdf")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        entry = receipt.journal_entry
        self.assertEqual(entry.source, "voucher_receipt")
        self.assertEqual(entry.total_debit, entry.total_credit)
        self.assertTrue(entry.lines.filter(account__code="1010", debit=Decimal("100.00")).exists())
        self.assertTrue(entry.lines.filter(account__code="1100", credit=Decimal("100.00")).exists())
        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.assertContains(dashboard_response, invoice_ref)
        self.assertContains(dashboard_response, "Direct receipt", count=0)

    def test_customer_can_record_partial_and_final_invoice_payments(self):
        user = User.objects.create_user("partial-ar@example.com", "partial-ar@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        invoice = Invoice.objects.create(
            owner=user,
            owner_email=user.email,
            market="US",
            business_name="Partial AR Business",
            client_name="Partial Client",
            service_name="Monthly support",
            include_gst=False,
            amount_before_gst=Decimal("300.00"),
            gst_rate=Decimal("0"),
            tax_label="Sales tax",
            currency_symbol="$",
            total_text="$ 300.00",
            upi_link="",
            invoice_text="Invoice text",
        )
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")

        partial = self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-05",
                "payer_name": "Partial Client",
                "amount": "125.00",
                "method": "bank",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(partial.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "partially_paid")
        self.assertEqual(sum((payment.amount for payment in invoice.payments.all()), Decimal("0")), Decimal("125.00"))
        payment_form = self.client.get(f"{reverse('payment_new')}?invoice={invoice.id}", HTTP_HOST="rozledger.com")
        self.assertContains(payment_form, 'value="175.00"')

        overpay = self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-05",
                "payer_name": "Partial Client",
                "amount": "176.00",
                "method": "bank",
            },
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(overpay.status_code, 200)
        self.assertContains(overpay, "Payment cannot exceed the outstanding balance")
        self.assertEqual(PaymentReceipt.objects.filter(invoice=invoice).count(), 1)

        final = self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-06",
                "payer_name": "Partial Client",
                "amount": "175.00",
                "method": "bank",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(final.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(PaymentReceipt.objects.filter(invoice=invoice).count(), 2)

    def test_customer_can_record_unpaid_vendor_bill_as_accounts_payable(self):
        user = User.objects.create_user("payable@example.com", "payable@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        expense = Account.objects.get(owner=user, market="IN", code="5100")

        response = self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-05",
                "due_date": "2026-06-20",
                "vendor_name": "Office Vendor",
                "category": "Office supplies",
                "expense_account": str(expense.id),
                "amount": "750.00",
                "status": "unpaid",
                "payment_method": "bank",
                "reference": "BILL-1",
                "notes": "Printer paper",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 302)
        bill = VendorBill.objects.get(owner=user, market="IN")
        self.assertEqual(bill.status, "unpaid")
        self.assertEqual(bill.amount, Decimal("750.00"))
        self.assertIsNotNone(bill.voucher)
        self.assertEqual(bill.voucher.voucher_type, "expense")
        self.assertIsNone(bill.payment_voucher)
        entry = bill.journal_entry
        self.assertEqual(entry.source, "voucher_expense")
        self.assertEqual(entry.total_debit, entry.total_credit)
        self.assertTrue(entry.lines.filter(account__code="5100", debit=Decimal("750.00")).exists())
        self.assertTrue(entry.lines.filter(account__code="2000", credit=Decimal("750.00")).exists())

        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        self.assertContains(dashboard_response, "Accounts payable")
        self.assertContains(dashboard_response, "₹ 750.00")

    def test_customer_uploads_bill_photo_then_confirms_posting(self):
        user = User.objects.create_user("upload-expense@example.com", "upload-expense@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")

        upload_response = self.client.post(
            reverse("expense_upload"),
            {
                "action": "upload",
                "document": SimpleUploadedFile(
                    "airtel-1800-internet.jpg",
                    b"fake-image-content",
                    content_type="image/jpeg",
                ),
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(upload_response.status_code, 302)
        draft = ExpenseUploadDraft.objects.get(owner=user)
        self.assertIn("airtel", draft.extracted_text.lower())
        review_response = self.client.get(reverse("expense_upload_review", args=[draft.public_token]), HTTP_HOST="rozledger.in")
        self.assertContains(review_response, "Verify before posting")
        self.assertContains(review_response, "Type YES to post")

        blocked = self.client.post(
            reverse("expense_upload_review", args=[draft.public_token]),
            {
                "action": "post",
                "vendor_name": "Airtel",
                "category": "Internet",
                "expense_account": str(Account.objects.get(owner=user, market="IN", code="5100").id),
                "amount": "1800",
                "bill_date": "2026-06-06",
                "bill_status": "paid",
                "payment_method": "bank",
                "reference": "BILL-UP-1",
                "notes": "Uploaded from phone",
                "confirm": "no",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "Type YES")
        self.assertFalse(VendorBill.objects.filter(owner=user, vendor_name="Airtel").exists())

        posted = self.client.post(
            reverse("expense_upload_review", args=[draft.public_token]),
            {
                "action": "post",
                "vendor_name": "Airtel",
                "category": "Internet",
                "expense_account": str(Account.objects.get(owner=user, market="IN", code="5100").id),
                "amount": "1800",
                "bill_date": "2026-06-06",
                "bill_status": "paid",
                "payment_method": "bank",
                "reference": "BILL-UP-1",
                "notes": "Uploaded from phone",
                "confirm": "YES",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(posted.status_code, 302)
        bill = VendorBill.objects.get(owner=user, vendor_name="Airtel")
        self.assertEqual(bill.amount, Decimal("1800.00"))
        self.assertIsNotNone(bill.voucher)
        self.assertEqual(bill.voucher.voucher_type, "expense")
        self.assertEqual(bill.journal_entry.source, "voucher_expense")
        draft.refresh_from_db()
        self.assertEqual(draft.status, "posted")
        self.assertEqual(draft.vendor_bill, bill)
        detail_response = self.client.get(reverse("vendor_bill_detail", args=[bill.id]), HTTP_HOST="rozledger.in")
        self.assertContains(detail_response, "Uploaded bill preview")
        self.assertContains(detail_response, "airtel-1800-internet.jpg")
        attachment_response = self.client.get(reverse("vendor_bill_attachment", args=[bill.id, draft.id]), HTTP_HOST="rozledger.in")
        self.assertEqual(attachment_response.status_code, 200)
        self.assertEqual(attachment_response["Content-Type"], "image/jpeg")

    def test_customer_can_pay_selected_vendor_bill_with_payment_voucher(self):
        user = User.objects.create_user("pay-bill@example.com", "pay-bill@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        expense = Account.objects.get(owner=user, market="IN", code="5100")
        self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-01",
                "due_date": "2026-06-08",
                "vendor_name": "Courier Vendor",
                "category": "Courier",
                "expense_account": str(expense.id),
                "amount": "1200.00",
                "status": "unpaid",
                "payment_method": "bank",
                "reference": "BILL-PAY-1",
            },
            HTTP_HOST="rozledger.in",
        )
        bill = VendorBill.objects.get(owner=user, vendor_name="Courier Vendor")

        form = self.client.get(f"{reverse('vendor_bill_payment')}?bill={bill.id}", HTTP_HOST="rozledger.in")
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, "Courier Vendor")
        self.assertContains(form, 'value="1200.00"')

        response = self.client.post(
            reverse("vendor_bill_payment"),
            {
                "bill_id": str(bill.id),
                "payment_date": "2026-06-05",
                "amount": "1200.00",
                "method": "bank",
                "reference": "BANK-PAY-1",
                "notes": "Paid from bank",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "paid")
        self.assertEqual(bill.payment_reference, "BANK-PAY-1")
        self.assertEqual(bill.paid_date.strftime("%Y-%m-%d"), "2026-06-05")
        self.assertIsNotNone(bill.payment_voucher)
        self.assertEqual(bill.payment_voucher.voucher_type, "payment")
        payment = VendorBillPayment.objects.get(bill=bill)
        self.assertEqual(payment.amount, Decimal("1200.00"))
        self.assertEqual(payment.voucher_id, bill.payment_voucher_id)
        entry = bill.payment_voucher.journal_entry
        self.assertEqual(entry.source, "voucher_payment")
        self.assertTrue(entry.lines.filter(account__code="2000", debit=Decimal("1200.00")).exists())
        self.assertTrue(entry.lines.filter(account__code="1010", credit=Decimal("1200.00")).exists())

    def test_customer_can_record_partial_and_final_vendor_bill_payments(self):
        user = User.objects.create_user("partial-ap@example.com", "partial-ap@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        expense = Account.objects.get(owner=user, market="IN", code="5100")
        self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-01",
                "due_date": "2026-06-08",
                "vendor_name": "Partial Vendor",
                "category": "Supplies",
                "expense_account": str(expense.id),
                "amount": "900.00",
                "status": "unpaid",
                "payment_method": "bank",
                "reference": "PV-1",
            },
            HTTP_HOST="rozledger.in",
        )
        bill = VendorBill.objects.get(owner=user, vendor_name="Partial Vendor")

        partial = self.client.post(
            reverse("vendor_bill_payment"),
            {
                "bill_id": str(bill.id),
                "payment_date": "2026-06-05",
                "amount": "400.00",
                "method": "bank",
                "reference": "BANK-PART-1",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(partial.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "partially_paid")
        self.assertEqual(VendorBillPayment.objects.filter(bill=bill).count(), 1)
        form = self.client.get(f"{reverse('vendor_bill_payment')}?bill={bill.id}", HTTP_HOST="rozledger.in")
        self.assertContains(form, 'value="500.00"')

        overpay = self.client.post(
            reverse("vendor_bill_payment"),
            {
                "bill_id": str(bill.id),
                "payment_date": "2026-06-05",
                "amount": "501.00",
                "method": "bank",
            },
            HTTP_HOST="rozledger.in",
        )
        self.assertEqual(overpay.status_code, 200)
        self.assertContains(overpay, "Payment cannot exceed the outstanding vendor bill balance")
        self.assertEqual(VendorBillPayment.objects.filter(bill=bill).count(), 1)

        final = self.client.post(
            reverse("vendor_bill_payment"),
            {
                "bill_id": str(bill.id),
                "payment_date": "2026-06-06",
                "amount": "500.00",
                "method": "bank",
                "reference": "BANK-FINAL-1",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(final.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "paid")
        self.assertEqual(VendorBillPayment.objects.filter(bill=bill).count(), 2)
        self.assertEqual(sum((payment.amount for payment in bill.payments.all()), Decimal("0")), Decimal("900.00"))

    def test_customer_ledger_statement_shows_invoices_receipts_and_balance(self):
        user = User.objects.create_user("customer-ledger@example.com", "customer-ledger@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        invoice = Invoice.objects.create(
            owner=user,
            owner_email=user.email,
            market="US",
            business_name="Ledger Business",
            client_name="Ledger Client",
            service_name="Monthly support",
            include_gst=False,
            amount_before_gst=Decimal("300.00"),
            gst_rate=Decimal("0"),
            tax_label="Sales tax",
            currency_symbol="$",
            total_text="$ 300.00",
            upi_link="",
            invoice_text="Invoice text",
        )
        self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-05",
                "payer_name": "Ledger Client",
                "amount": "125.00",
                "method": "bank",
                "reference": "PARTIAL-1",
            },
            HTTP_HOST="rozledger.com",
        )

        response = self.client.get(f"{reverse('customer_ledger')}?customer=Ledger%20Client", HTTP_HOST="rozledger.com")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer statement")
        self.assertContains(response, "Ledger Client")
        self.assertContains(response, f"RL-{invoice.created_at:%Y%m}-{invoice.id:05d}")
        self.assertContains(response, "$ 300.00")
        self.assertContains(response, "$ 125.00")
        self.assertContains(response, "$ 175.00")
        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.assertContains(dashboard_response, "/dashboard/ledger/customers/?customer=Ledger+Client")

    def test_vendor_ledger_statement_shows_bills_payments_and_balance(self):
        user = User.objects.create_user("vendor-ledger@example.com", "vendor-ledger@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        expense = Account.objects.get(owner=user, market="IN", code="5100")
        self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-01",
                "due_date": "2026-06-08",
                "vendor_name": "Ledger Vendor",
                "category": "Supplies",
                "expense_account": str(expense.id),
                "amount": "900.00",
                "status": "unpaid",
                "payment_method": "bank",
                "reference": "LV-1",
            },
            HTTP_HOST="rozledger.in",
        )
        bill = VendorBill.objects.get(owner=user, vendor_name="Ledger Vendor")
        self.client.post(
            reverse("vendor_bill_payment"),
            {
                "bill_id": str(bill.id),
                "payment_date": "2026-06-05",
                "amount": "400.00",
                "method": "bank",
                "reference": "VPART-1",
            },
            HTTP_HOST="rozledger.in",
        )

        response = self.client.get(f"{reverse('vendor_ledger')}?vendor=Ledger%20Vendor", HTTP_HOST="rozledger.in")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vendor statement")
        self.assertContains(response, "Ledger Vendor")
        self.assertContains(response, "LV-1")
        self.assertContains(response, "900.00")
        self.assertContains(response, "400.00")
        self.assertContains(response, "500.00")
        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        self.assertContains(dashboard_response, "/dashboard/ledger/vendors/?vendor=Ledger+Vendor")

    def test_search_audit_and_reconciliation_pages(self):
        user = User.objects.create_user("ops-pages@example.com", "ops-pages@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        invoice_response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Ops Business",
                "client_name": "Ops Client",
                "item_description": ["Operations support"],
                "item_quantity": ["1"],
                "item_rate": ["100"],
                "gst_rate": "0",
                "due_days": "7",
                "thank_you_note": "Thanks.",
            },
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(invoice_response.status_code, 302)
        invoice = Invoice.objects.get(owner=user, client_name="Ops Client")
        self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-05",
                "payer_name": "Ops Client",
                "amount": "100.00",
                "method": "bank",
                "reference": "OPS-PAY-1",
            },
            HTTP_HOST="rozledger.com",
        )

        search_response = self.client.get(f"{reverse('global_search')}?q=Ops&type=all", HTTP_HOST="rozledger.com")
        self.assertEqual(search_response.status_code, 200)
        self.assertContains(search_response, "Find records")
        self.assertContains(search_response, "Ops Client")
        self.assertContains(search_response, "OPS-PAY-1")

        audit_response = self.client.get(f"{reverse('audit_trail')}?q=invoice", HTTP_HOST="rozledger.com")
        self.assertEqual(audit_response.status_code, 200)
        self.assertContains(audit_response, "Audit trail")
        self.assertContains(audit_response, "invoice.created")

        reconciliation_response = self.client.get(
            f"{reverse('reconciliation')}?account=1010&statement_balance=100.00",
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(reconciliation_response.status_code, 200)
        self.assertContains(reconciliation_response, "Bank and cash reconciliation")
        self.assertContains(reconciliation_response, "$ 100.00")
        self.assertContains(reconciliation_response, "$ 0.00")

    def test_invoice_accounting_trace_and_delete_safety_for_posted_invoice(self):
        user = User.objects.create_user("trace-invoice@example.com", "trace-invoice@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        response = self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Trace Business",
                "client_name": "Trace Client",
                "item_description": ["Monthly support"],
                "item_quantity": ["1"],
                "item_rate": ["250"],
                "gst_rate": "0",
                "due_days": "7",
                "thank_you_note": "Thanks.",
            },
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(owner=user, client_name="Trace Client")
        self.assertIsNotNone(invoice.sales_voucher)

        accounting = self.client.get(reverse("invoice_accounting_detail", args=[invoice.id]), HTTP_HOST="rozledger.com")
        self.assertEqual(accounting.status_code, 200)
        self.assertContains(accounting, "Invoice accounting")
        self.assertContains(accounting, invoice.sales_voucher.voucher_number)
        self.assertContains(accounting, "Correction safety")

        edit_response = self.client.get(reverse("invoice_edit", args=[invoice.id]), HTTP_HOST="rozledger.com")
        self.assertContains(edit_response, "Posted invoices cannot be deleted")
        delete_response = self.client.post(reverse("invoice_delete", args=[invoice.id]), HTTP_HOST="rozledger.com")
        self.assertEqual(delete_response.status_code, 200)
        self.assertContains(delete_response, "Invoice cannot be deleted")
        self.assertTrue(Invoice.objects.filter(id=invoice.id).exists())
        self.assertTrue(AuditLog.objects.filter(owner=user, action="invoice.delete_blocked", object_id=str(invoice.id)).exists())

    def test_receipt_voucher_and_journal_detail_pages_show_accounting_trace(self):
        user = User.objects.create_user("trace-receipt@example.com", "trace-receipt@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Trace Receipt Business",
                "client_name": "Receipt Trace Client",
                "item_description": ["Implementation"],
                "item_quantity": ["1"],
                "item_rate": ["300"],
                "gst_rate": "0",
                "due_days": "7",
            },
            HTTP_HOST="rozledger.com",
        )
        invoice = Invoice.objects.get(owner=user, client_name="Receipt Trace Client")
        payment_response = self.client.post(
            reverse("payment_new"),
            {
                "invoice_id": str(invoice.id),
                "payment_date": "2026-06-06",
                "payer_name": "Receipt Trace Client",
                "amount": "120.00",
                "method": "bank",
                "reference": "TRACE-PAY-1",
                "notes": "Partial payment",
            },
            HTTP_HOST="rozledger.com",
        )
        self.assertEqual(payment_response.status_code, 302)
        receipt = PaymentReceipt.objects.select_related("voucher", "journal_entry").get(owner=user, reference="TRACE-PAY-1")

        receipt_response = self.client.get(reverse("receipt_detail", args=[receipt.id]), HTTP_HOST="rozledger.com")
        self.assertEqual(receipt_response.status_code, 200)
        self.assertContains(receipt_response, "Download acknowledgement")
        self.assertContains(receipt_response, invoice.client_name)
        self.assertContains(receipt_response, receipt.voucher.voucher_number)

        voucher_response = self.client.get(reverse("voucher_detail", args=[receipt.voucher.id]), HTTP_HOST="rozledger.com")
        self.assertEqual(voucher_response.status_code, 200)
        self.assertContains(voucher_response, "Debit and credit lines")
        self.assertContains(voucher_response, "TRACE-PAY-1")
        self.assertContains(voucher_response, "Receipt")

        journal_response = self.client.get(reverse("journal_detail", args=[receipt.journal_entry.id]), HTTP_HOST="rozledger.com")
        self.assertEqual(journal_response.status_code, 200)
        self.assertContains(journal_response, "Journal entry")
        self.assertContains(journal_response, "Balanced")
        self.assertContains(journal_response, receipt.voucher.voucher_number)

    def test_customer_credit_note_posts_accounting_and_updates_invoice_balance(self):
        user = User.objects.create_user("credit-note@example.com", "credit-note@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Credit Business",
                "client_name": "Credit Client",
                "item_description": ["Correctable work"],
                "item_quantity": ["1"],
                "item_rate": ["1000"],
                "include_gst": "on",
                "gst_rate": "18",
                "due_days": "7",
            },
            HTTP_HOST="rozledger.in",
        )
        invoice = Invoice.objects.get(owner=user, client_name="Credit Client")

        response = self.client.post(
            reverse("credit_note_new", args=[invoice.id]),
            {
                "credit_date": "2026-06-06",
                "total_amount": "590.00",
                "reason": "Scope reduced",
                "notes": "Customer approved reduction",
            },
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 302)
        credit_note = CustomerCreditNote.objects.select_related("voucher", "journal_entry").get(owner=user)
        self.assertEqual(response["Location"], reverse("credit_note_detail", args=[credit_note.id]))
        self.assertEqual(credit_note.credit_note_number[:3], "CN-")
        self.assertEqual(credit_note.taxable_amount, Decimal("500.00"))
        self.assertEqual(credit_note.tax_amount, Decimal("90.00"))
        self.assertEqual(credit_note.total_amount, Decimal("590.00"))
        self.assertEqual(credit_note.voucher.voucher_type, "credit_note")
        self.assertTrue(credit_note.journal_entry.lines.filter(account__code="4000", debit=Decimal("500.00")).exists())
        self.assertTrue(credit_note.journal_entry.lines.filter(account__code="2110", debit=Decimal("45.00")).exists())
        self.assertTrue(credit_note.journal_entry.lines.filter(account__code="2120", debit=Decimal("45.00")).exists())
        self.assertTrue(credit_note.journal_entry.lines.filter(account__code="1100", credit=Decimal("590.00")).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "partially_credited")
        self.assertEqual(invoice_outstanding_amount(invoice), Decimal("590.00"))

        detail_response = self.client.get(reverse("credit_note_detail", args=[credit_note.id]), HTTP_HOST="rozledger.in")
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Credit note voucher ledger lines")
        self.assertContains(detail_response, "Scope reduced")

        accounting_response = self.client.get(reverse("invoice_accounting_detail", args=[invoice.id]), HTTP_HOST="rozledger.in")
        self.assertContains(accounting_response, credit_note.credit_note_number)
        self.assertContains(accounting_response, "590.00")

        ledger_response = self.client.get(f"{reverse('customer_ledger')}?customer=Credit%20Client", HTTP_HOST="rozledger.in")
        self.assertContains(ledger_response, "Credit note")
        self.assertContains(ledger_response, credit_note.credit_note_number)
        self.assertContains(ledger_response, "590.00")

    def test_customer_credit_note_blocks_amount_above_outstanding(self):
        user = User.objects.create_user("credit-note-block@example.com", "credit-note-block@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Credit Block Business",
                "client_name": "Credit Block Client",
                "item_description": ["Small job"],
                "item_quantity": ["1"],
                "item_rate": ["100"],
                "gst_rate": "0",
                "due_days": "7",
            },
            HTTP_HOST="rozledger.com",
        )
        invoice = Invoice.objects.get(owner=user, client_name="Credit Block Client")

        response = self.client.post(
            reverse("credit_note_new", args=[invoice.id]),
            {
                "credit_date": "2026-06-06",
                "total_amount": "101.00",
                "reason": "Too much credit",
            },
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Credit note cannot exceed the outstanding balance")
        self.assertFalse(CustomerCreditNote.objects.filter(owner=user).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "sent")

    def test_vendor_debit_note_posts_accounting_and_reduces_ap(self):
        user = User.objects.create_user("vendor-debit@example.com", "vendor-debit@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        expense = Account.objects.get(owner=user, market="IN", code="5100")
        self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-01",
                "due_date": "2026-06-15",
                "vendor_name": "Debit Vendor",
                "category": "Office supplies",
                "expense_account": str(expense.id),
                "amount": "1000.00",
                "status": "unpaid",
                "payment_method": "bank",
                "reference": "DV-1",
            },
            HTTP_HOST="rozledger.in",
        )
        bill = VendorBill.objects.get(owner=user, vendor_name="Debit Vendor")

        response = self.client.post(
            reverse("vendor_debit_note_new", args=[bill.id]),
            {"debit_date": "2026-06-06", "amount": "250.00", "reason": "Supplier discount"},
            HTTP_HOST="rozledger.in",
        )

        self.assertEqual(response.status_code, 302)
        debit_note = VendorDebitNote.objects.select_related("voucher", "journal_entry").get(owner=user)
        self.assertEqual(debit_note.amount, Decimal("250.00"))
        self.assertEqual(debit_note.voucher.voucher_type, "debit_note")
        self.assertTrue(debit_note.journal_entry.lines.filter(account__code="2000", debit=Decimal("250.00")).exists())
        self.assertTrue(debit_note.journal_entry.lines.filter(account__code="5100", credit=Decimal("250.00")).exists())
        bill.refresh_from_db()
        self.assertEqual(vendor_bill_outstanding_amount(bill), Decimal("750.00"))
        detail = self.client.get(reverse("vendor_debit_note_detail", args=[debit_note.id]), HTTP_HOST="rozledger.in")
        self.assertContains(detail, "Debit note voucher ledger lines")
        ledger = self.client.get(f"{reverse('vendor_ledger')}?vendor=Debit%20Vendor", HTTP_HOST="rozledger.in")
        self.assertContains(ledger, "Debit note")
        self.assertContains(ledger, debit_note.debit_note_number)

    def test_customer_receipt_reversal_restores_invoice_balance(self):
        user = User.objects.create_user("receipt-reversal@example.com", "receipt-reversal@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "Reversal Business",
                "client_name": "Reversal Client",
                "item_description": ["Support"],
                "item_quantity": ["1"],
                "item_rate": ["300"],
                "gst_rate": "0",
                "due_days": "7",
            },
            HTTP_HOST="rozledger.com",
        )
        invoice = Invoice.objects.get(owner=user, client_name="Reversal Client")
        self.client.post(
            reverse("payment_new"),
            {"invoice_id": str(invoice.id), "payment_date": "2026-06-06", "payer_name": "Reversal Client", "amount": "200.00", "method": "bank", "reference": "RR-1"},
            HTTP_HOST="rozledger.com",
        )
        receipt = PaymentReceipt.objects.get(owner=user, reference="RR-1")

        response = self.client.post(
            reverse("receipt_reversal_new", args=[receipt.id]),
            {"reversal_date": "2026-06-07", "amount": "50.00", "reason": "Wrong amount"},
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 302)
        reversal = PaymentReversal.objects.select_related("voucher", "journal_entry").get(owner=user, reversal_type="customer_receipt")
        self.assertEqual(reversal.amount, Decimal("50.00"))
        self.assertTrue(reversal.journal_entry.lines.filter(account__code="1100", debit=Decimal("50.00")).exists())
        self.assertTrue(reversal.journal_entry.lines.filter(account__code="1010", credit=Decimal("50.00")).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice_outstanding_amount(invoice), Decimal("150.00"))
        detail = self.client.get(reverse("payment_reversal_detail", args=[reversal.id]), HTTP_HOST="rozledger.com")
        self.assertContains(detail, "Reversal voucher ledger lines")

    def test_vendor_payment_reversal_restores_bill_balance(self):
        user = User.objects.create_user("vendor-payment-reversal@example.com", "vendor-payment-reversal@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        expense = Account.objects.get(owner=user, market="US", code="5100")
        self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-01",
                "vendor_name": "Payment Reverse Vendor",
                "category": "Supplies",
                "expense_account": str(expense.id),
                "amount": "500.00",
                "status": "unpaid",
                "payment_method": "bank",
            },
            HTTP_HOST="rozledger.com",
        )
        bill = VendorBill.objects.get(owner=user, vendor_name="Payment Reverse Vendor")
        self.client.post(reverse("vendor_bill_payment"), {"bill_id": str(bill.id), "payment_date": "2026-06-06", "amount": "300.00", "method": "bank", "reference": "VP-1"}, HTTP_HOST="rozledger.com")
        payment = VendorBillPayment.objects.get(owner=user, reference="VP-1")

        response = self.client.post(
            reverse("vendor_payment_reversal_new", args=[payment.id]),
            {"reversal_date": "2026-06-07", "amount": "100.00", "reason": "Wrong vendor payment"},
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 302)
        reversal = PaymentReversal.objects.select_related("journal_entry").get(owner=user, reversal_type="vendor_payment")
        self.assertTrue(reversal.journal_entry.lines.filter(account__code="1010", debit=Decimal("100.00")).exists())
        self.assertTrue(reversal.journal_entry.lines.filter(account__code="2000", credit=Decimal("100.00")).exists())
        bill.refresh_from_db()
        self.assertEqual(vendor_bill_outstanding_amount(bill), Decimal("300.00"))

    def test_reconciliation_can_save_selected_bank_lines(self):
        user = User.objects.create_user("reconcile-save@example.com", "reconcile-save@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.client.post(reverse("payment_new"), {"payment_date": "2026-06-06", "payer_name": "Bank Customer", "amount": "125.00", "method": "bank", "reference": "BANK-REC-1"}, HTTP_HOST="rozledger.com")
        line = JournalLine.objects.get(entry__owner=user, account__code="1010", debit=Decimal("125.00"))

        response = self.client.post(
            reverse("reconciliation"),
            {"account": "1010", "from": "2026-06-01", "to": "2026-06-30", "statement_balance": "125.00", "line_ids": [str(line.id)], "notes": "June statement"},
            HTTP_HOST="rozledger.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reconciliation saved")
        session = ReconciliationSession.objects.get(owner=user)
        self.assertEqual(session.statement_balance, Decimal("125.00"))
        self.assertEqual(session.ledger_balance, Decimal("125.00"))
        self.assertEqual(session.lines.count(), 1)

    def test_reports_show_profit_loss_ar_ap_and_cash_summary(self):
        user = User.objects.create_user("reports@example.com", "reports@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        invoice = Invoice.objects.create(
            owner=user,
            owner_email=user.email,
            market="US",
            business_name="Reports Business",
            client_name="Reports Client",
            service_name="Monthly service",
            include_gst=False,
            amount_before_gst=Decimal("300.00"),
            gst_rate=Decimal("0"),
            tax_label="Sales tax",
            currency_symbol="$",
            total_text="$ 300.00",
            upi_link="https://pay.example.com",
            invoice_text="Invoice text",
            due_days=0,
        )
        expense = Account.objects.get(owner=user, market="US", code="5100")
        self.client.post(
            reverse("payment_new"),
            {
                "payment_date": "2026-06-05",
                "payer_name": "Paid Client",
                "amount": "125.00",
                "method": "bank",
            },
            HTTP_HOST="rozledger.com",
        )
        self.client.post(
            reverse("expense_new"),
            {
                "bill_date": "2026-06-05",
                "due_date": "2026-06-20",
                "vendor_name": "Reports Vendor",
                "category": "Office supplies",
                "expense_account": str(expense.id),
                "amount": "75.00",
                "status": "unpaid",
                "payment_method": "bank",
            },
            HTTP_HOST="rozledger.com",
        )

        response = self.client.get(reverse("reports"), HTTP_HOST="rozledger.com")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profit & Loss")
        self.assertContains(response, "AR aging by invoice and customer")
        self.assertContains(response, "AP aging by vendor")
        self.assertContains(response, "Cash and bank position")
        self.assertContains(response, "Trial balance")
        self.assertContains(response, "Balance sheet")
        self.assertContains(response, "Tax summary")
        self.assertContains(response, "Sales by customer")
        self.assertContains(response, "Expenses by vendor")
        self.assertContains(response, f"RL-{invoice.created_at:%Y%m}-{invoice.id:05d}")
        self.assertContains(response, "Reports Client")
        self.assertContains(response, "Reports Vendor")
        self.assertContains(response, "$ 125.00")
        self.assertContains(response, "$ 75.00")

    def test_admin_can_activate_15_day_pro_trial(self):
        user = User.objects.create_user("trial@example.com", "trial@example.com", "password-123456")
        subscription = PlanSubscription.objects.create(owner=user, owner_email=user.email, plan="pro", status="requested")
        admin_model = PlanSubscriptionAdmin(PlanSubscription, admin.site)
        admin_model.message_user = lambda *args, **kwargs: None

        admin_model.activate_15_day_trial(request=None, queryset=PlanSubscription.objects.filter(id=subscription.id))

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.plan, "pro")
        self.assertIsNotNone(subscription.activated_at)
        self.assertIsNotNone(subscription.expires_at)
        self.assertGreater(subscription.expires_at, timezone.now() + timedelta(days=14))


@override_settings(**TEST_SETTINGS)
class LeadWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_public_lead_form_saves_and_redirects_to_thanks_page(self):
        response = self.client.post(
            reverse("lead_request_form"),
            {
                "name": "Lead Customer",
                "email": "lead@example.com",
                "phone": "9516022222",
                "business_type": "Freelancer",
            },
            HTTP_HOST="testserver",
            HTTP_REFERER="http://testserver/#pro",
        )

        self.assertEqual(response.status_code, 302)
        lead = Lead.objects.get()
        self.assertIn(f"/pro/thanks/{lead.public_token}/", response["Location"])

    def test_public_lead_form_allows_privacy_browser_without_referrer(self):
        response = self.client.post(
            reverse("lead_request_form"),
            {
                "name": "Privacy Customer",
                "email": "privacy@example.com",
                "phone": "9516022223",
                "business_type": "Consultant",
            },
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Lead.objects.filter(email="privacy@example.com").exists())

    def test_invalid_lead_api_returns_validation_errors(self):
        response = self.client.post(
            reverse("create_lead"),
            data=json.dumps({"name": "A", "email": "bad", "phone": "1"}),
            content_type="application/json",
            HTTP_HOST="testserver",
            HTTP_REFERER="http://testserver/#pro",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("fields", response.json())

    def test_lead_api_rate_limit_blocks_repeated_posts(self):
        statuses = []
        for index in range(5):
            payload = {
                "name": f"Rate Limit Customer {index}",
                "email": f"rate-{index}@example.com",
                "phone": f"95160222{index:02d}",
                "business_type": "Shop or local service",
            }
            response = self.client.post(
                reverse("create_lead"),
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_HOST="testserver",
                HTTP_REFERER="http://testserver/#pro",
            )
            statuses.append(response.status_code)

        self.assertEqual(statuses[:4], [201] * 4)
        self.assertEqual(statuses[4], 429)

    def test_lead_honeypot_blocks_bot_submission(self):
        response = self.client.post(
            reverse("lead_request_form"),
            {
                "name": "Bot Lead",
                "email": "bot@example.com",
                "phone": "9516022222",
                "business_type": "Freelancer",
                "website": "https://spam.example",
            },
            HTTP_HOST="testserver",
            HTTP_REFERER="http://testserver/#pro",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Lead.objects.filter(email="bot@example.com").exists())

    def test_lead_requires_same_origin_referrer(self):
        response = self.client.post(
            reverse("create_lead"),
            data=json.dumps(
                {
                    "name": "No Referrer",
                    "email": "noreferrer@example.com",
                    "phone": "9516022222",
                    "business_type": "Freelancer",
                }
            ),
            content_type="application/json",
            HTTP_HOST="testserver",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Lead.objects.filter(email="noreferrer@example.com").exists())


@override_settings(**TEST_SETTINGS)
class RazorpaySubscriptionTests(TestCase):
    WEBHOOK_SECRET = "whsec_test_123"

    def _gateway(self):
        from .models import PaymentGatewayConfig

        config = PaymentGatewayConfig.objects.create(market="IN", gateway="razorpay", enabled=True, mode="test")
        config.key_id = "rzp_test_key"
        config.key_secret = "rzp_test_secret"
        config.webhook_secret = self.WEBHOOK_SECRET
        config.save()
        return config

    def _sign(self, body: bytes) -> str:
        import hashlib
        import hmac

        return hmac.new(self.WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()

    def _post_event(self, event_type, subscription_id="sub_test_1", payment_id="pay_test_1", event_id="evt_1", secret_ok=True):
        body = json.dumps(
            {
                "event": event_type,
                "payload": {
                    "subscription": {"entity": {"id": subscription_id}},
                    "payment": {"entity": {"id": payment_id}},
                },
            }
        ).encode("utf-8")
        signature = self._sign(body) if secret_ok else "deadbeef"
        return self.client.post(
            reverse("razorpay_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature,
            HTTP_X_RAZORPAY_EVENT_ID=event_id,
            HTTP_HOST="rozledger.in",
        )

    def test_signature_helpers(self):
        import hashlib
        import hmac

        from . import razorpay_client

        body = b'{"event":"subscription.charged"}'
        good = hmac.new(b"sec", body, hashlib.sha256).hexdigest()
        self.assertTrue(razorpay_client.verify_webhook_signature(body, good, "sec"))
        self.assertFalse(razorpay_client.verify_webhook_signature(body, "bad", "sec"))
        self.assertFalse(razorpay_client.verify_webhook_signature(body, good, "wrong-secret"))
        pay_sig = hmac.new(b"keysec", b"pay_1|sub_1", hashlib.sha256).hexdigest()
        self.assertTrue(razorpay_client.verify_subscription_payment_signature("pay_1", "sub_1", pay_sig, "keysec"))
        self.assertFalse(razorpay_client.verify_subscription_payment_signature("pay_1", "sub_1", "bad", "keysec"))

    def test_webhook_charge_activates_subscription(self):
        self._gateway()
        subscription = PlanSubscription.objects.create(
            market="IN", owner_email="payer@example.com", plan="free", status="requested", razorpay_subscription_id="sub_test_1"
        )
        response = self._post_event("subscription.charged")
        self.assertEqual(response.status_code, 200)
        subscription.refresh_from_db()
        self.assertEqual(subscription.plan, "pro")
        self.assertEqual(subscription.status, "active")
        self.assertTrue(subscription.is_pro_active)
        self.assertEqual(subscription.last_payment_id, "pay_test_1")
        self.assertIsNotNone(subscription.expires_at)

    def test_webhook_rejects_invalid_signature(self):
        self._gateway()
        subscription = PlanSubscription.objects.create(
            market="IN", owner_email="payer2@example.com", plan="free", status="requested", razorpay_subscription_id="sub_test_2"
        )
        response = self._post_event("subscription.charged", subscription_id="sub_test_2", event_id="evt_2", secret_ok=False)
        self.assertEqual(response.status_code, 400)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, "requested")
        self.assertFalse(subscription.is_pro_active)

    def test_webhook_is_idempotent(self):
        from .models import PaymentEvent

        self._gateway()
        PlanSubscription.objects.create(
            market="IN", owner_email="payer3@example.com", plan="free", status="requested", razorpay_subscription_id="sub_test_3"
        )
        first = self._post_event("subscription.charged", subscription_id="sub_test_3", event_id="evt_dup")
        second = self._post_event("subscription.charged", subscription_id="sub_test_3", event_id="evt_dup")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(json.loads(second.content)["status"], "duplicate")
        self.assertEqual(PaymentEvent.objects.filter(event_id="evt_dup").count(), 1)

    def test_webhook_cancel_marks_subscription_cancelled(self):
        self._gateway()
        subscription = PlanSubscription.objects.create(
            market="IN",
            owner_email="payer4@example.com",
            plan="pro",
            status="active",
            razorpay_subscription_id="sub_test_4",
            activated_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=20),
        )
        response = self._post_event("subscription.cancelled", subscription_id="sub_test_4", event_id="evt_cancel")
        self.assertEqual(response.status_code, 200)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, "cancelled")
        self.assertFalse(subscription.is_pro_active)

    def test_billing_page_shows_webhook_status_for_staff(self):
        staff = User.objects.create_user("staffbill@example.com", "staffbill@example.com", "password-123456", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("pro_billing"), HTTP_HOST="rozledger.in")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Razorpay webhook status", content)
        self.assertIn("/webhooks/razorpay/", content)

    def test_billing_page_hides_webhook_status_for_non_staff(self):
        user = User.objects.create_user("normbill@example.com", "normbill@example.com", "password-123456")
        self.client.force_login(user)
        response = self.client.get(reverse("pro_billing"), HTTP_HOST="rozledger.in")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn("Razorpay webhook status", content)


@override_settings(**TEST_SETTINGS)
class GstSplitTests(TestCase):
    def _create_invoice_in(self, user, *, rate="1000", gst_rate="18", supply_type="intra", place="", host="rozledger.in", client="GST Client"):
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST=host)
        self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "GST Business",
                "client_name": client,
                "item_description": ["Taxable work"],
                "item_quantity": ["1"],
                "item_rate": [rate],
                "include_gst": "on",
                "gst_rate": gst_rate,
                "supply_type": supply_type,
                "place_of_supply": place,
                "due_days": "7",
            },
            HTTP_HOST=host,
        )
        return Invoice.objects.get(owner=user, client_name=client)

    def test_gst_split_intra_state_halves(self):
        from .views import gst_split

        result = gst_split(Decimal("360.00"), "intra")
        self.assertEqual(result["cgst"], Decimal("180.00"))
        self.assertEqual(result["sgst"], Decimal("180.00"))
        self.assertEqual(result["igst"], Decimal("0.00"))

    def test_gst_split_inter_state_is_igst(self):
        from .views import gst_split

        result = gst_split(Decimal("360.00"), "inter")
        self.assertEqual(result["igst"], Decimal("360.00"))
        self.assertEqual(result["cgst"], Decimal("0.00"))
        self.assertEqual(result["sgst"], Decimal("0.00"))

    def test_gst_split_odd_amount_loses_no_paisa(self):
        from .views import gst_split

        result = gst_split(Decimal("45.01"), "intra")
        self.assertEqual(result["cgst"] + result["sgst"], Decimal("45.01"))

    def test_intra_state_invoice_posts_cgst_and_sgst(self):
        user = User.objects.create_user("intra@example.com", "intra@example.com", "password-123456")
        invoice = self._create_invoice_in(user, supply_type="intra", client="Intra Client")
        self.assertEqual(invoice.supply_type, "intra")
        lines = invoice.sales_voucher.journal_entry.lines
        self.assertTrue(lines.filter(account__code="2110", credit=Decimal("90.00")).exists())
        self.assertTrue(lines.filter(account__code="2120", credit=Decimal("90.00")).exists())
        self.assertFalse(lines.filter(account__code="2130").exists())

    def test_inter_state_invoice_posts_igst(self):
        user = User.objects.create_user("inter@example.com", "inter@example.com", "password-123456")
        invoice = self._create_invoice_in(user, supply_type="inter", place="Maharashtra", client="Inter Client")
        self.assertEqual(invoice.supply_type, "inter")
        self.assertEqual(invoice.place_of_supply, "Maharashtra")
        lines = invoice.sales_voucher.journal_entry.lines
        self.assertTrue(lines.filter(account__code="2130", credit=Decimal("180.00")).exists())
        self.assertFalse(lines.filter(account__code__in=["2110", "2120"]).exists())

    def test_us_invoice_uses_single_sales_tax_account(self):
        user = User.objects.create_user("ussplit@example.com", "ussplit@example.com", "password-123456")
        self.client.force_login(user)
        self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.client.post(
            reverse("invoice_new"),
            {
                "business_name": "US Business",
                "client_name": "US Client",
                "item_description": ["Service"],
                "item_quantity": ["1"],
                "item_rate": ["1000"],
                "include_gst": "on",
                "gst_rate": "10",
                "due_days": "7",
            },
            HTTP_HOST="rozledger.com",
        )
        invoice = Invoice.objects.get(owner=user, client_name="US Client")
        lines = invoice.sales_voucher.journal_entry.lines
        self.assertTrue(lines.filter(account__code="2100", credit=Decimal("100.00")).exists())
        self.assertFalse(lines.filter(account__code__in=["2110", "2120", "2130"]).exists())

    def test_intra_invoice_print_shows_cgst_sgst(self):
        user = User.objects.create_user("printsplit@example.com", "printsplit@example.com", "password-123456")
        invoice = self._create_invoice_in(user, supply_type="intra", client="Print Client")
        response = self.client.get(reverse("invoice_print", args=[invoice.public_token]))
        content = b"".join(response.streaming_content).decode("utf-8") if hasattr(response, "streaming_content") else response.content.decode("utf-8")
        self.assertIn("CGST", content)
        self.assertIn("SGST", content)


@override_settings(**TEST_SETTINGS)
class GstnApiTests(TestCase):
    def _config(self, *, enabled=True, mode="sandbox"):
        from .models import GstnApiConfig

        config = GstnApiConfig.objects.create(
            market="IN", provider="whitebooks", enabled=enabled, mode=mode,
            base_url="https://gsp.example.com", gstin="29AAGCB1286Q000",
            api_email="dev@example.com", ip_address="1.2.3.4",
        )
        config.client_id = "cid"
        config.client_secret = "csecret"
        config.username = "uname"
        config.password = "pwd"
        config.save()
        return config

    def test_parse_envelope_success_dict(self):
        from . import gstn_client

        data = gstn_client.parse_envelope(json.dumps({"status_cd": "1", "data": {"Gstin": "X", "lgnm": "ACME"}}))
        self.assertEqual(data["lgnm"], "ACME")

    def test_parse_envelope_success_string_data(self):
        from . import gstn_client

        data = gstn_client.parse_envelope(json.dumps({"status_cd": "1", "data": json.dumps({"AuthToken": "tok"})}))
        self.assertEqual(data["AuthToken"], "tok")

    def test_parse_envelope_error_raises(self):
        from . import gstn_client

        with self.assertRaises(gstn_client.GstnApiError):
            gstn_client.parse_envelope(json.dumps({"status_cd": "0", "error": {"message": "bad gstin"}}))

    def test_parse_envelope_non_json_raises(self):
        from . import gstn_client

        with self.assertRaises(gstn_client.GstnApiError):
            gstn_client.parse_envelope("<html>blocked</html>")

    def test_token_ttl_by_mode(self):
        from . import gstn_client
        from .models import GstnApiConfig

        self.assertEqual(gstn_client.token_ttl(GstnApiConfig(mode="sandbox")), gstn_client.SANDBOX_TOKEN_TTL)
        self.assertEqual(gstn_client.token_ttl(GstnApiConfig(mode="production")), gstn_client.PRODUCTION_TOKEN_TTL)

    def test_authenticate_caches_token(self):
        from unittest import mock

        from . import gstn_client

        config = self._config()
        with mock.patch.object(gstn_client, "_request", return_value={"AuthToken": "TOKEN123"}) as request_mock:
            first = gstn_client.authenticate(config)
            second = gstn_client.authenticate(config)
        self.assertEqual(first, "TOKEN123")
        self.assertEqual(second, "TOKEN123")
        self.assertEqual(request_mock.call_count, 1)
        config.refresh_from_db()
        self.assertTrue(config.token_valid)
        self.assertEqual(config.auth_token, "TOKEN123")

    def test_get_gstin_details_builds_authed_request(self):
        from unittest import mock

        from . import gstn_client

        config = self._config()
        captured = {}

        def fake_request(cfg, method, path, headers=None, query=None, body=None):
            if path.endswith("/authenticate"):
                return {"AuthToken": "TKN"}
            captured.update(method=method, path=path, headers=headers, query=query)
            return {"Gstin": "29AAGCB1286Q000", "lgnm": "ACME"}

        with mock.patch.object(gstn_client, "_request", side_effect=fake_request):
            details = gstn_client.get_gstin_details(config, "29AAGCB1286Q000")
        self.assertEqual(details["lgnm"], "ACME")
        self.assertEqual(captured["method"], "GET")
        self.assertIn("GSTNDETAILS", captured["path"])
        self.assertEqual(captured["query"], {"param1": "29AAGCB1286Q000"})
        self.assertEqual(captured["headers"]["auth-token"], "TKN")
        self.assertEqual(captured["headers"]["client_id"], "cid")

    def test_admin_form_saves_encrypted_credentials(self):
        from .admin import GstnApiConfigForm

        form = GstnApiConfigForm(
            data={
                "market": "IN", "provider": "whitebooks", "enabled": False, "mode": "sandbox",
                "base_url": "https://gsp.example.com", "gstin": "29AAGCB1286Q000",
                "api_email": "dev@example.com", "ip_address": "1.2.3.4", "default_hsn": "998314",
                "api_client_id": "CID", "api_client_secret": "SECRET", "api_username": "USER", "api_password": "PASS",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        obj.refresh_from_db()
        self.assertEqual(obj.client_id, "CID")
        self.assertEqual(obj.password, "PASS")
        self.assertNotIn("SECRET", obj.encrypted_client_secret)

    def test_admin_form_requires_credentials_to_enable(self):
        from .admin import GstnApiConfigForm

        form = GstnApiConfigForm(
            data={
                "market": "IN", "provider": "whitebooks", "enabled": True, "mode": "sandbox",
                "base_url": "", "gstin": "", "api_email": "", "ip_address": "", "default_hsn": "",
                "api_client_id": "", "api_client_secret": "", "api_username": "", "api_password": "",
            }
        )
        self.assertFalse(form.is_valid())

    def test_gstn_settings_page_staff_only(self):
        staff = User.objects.create_user("gstnstaff@example.com", "gstnstaff@example.com", "password-123456", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("gstn_settings"), HTTP_HOST="rozledger.in")
        self.assertEqual(response.status_code, 200)
        self.assertIn("GSTN API settings", response.content.decode("utf-8"))

    def test_gstn_settings_page_hidden_for_non_staff(self):
        user = User.objects.create_user("gstnuser@example.com", "gstnuser@example.com", "password-123456")
        self.client.force_login(user)
        response = self.client.get(reverse("gstn_settings"), HTTP_HOST="rozledger.in")
        self.assertEqual(response.status_code, 404)

    def test_gstn_settings_validate_gstin_action(self):
        from unittest import mock

        from . import gstn_client

        staff = User.objects.create_user("gstnval@example.com", "gstnval@example.com", "password-123456", is_staff=True)
        self.client.force_login(staff)
        self._config()
        with mock.patch.object(gstn_client, "get_gstin_details", return_value={"lgnm": "ACME LTD", "Status": "Active"}):
            response = self.client.post(
                reverse("gstn_settings"),
                {"action": "validate_gstin", "gstin": "29AAGCB1286Q000"},
                HTTP_HOST="rozledger.in",
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("ACME LTD", response.content.decode("utf-8"))


@override_settings(**TEST_SETTINGS)
class DocumentWorkflowTests(TestCase):
    """Quotation -> Proforma -> Tax invoice document workflow.

    The defining rule: quotations and proforma invoices are non-accounting
    documents (no sales voucher, no receivable, no GST liability). Only a tax
    invoice posts to the ledger, and conversion to a tax invoice is what posts.
    """

    def _login(self, email="doc-owner@example.com"):
        user = User.objects.create_user(username=email, email=email, password="strong-password-123")
        self.client.force_login(user)
        return user

    def _create_document(self, document_type, *, amount="1000", gst_rate="18", include_gst="on"):
        response = self.client.post(
            reverse("invoice_new"),
            {
                "document_type": document_type,
                "business_name": "Doc Business",
                "template": "classic",
                "accent_color": "#126b4f",
                "business_phone": "+91 90000 11111",
                "business_address": "Doc Address",
                "client_name": "Doc Client",
                "client_phone": "+91 90000 22222",
                "client_address": "Client Address",
                "client_gstin": "",
                "place_of_supply": "Kerala",
                "supply_type": "intra",
                "service_name": "Doc Service",
                "include_gst": include_gst,
                "amount_before_gst": amount,
                "gst_rate": gst_rate,
                "due_days": "7",
                "upi_link": "",
                "bank_details": "",
                "thank_you_note": "Thanks.",
            },
        )
        self.assertEqual(response.status_code, 302)
        return Invoice.objects.order_by("-id").first()

    def test_quotation_does_not_post_to_ledger(self):
        self._login()
        inv = self._create_document("quotation")
        self.assertEqual(inv.document_type, "quotation")
        self.assertIsNone(inv.sales_voucher)
        self.assertEqual(invoice_outstanding_amount(inv), Decimal("0.00"))
        self.assertFalse(Voucher.objects.filter(voucher_type="sales").exists())

    def test_proforma_does_not_post_to_ledger(self):
        self._login()
        inv = self._create_document("proforma")
        self.assertEqual(inv.document_type, "proforma")
        self.assertIsNone(inv.sales_voucher)
        self.assertEqual(invoice_outstanding_amount(inv), Decimal("0.00"))
        self.assertFalse(Voucher.objects.filter(voucher_type="sales").exists())

    def test_tax_invoice_posts_to_ledger(self):
        self._login()
        inv = self._create_document("tax_invoice")
        self.assertEqual(inv.document_type, "tax_invoice")
        self.assertIsNotNone(inv.sales_voucher)
        self.assertGreater(invoice_outstanding_amount(inv), Decimal("0"))

    def test_document_number_prefixes(self):
        self._login()
        quotation = self._create_document("quotation")
        proforma = self._create_document("proforma")
        tax_invoice = self._create_document("tax_invoice")
        self.assertTrue(invoice_number(quotation).startswith("QTN-"))
        self.assertTrue(invoice_number(proforma).startswith("PI-"))
        self.assertTrue(invoice_number(tax_invoice).startswith("RL-"))

    def test_convert_quotation_to_proforma_keeps_books_clean(self):
        self._login()
        inv = self._create_document("quotation")
        response = self.client.post(reverse("invoice_convert", args=[inv.id]), {"target": "proforma"})
        self.assertEqual(response.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.document_type, "proforma")
        self.assertIsNone(inv.sales_voucher)

    def test_convert_proforma_to_tax_invoice_posts(self):
        self._login()
        inv = self._create_document("proforma")
        response = self.client.post(reverse("invoice_convert", args=[inv.id]), {"target": "tax_invoice"})
        self.assertEqual(response.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.document_type, "tax_invoice")
        self.assertEqual(inv.status, "sent")
        self.assertIsNotNone(inv.sales_voucher)
        self.assertTrue(inv.sales_voucher.journal_entry.lines.filter(account__code="1100").exists())
        self.assertGreater(invoice_outstanding_amount(inv), Decimal("0"))

    def test_convert_quotation_directly_to_tax_invoice_posts(self):
        self._login()
        inv = self._create_document("quotation")
        self.client.post(reverse("invoice_convert", args=[inv.id]), {"target": "tax_invoice"})
        inv.refresh_from_db()
        self.assertEqual(inv.document_type, "tax_invoice")
        self.assertIsNotNone(inv.sales_voucher)

    def test_cannot_convert_tax_invoice_backwards(self):
        self._login()
        inv = self._create_document("tax_invoice")
        voucher_id = inv.sales_voucher_id
        self.client.post(reverse("invoice_convert", args=[inv.id]), {"target": "quotation"})
        inv.refresh_from_db()
        self.assertEqual(inv.document_type, "tax_invoice")
        self.assertEqual(inv.sales_voucher_id, voucher_id)

    def test_convert_to_tax_invoice_is_idempotent(self):
        self._login()
        inv = self._create_document("proforma")
        self.client.post(reverse("invoice_convert", args=[inv.id]), {"target": "tax_invoice"})
        inv.refresh_from_db()
        first_voucher = inv.sales_voucher_id
        self.assertIsNotNone(first_voucher)
        # Already a tax invoice: a second convert is a no-op, no duplicate voucher.
        self.client.post(reverse("invoice_convert", args=[inv.id]), {"target": "tax_invoice"})
        inv.refresh_from_db()
        self.assertEqual(inv.sales_voucher_id, first_voucher)
        self.assertEqual(Voucher.objects.filter(voucher_type="sales").count(), 1)

    def test_payment_new_excludes_quotations_and_proformas(self):
        self._login()
        quotation = self._create_document("quotation")
        proforma = self._create_document("proforma")
        tax_invoice = self._create_document("tax_invoice")
        response = self.client.get(reverse("payment_new"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(invoice_number(tax_invoice), body)
        self.assertNotIn(invoice_number(quotation), body)
        self.assertNotIn(invoice_number(proforma), body)

    def test_create_invoice_api_respects_document_type(self):
        user = self._login("api-doc@example.com")
        response = self.client.post(
            reverse("create_invoice"),
            data=json.dumps(
                {
                    "business_name": "API Biz",
                    "client_name": "API Client",
                    "service_name": "API Service",
                    "amount_before_gst": "1000",
                    "gst_rate": "18",
                    "include_gst": True,
                    "document_type": "quotation",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        inv = Invoice.objects.order_by("-id").first()
        self.assertEqual(inv.document_type, "quotation")
        self.assertIsNone(inv.sales_voucher)

    def test_dashboard_shows_document_label_and_convert_action(self):
        self._login()
        self._create_document("quotation")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Quotation", body)
        self.assertIn("Convert to invoice", body)
        self.assertIn('name="target" value="proforma"', body)

    def test_quotation_public_page_shows_disclaimer(self):
        self._login()
        inv = self._create_document("quotation")
        response = self.client.get(reverse("invoice_print", args=[inv.public_token]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Quotation", body)
        self.assertIn("not a tax invoice", body)
