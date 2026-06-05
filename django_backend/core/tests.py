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
from .models import Account, AuditLog, BusinessProfile, Client as SavedClient
from .models import InventoryItem, Invoice, InvoiceLineItem, JournalEntry, Lead, PaymentReceipt, PlanSubscription, StockMovement, VendorBill


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
        self.assertIn("US small service businesses", content)
        self.assertIn("Sales tax", content)
        self.assertIn("chart of accounts", content)
        self.assertIn("journal entries", content)
        self.assertNotIn("Built for India", content)

    def test_dot_in_homepage_keeps_india_positioning(self):
        response = self.client.get("/", HTTP_HOST="rozledger.in")
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Built for India", content)
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

        print_response = self.client.get(reverse("invoice_print", args=[invoice.public_token]), HTTP_HOST="rozledger.com")
        self.assertContains(print_response, "Sales tax")
        self.assertContains(print_response, "Payment link")
        self.assertContains(print_response, "www.rozledger.com")
        self.assertNotContains(print_response, "GSTIN")
        self.assertNotContains(print_response, "UPI")

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
        self.assertEqual(bill.journal_entry.source, "ai_expense_paid")

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
        self.assertContains(payment_form, 'value="100.00"')

        receipt = PaymentReceipt.objects.get(owner=user, market="US")
        self.assertEqual(receipt.amount, Decimal("100.00"))
        self.assertEqual(receipt.invoice_id, invoice.id)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "paid")
        entry = receipt.journal_entry
        self.assertEqual(entry.source, "payment_received")
        self.assertEqual(entry.total_debit, entry.total_credit)
        self.assertTrue(entry.lines.filter(account__code="1010", debit=Decimal("100.00")).exists())
        self.assertTrue(entry.lines.filter(account__code="4000", credit=Decimal("100.00")).exists())
        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.com")
        self.assertContains(dashboard_response, invoice_ref)
        self.assertContains(dashboard_response, "Direct receipt", count=0)

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
        entry = bill.journal_entry
        self.assertEqual(entry.source, "vendor_bill")
        self.assertEqual(entry.total_debit, entry.total_credit)
        self.assertTrue(entry.lines.filter(account__code="5100", debit=Decimal("750.00")).exists())
        self.assertTrue(entry.lines.filter(account__code="2000", credit=Decimal("750.00")).exists())

        dashboard_response = self.client.get(reverse("dashboard"), HTTP_HOST="rozledger.in")
        self.assertContains(dashboard_response, "Accounts payable")
        self.assertContains(dashboard_response, "₹ 750.00")

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
