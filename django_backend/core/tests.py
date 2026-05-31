import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import PlanSubscriptionAdmin
from .models import Client as SavedClient
from .models import Invoice, Lead, PlanSubscription


TEST_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "SECURE_SSL_REDIRECT": False,
    "DEFAULT_FROM_EMAIL": "RozLedger <cs@rozledger.in>",
    "ROZLEDGER_NOTIFY_EMAIL": "cs@rozledger.in",
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


@override_settings(**TEST_SETTINGS)
class AccountWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_register_logs_customer_into_dashboard(self):
        response = self.client.post(
            reverse("register"),
            {
                "name": "Test Customer",
                "email": "customer@example.com",
                "password": "strong-password-123",
                "next": "/dashboard/",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertTrue(User.objects.filter(username="customer@example.com").exists())

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
                    "business_name": "Owner Business",
                    "business_address": "Owner Street\nKochi",
                    "client_name": "Alpha Client",
                    "client_address": "Client Road\nMumbai",
                    "client_gstin": "32ABCDE1234F1Z5",
                    "service_name": "Monthly service",
                    "include_gst": True,
                    "amount_before_gst": "1000",
                    "gst_rate": "18",
                    "due_days": 7,
                    "total_text": "Rs 1180",
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
        self.assertEqual(invoice.business_address, "Owner Street\nKochi")
        self.assertEqual(invoice.client_gstin, "32ABCDE1234F1Z5")
        self.assertEqual(invoice.bank_details, "Bank: Test Bank\nIFSC: TEST0001")
        self.assertTrue(SavedClient.objects.filter(owner=user, owner_email="owner@example.com").exists())

        pdf_response = self.client.get(reverse("invoice_pdf", args=[invoice.public_token]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

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
                "business_address": "Form Address",
                "client_name": "Form Client",
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
        self.assertEqual(invoice.total_text, "Rs 2360.00")
        self.assertTrue(invoice.include_gst)
        self.assertEqual(invoice.client_address, "Client Address")
        self.assertEqual(invoice.thank_you_note, "Thanks from form.")
        self.assertIn("Form Service", invoice.invoice_text)
        self.assertTrue(invoice.business_logo.name)

        logo_response = self.client.get(reverse("invoice_logo", args=[invoice.public_token]))
        self.assertEqual(logo_response.status_code, 200)
        self.assertEqual(logo_response["Content-Type"], "image/png")

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
        self.assertEqual(invoice.total_text, "Rs 1500.00")
        self.assertIn("GST: Not charged", invoice.invoice_text)

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
            total_text="Rs 590",
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
