from __future__ import annotations

import os
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "pages"
BLOG_DIR = ROOT / "blog"
VIDEOS_DIR = ROOT / "videos"
BASE_URL = os.getenv("ROZLEDGER_PUBLIC_URL", "https://rozledger.in").rstrip("/")
BRAND_HTML = '<a class="brand" href="/" aria-label="RozLedger home"><img class="brand-logo" src="/rozledger-logo.png" alt="RozLedger" /></a>'


GOOGLE_TAG = """<!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-KLPE4CG3TK"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-KLPE4CG3TK');
    </script>"""


PAGES = [
    ("gst-invoice-format-for-freelancers-india", "GST Invoice Format for Freelancers in India", "Freelancers", "invoice"),
    ("gst-invoice-format-for-digital-marketing-agency", "GST Invoice Format for Digital Marketing Agency", "Agencies", "invoice"),
    ("invoice-format-for-tuition-teachers-india", "Invoice Format for Tuition Teachers in India", "Tutors", "invoice"),
    ("invoice-format-for-home-service-business-india", "Invoice Format for Home Service Businesses in India", "Home services", "invoice"),
    ("consultant-invoice-format-india", "Consultant Invoice Format India", "Consultants", "invoice"),
    ("rent-receipt-generator-for-salaried-employees-india", "Rent Receipt Generator for Salaried Employees in India", "Employees", "receipt"),
    ("cash-receipt-format-for-small-shop-india", "Cash Receipt Format for Small Shops in India", "Retail shops", "receipt"),
    ("advance-payment-receipt-format-india", "Advance Payment Receipt Format India", "Service businesses", "receipt"),
    ("tuition-fee-receipt-format-india", "Tuition Fee Receipt Format India", "Tutors", "receipt"),
    ("maintenance-payment-receipt-format-india", "Maintenance Payment Receipt Format India", "Apartment services", "receipt"),
    ("gst-calculator-18-percent-india", "GST Calculator 18 Percent India", "GST users", "gst"),
    ("gst-calculator-5-percent-india", "GST Calculator 5 Percent India", "GST users", "gst"),
    ("gst-calculator-12-percent-india", "GST Calculator 12 Percent India", "GST users", "gst"),
    ("gst-calculator-28-percent-india", "GST Calculator 28 Percent India", "GST users", "gst"),
    ("reverse-gst-calculator-india", "Reverse GST Calculator India", "GST users", "gst"),
    ("whatsapp-payment-reminder-message-hindi", "WhatsApp Payment Reminder Message in Hindi", "Hindi users", "whatsapp"),
    ("whatsapp-payment-reminder-message-english", "WhatsApp Payment Reminder Message in English", "English users", "whatsapp"),
    ("polite-payment-reminder-message-for-clients", "Polite Payment Reminder Message for Clients", "Freelancers", "whatsapp"),
    ("overdue-payment-reminder-whatsapp-template", "Overdue Payment Reminder WhatsApp Template", "Small businesses", "whatsapp"),
    ("upi-payment-reminder-message-template", "UPI Payment Reminder Message Template", "UPI users", "whatsapp"),
    ("invoice-due-date-calculator-india", "Invoice Due Date Calculator India", "Businesses", "invoice"),
    ("daily-sales-target-calculator-small-business", "Daily Sales Target Calculator for Small Business", "Business owners", "target"),
    ("freelancer-payment-follow-up-message", "Freelancer Payment Follow Up Message", "Freelancers", "whatsapp"),
    ("agency-retainer-invoice-template-india", "Agency Retainer Invoice Template India", "Agencies", "invoice"),
    ("consulting-fee-receipt-template-india", "Consulting Fee Receipt Template India", "Consultants", "receipt"),
    ("gst-bill-format-for-shop-india", "GST Bill Format for Shop India", "Retail shops", "invoice"),
    ("service-bill-format-with-gst-india", "Service Bill Format with GST India", "Service businesses", "invoice"),
    ("payment-link-message-for-customers-india", "Payment Link Message for Customers India", "UPI users", "whatsapp"),
    ("invoice-template-for-instagram-sellers-india", "Invoice Template for Instagram Sellers India", "Online sellers", "invoice"),
    ("payment-collection-tracker-for-small-business", "Payment Collection Tracker for Small Business", "Business owners", "target"),
]


BLOGS = [
    (
        "why-small-businesses-need-rozledger",
        "Why Small Businesses Need RozLedger for Daily Billing",
        "Learn why RozLedger helps Indian freelancers, tutors, agencies and local shops prepare invoices, GST totals, UPI links and reminders with less manual work.",
        "Small businesses in India often handle billing between customer calls, WhatsApp messages and daily operations. RozLedger gives them one practical place to prepare billing text, calculate GST, create UPI payment links and send polite reminders.",
        [
            "It reduces repeated typing for common invoice and reminder messages.",
            "It keeps GST totals, due dates and payment notes in the same workflow.",
            "It helps business owners send clearer payment communication to customers.",
            "It works in the browser, so users can start without installing software.",
        ],
    ),
    (
        "rozledger-for-freelancers-india",
        "How RozLedger Helps Freelancers Send Cleaner Invoices",
        "RozLedger helps freelancers prepare invoice text, GST totals, payment links and WhatsApp reminders for repeat client work.",
        "Freelancers need fast billing that still looks clear to the client. RozLedger helps create invoice text, add service details, calculate GST where applicable and send a payment message without rebuilding the same note every time.",
        [
            "Freelancers can save time when billing monthly retainers or project work.",
            "The generated text is easy to copy into email, WhatsApp or a PDF invoice page.",
            "The dashboard can keep saved invoices connected to the freelancer email.",
            "Payment reminders stay polite and professional.",
        ],
    ),
    (
        "rozledger-for-tutors-and-coaching-centres",
        "RozLedger for Tutors and Coaching Centres",
        "Tutors and coaching centres can use RozLedger for fee receipts, reminders and simple monthly payment tracking.",
        "Tutors often collect recurring fees from students or parents. RozLedger helps prepare receipt text, payment reminders and due-date messages quickly, which keeps communication consistent during busy teaching days.",
        [
            "Fee receipt text can be prepared quickly after payment.",
            "WhatsApp reminders can be copied without sounding harsh.",
            "UPI links and QR codes make fee collection easier.",
            "Saved invoice records help review what was sent earlier.",
        ],
    ),
    (
        "rozledger-for-digital-marketing-agencies",
        "RozLedger for Digital Marketing Agencies",
        "Agencies can use RozLedger for retainer invoices, GST calculation, UPI collection links and client reminders.",
        "Agencies usually bill clients every month for retainers, ad management or content services. RozLedger supports this repeat workflow by turning business, client, service and GST details into a ready billing message.",
        [
            "Monthly service invoices can be prepared faster.",
            "GST totals are visible before the message is sent.",
            "Client payment reminders can be sent from WhatsApp.",
            "Pro workflows can later support saved clients and payment status tracking.",
        ],
    ),
    (
        "rozledger-requirements-to-start",
        "What You Need Before Using RozLedger",
        "A simple checklist of information needed to use RozLedger: business name, client name, invoice amount, GST rate, UPI ID and contact details.",
        "RozLedger is intentionally simple. Most users can start with only a few details: their business name, client name, service description, amount, GST rate if applicable and UPI ID for payment collection.",
        [
            "Business name and client name for invoice text.",
            "Service or product description for the billing note.",
            "Amount before GST and the GST rate if GST applies.",
            "UPI ID and payee name for payment links.",
        ],
    ),
    (
        "rozledger-easy-invoice-workflow",
        "The Easy RozLedger Invoice Workflow",
        "See the simple step-by-step workflow for creating invoice text, GST totals, UPI links and printable invoice pages in RozLedger.",
        "The basic RozLedger workflow is: enter invoice details, check GST and total, copy the generated text, save the invoice, then open the printable page or send it on WhatsApp.",
        [
            "The tool updates totals as the user changes the amount or GST rate.",
            "Invoice text is generated in a copy-ready format.",
            "Saved invoices get a printable page link.",
            "The dashboard can show saved invoice history for logged-in users.",
        ],
    ),
    (
        "gst-calculation-made-simple-with-rozledger",
        "GST Calculation Made Simple with RozLedger",
        "RozLedger helps users calculate GST amount and total payable before sending invoice or payment reminder text.",
        "GST calculation can slow down billing when done manually. RozLedger shows GST and total payable as soon as the user enters the amount and GST percentage, making it easier to prepare billing communication.",
        [
            "Supports common GST rates like 5%, 12%, 18% and 28%.",
            "Shows amount, GST and total in a customer-friendly message.",
            "Helps users avoid basic arithmetic mistakes in routine billing.",
            "Users should still verify tax compliance with a qualified professional.",
        ],
    ),
    (
        "upi-payment-links-with-rozledger",
        "Using RozLedger to Create UPI Payment Links",
        "RozLedger can create UPI payment links and QR codes from a UPI ID, amount, payee name and payment note.",
        "UPI is one of the easiest ways for many Indian small businesses to collect payments. RozLedger turns a UPI ID, amount and note into a payment URI and QR code that can be shared with customers.",
        [
            "The payment amount can follow the invoice total.",
            "The note can include the service or invoice purpose.",
            "The QR code is useful for mobile-first customers.",
            "Users should verify their UPI ID before sending it to customers.",
        ],
    ),
    (
        "whatsapp-payment-reminders-with-rozledger",
        "WhatsApp Payment Reminders with RozLedger",
        "RozLedger helps business owners create polite WhatsApp payment reminders with invoice amount and payment link details.",
        "Payment follow-up is sensitive. RozLedger gives users a polite reminder format that includes the amount due and payment link, so the message is clear without becoming aggressive.",
        [
            "Useful for overdue invoices and same-day payment follow-up.",
            "Can include a UPI payment link for easier action.",
            "Keeps wording consistent across repeat customers.",
            "Saves time for owners who send reminders manually.",
        ],
    ),
    (
        "daily-collection-targets-with-rozledger",
        "Daily Collection Targets with RozLedger",
        "RozLedger helps small business owners estimate daily collection targets and understand how much is left to collect today.",
        "Revenue is not only about invoices. Small businesses also need to understand collection gaps. RozLedger includes a simple daily target tool to compare monthly goals, working days and today's collection.",
        [
            "Shows daily collection target from monthly goal and working days.",
            "Shows how much is left to collect today.",
            "Estimates orders needed from average order value.",
            "Helps owners plan payment follow-ups more deliberately.",
        ],
    ),
    (
        "rozledger-dashboard-benefits",
        "Benefits of the RozLedger Dashboard",
        "The RozLedger dashboard helps logged-in users see saved invoices, clients, Pro requests and payment statuses in one place.",
        "A dashboard becomes useful when a business repeats the same billing work every week. RozLedger lets users save invoices by email, view client records and mark invoices as paid.",
        [
            "Saved invoice history is easier than searching old messages.",
            "Client records reduce repeated typing.",
            "Payment status helps separate paid and pending invoices.",
            "PDF download makes invoice sharing more practical.",
        ],
    ),
    (
        "rozledger-pro-benefits",
        "What RozLedger Pro Is Planned to Offer",
        "RozLedger Pro is planned for users who need saved clients, invoice history, PDF downloads and payment tracking.",
        "RozLedger Pro is being prepared for users who want more than the free daily tools. The goal is to support saved clients, invoice records, PDF download and payment status workflows.",
        [
            "Pro is planned for repeat billing users.",
            "Saved clients and invoice history are useful for monthly work.",
            "Payment tracking helps users know what needs follow-up.",
            "Paid checkout will be enabled after Razorpay approval.",
        ],
    ),
    (
        "rozledger-for-local-shops",
        "RozLedger for Local Shops and Service Businesses",
        "Local shops and service businesses can use RozLedger for simple bills, receipts, UPI links and customer payment follow-ups.",
        "Many local businesses collect money through a mix of cash, UPI and bank transfers. RozLedger gives them simple billing and reminder text they can prepare quickly from a phone or desktop.",
        [
            "Useful for small service bills and maintenance payments.",
            "Receipt text can be copied after payment.",
            "UPI QR and payment links support digital collection.",
            "Daily target tracking helps with collection discipline.",
        ],
    ),
    (
        "rozledger-for-consultants",
        "RozLedger for Consultants and Independent Professionals",
        "Consultants can use RozLedger to prepare service invoices, GST totals, receipt text and polite client follow-ups.",
        "Consultants often bill for advisory, implementation or retainer work. RozLedger supports a simple professional workflow: describe the service, calculate total payable and send a clean reminder if payment is pending.",
        [
            "Service descriptions can be added clearly.",
            "GST totals are calculated before sharing the invoice message.",
            "PDF invoice pages can be generated from saved invoices.",
            "Follow-up messages help maintain a professional tone.",
        ],
    ),
    (
        "rozledger-vs-manual-billing",
        "RozLedger vs Manual Billing on WhatsApp",
        "Compare manual WhatsApp billing with a cleaner RozLedger workflow for invoice text, GST totals, UPI links and reminders.",
        "Manual billing often means typing the same details again and again. RozLedger keeps the core billing steps together so business owners can create a clearer message with less effort.",
        [
            "Manual billing is fast at first but becomes repetitive.",
            "RozLedger makes totals and payment links easier to prepare.",
            "Saved invoices create a better history than scattered chats.",
            "Templates help users maintain consistent wording.",
        ],
    ),
    (
        "how-rozledger-supports-organic-growth",
        "How RozLedger Helps Small Businesses Collect Payments Faster",
        "Learn how RozLedger helps Indian small businesses send clearer invoices, UPI payment links and polite follow-up messages.",
        "Many small businesses do good work but lose time in payment follow-up. RozLedger helps by making invoice text, GST totals, UPI payment links and WhatsApp reminders easier to prepare and send.",
        [
            "Customers receive clearer billing details before they pay.",
            "UPI links and QR codes reduce friction in collection.",
            "Polite reminders make follow-up easier for the business owner.",
            "Saved invoices help users review pending and completed payments.",
        ],
    ),
    (
        "rozledger-getting-started-guide",
        "Getting Started with RozLedger in 5 Minutes",
        "A quick getting-started guide for using RozLedger to create invoice text, GST totals, UPI payment links and saved invoice records.",
        "New users can start with RozLedger quickly. They do not need accounting software knowledge to try the free tool; they only need basic billing details and a UPI ID if they want payment links.",
        [
            "Open the free tool and enter business details.",
            "Add client, service, amount and GST rate.",
            "Copy the invoice text or save the invoice.",
            "Create an account to view saved invoices in the dashboard.",
        ],
    ),
]


VIDEOS = [
    ("make-gst-invoice-in-60-seconds", "Make a GST invoice in 60 seconds", "invoice"),
    ("send-upi-payment-link-to-client", "Send a UPI payment link to a client", "upi"),
    ("whatsapp-reminder-for-late-payment", "WhatsApp reminder for late payment", "whatsapp"),
    ("daily-sales-target-for-small-shop", "Daily sales target for a small shop", "target"),
    ("freelancer-invoice-follow-up", "Freelancer invoice follow-up", "invoice"),
    ("tuition-fee-receipt-in-one-minute", "Tuition fee receipt in one minute", "receipt"),
    ("agency-monthly-retainer-invoice", "Agency monthly retainer invoice", "invoice"),
    ("consultant-payment-reminder", "Consultant payment reminder", "whatsapp"),
    ("gst-18-percent-total-calculation", "GST 18 percent total calculation", "gst"),
    ("payment-link-for-instagram-seller", "Payment link for Instagram seller", "upi"),
    ("hindi-payment-reminder-copy-paste", "Hindi payment reminder copy paste", "whatsapp"),
    ("english-payment-reminder-copy-paste", "English payment reminder copy paste", "whatsapp"),
    ("shop-owner-cash-receipt", "Shop owner cash receipt", "receipt"),
    ("invoice-due-date-planning", "Invoice due date planning", "invoice"),
    ("daily-collection-gap", "Find today's collection gap", "target"),
    ("upi-qr-for-service-business", "UPI QR for service business", "upi"),
    ("save-invoice-details-demo", "Save invoice details demo", "lead"),
    ("request-early-access-demo", "Request early access demo", "lead"),
    ("request-a-new-template", "Request a new template", "feedback"),
    ("rozledger-daily-routine", "RozLedger daily routine", "overview"),
]


def page_copy(title: str, audience: str, category: str) -> dict[str, list[str] | str]:
    intro = {
        "invoice": f"Use this {title} when you need a clean bill, GST amount, total payable, due date and payment message for your customer.",
        "receipt": f"Use this {title} when a customer has paid and you need a simple proof-of-payment message or printable receipt note.",
        "gst": f"Use this {title} to quickly calculate taxable value, GST amount and total payable before sending a payment request.",
        "whatsapp": f"Use this {title} when you want a polite payment follow-up that can be copied directly to WhatsApp.",
        "target": f"Use this {title} to understand how much money you still need to collect today to stay on track.",
    }[category]
    steps = {
        "invoice": ["Enter your business and client name.", "Add the service or product details.", "Choose the GST rate and due date.", "Copy the invoice text or print it."],
        "receipt": ["Enter the payer and payment purpose.", "Add the amount received.", "Mention the payment mode, such as cash, UPI or bank transfer.", "Copy the receipt text for your records."],
        "gst": ["Enter the base amount before tax.", "Select the GST percentage.", "Check GST and final payable amount.", "Copy the result into your invoice or reminder."],
        "whatsapp": ["Enter the payable amount.", "Generate or paste your UPI payment link.", "Choose a polite reminder tone.", "Copy and send the message to the customer."],
        "target": ["Set your monthly collection target.", "Enter working days and today's collection.", "Check today's remaining amount.", "Use the order count to plan follow-ups."],
    }[category]
    template = {
        "invoice": "Invoice from [Business Name]\\nBill to: [Client Name]\\nService: [Service Details]\\nAmount: Rs [Amount]\\nGST: Rs [GST]\\nTotal payable: Rs [Total]\\nDue date: [Date]",
        "receipt": "Receipt from [Business Name]\\nReceived from: [Customer Name]\\nAmount: Rs [Amount]\\nMode: [Cash/UPI/Bank]\\nPurpose: [Reason]\\nDate: [Date]",
        "gst": "Base amount: Rs [Amount]\\nGST rate: [Rate]%\\nGST amount: Rs [GST]\\nTotal amount: Rs [Total]",
        "whatsapp": "Hi [Name], gentle reminder for the pending payment of Rs [Amount]. You can pay using this link: [UPI Link]. Please complete it today if possible. Thank you.",
        "target": "Monthly collection target: Rs [Goal]\\nDaily target: Rs [Daily Target]\\nCollected today: Rs [Collected]\\nLeft today: Rs [Balance]",
    }[category]
    faqs = [
        ("Is this an official tax invoice?", "No. It is a practical template and calculation helper. Add your required business, GST and invoice details, and verify compliance with a qualified professional."),
        ("Can I use it for GST billing?", "You can use it to calculate GST amounts and draft invoice text. Official invoices should include all fields required for your business and tax status."),
        ("Does RozLedger save my data?", "The calculator works in your browser. If you submit an early-access form or save invoice details, the submitted information may be stored so the site owner can follow up or improve the service."),
    ]
    return {"intro": intro, "steps": steps, "template": template, "faqs": faqs, "audience": audience}


def layout(title: str, description: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    {GOOGLE_TAG}
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="description" content="{escape(description)}" />
    <title>{escape(title)} | RozLedger</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="content-page">
    <header class="topbar">
      {BRAND_HTML}
      <nav aria-label="Primary navigation">
        <a href="/">Tool</a>
        <a href="/content/">Templates</a>
        <a href="/blog/">Blog</a>
        <a href="/pricing/">Pricing</a>
        <a href="/#pro">Pro</a>
        <a href="/contact/">Contact</a>
        <a href="/accounts/login/">Login</a>
      </nav>
    </header>
    {body}
    <footer class="site-footer">
      <div class="footer-grid">
        <div class="footer-brand">
          {BRAND_HTML}
          <p>Practical invoice, GST, UPI and payment reminder helpers for Indian small businesses.</p>
          <p class="footer-note">Verify tax and legal details with a qualified professional.</p>
        </div>
        <div>
          <h2>Support</h2>
          <ul>
            <li><a href="mailto:cs@rozledger.in">cs@rozledger.in</a></li>
            <li><a href="tel:+919516022222">+91 95160 22222</a></li>
            <li><a href="https://wa.me/919516022222" rel="noopener">WhatsApp: +91 95160 22222</a></li>
          </ul>
        </div>
        <div>
          <h2>Company</h2>
          <p>
            Klickevents Infosolutions Private Limited<br />
            CC-39/2342, South Janath Road, Palarivattom<br />
            2nd Floor, Thaimuriyil Building<br />
            Ernakulam, Kerala 682025
          </p>
        </div>
        <div>
          <h2>Links</h2>
          <ul>
            <li><a href="/content/">Templates</a></li>
            <li><a href="/blog/">Blog</a></li>
            <li><a href="/pricing/">Pricing</a></li>
            <li><a href="/privacy/">Privacy Policy</a></li>
            <li><a href="/terms/">Terms of Use</a></li>
            <li><a href="/contact/">Contact</a></li>
          </ul>
        </div>
      </div>
      <div class="footer-bottom">
        <span>Owned and operated by Klickevents Infosolutions Private Limited.</span>
        <span>&copy; 2026 RozLedger. All rights reserved.</span>
      </div>
    </footer>
    <a
      class="whatsapp-float"
      href="https://wa.me/919516022222"
      aria-label="Chat with RozLedger on WhatsApp"
      rel="noopener"
    >
      <span class="whatsapp-icon" aria-hidden="true">W</span>
      <span class="whatsapp-text">WhatsApp</span>
    </a>
  </body>
</html>
"""


def render_page(slug: str, title: str, audience: str, category: str) -> str:
    copy = page_copy(title, audience, category)
    steps = "\n".join(f"<li>{escape(step)}</li>" for step in copy["steps"])
    faqs = "\n".join(
        f"<details><summary>{escape(q)}</summary><p>{escape(a)}</p></details>"
        for q, a in copy["faqs"]
    )
    body = f"""
    <main class="article-shell">
      <article class="article">
        <p class="eyebrow">Free India business template</p>
        <h1>{escape(title)}</h1>
        <p class="article-lead">{escape(copy["intro"])}</p>
        <div class="article-actions">
          <a class="button primary" href="/#tool-panel">Open free tool</a>
          <a class="button secondary" href="/pricing/">See Pro pricing</a>
        </div>
        <section>
          <h2>Who this is for</h2>
          <p>This page is built for {escape(audience.lower())} in India who need faster payment paperwork, clearer customer messages and fewer manual calculations.</p>
        </section>
        <section>
          <h2>How to use it</h2>
          <ol>{steps}</ol>
        </section>
        <section>
          <h2>Copy template</h2>
          <pre class="template-box">{escape(copy["template"])}</pre>
        </section>
        <section>
          <h2>Daily workflow</h2>
          <p>Open RozLedger, prepare the invoice or receipt, copy the UPI payment link, send the WhatsApp message, then track how much is left to collect today.</p>
        </section>
        <section class="faq">
          <h2>FAQs</h2>
          {faqs}
        </section>
      </article>
    </main>
"""
    description = f"Free {title.lower()} with copy-ready template, GST/payment workflow and RozLedger daily tool link."
    return layout(title, description, body)


def render_content_index() -> str:
    category_labels = {
        "gst": "GST",
        "whatsapp": "WhatsApp",
        "invoice": "Invoice",
        "receipt": "Receipt",
        "target": "Target",
    }
    items = "\n".join(
        f'<a class="content-link" href="/pages/{slug}/"><span>{escape(category_labels[category])}</span><strong>{escape(title)}</strong></a>'
        for slug, title, _audience, category in PAGES
    )
    body = f"""
    <main class="article-shell">
      <section class="article">
        <p class="eyebrow">RozLedger template library</p>
        <h1>Invoice, GST, Receipt and WhatsApp Templates for India</h1>
        <p class="article-lead">Browse practical invoice, receipt, GST and WhatsApp payment templates for Indian freelancers, tutors, agencies and shop owners.</p>
        <div class="content-grid">{items}</div>
      </section>
    </main>
"""
    return layout("Invoice, GST, Receipt and WhatsApp Templates", "RozLedger template library for Indian small businesses.", body)


BLOG_SECTIONS = {
    "why-small-businesses-need-rozledger": {
        "why": "Small owners often prepare bills, payment notes and reminders while also handling customers. When those steps are scattered across notebooks, calculators and chat apps, mistakes and delays become common.",
        "fit": "This article is useful for freelancers, tutors, agencies, local shops and service businesses that send simple invoices or payment requests regularly.",
        "limits": "RozLedger helps with routine billing communication and payment collection workflow. It is not a replacement for a chartered accountant, GST filing software or legal tax advice.",
        "next": "Try one real invoice in the free tool, then create an account if you want to save invoice history.",
    },
    "rozledger-for-freelancers-india": {
        "why": "Freelancers need invoices that look clear even when billing happens quickly after project delivery or at the end of a monthly retainer.",
        "fit": "Best for designers, developers, writers, consultants and independent professionals who bill clients by project, milestone or monthly service.",
        "limits": "RozLedger can draft the invoice message and calculate totals, but the freelancer must provide correct business details, GST status and payment terms.",
        "next": "Use the invoice tool for your next client bill and save it from the dashboard for future reference.",
    },
    "rozledger-for-tutors-and-coaching-centres": {
        "why": "Fee reminders can become uncomfortable when they are typed differently each time. A clear receipt and polite reminder format keeps parent communication professional.",
        "fit": "Best for tuition teachers, home tutors, coaching centres and small training providers collecting monthly or course-wise fees.",
        "limits": "RozLedger does not manage student attendance or academic records. It focuses on fee receipt text, payment links and reminders.",
        "next": "Create a fee receipt or reminder message, add your UPI details, and send it to the parent or student.",
    },
    "rozledger-for-digital-marketing-agencies": {
        "why": "Agencies often repeat the same billing pattern every month for retainers, ad management, design, content or reporting services.",
        "fit": "Best for small agencies that need quick GST totals, monthly invoice text and follow-up messages for client payments.",
        "limits": "RozLedger does not replace full agency accounting or ad spend reconciliation. It helps with client-facing billing workflow.",
        "next": "Prepare a retainer invoice, save the client, and use payment status to track follow-up.",
    },
    "rozledger-requirements-to-start": {
        "why": "A billing tool is only useful when users know what details to keep ready before they begin.",
        "fit": "Best for first-time users who want a quick checklist before creating invoice text, receipt text or a UPI payment request.",
        "limits": "Different businesses may need additional invoice fields depending on GST registration, state, service type and customer requirements.",
        "next": "Keep business name, customer name, service details, amount, GST rate and UPI ID ready before opening the tool.",
    },
    "rozledger-easy-invoice-workflow": {
        "why": "The fastest billing workflow is one where the amount, tax, payment link and message are prepared together.",
        "fit": "Best for users who want to move from a rough WhatsApp bill to a cleaner invoice and collection routine.",
        "limits": "The tool helps prepare billing material, but users should still verify invoice numbering, GST details and statutory requirements for their business.",
        "next": "Follow the sequence: enter details, check totals, copy text, save invoice, send payment link.",
    },
    "gst-calculation-made-simple-with-rozledger": {
        "why": "Manual GST calculation can lead to avoidable mistakes, especially when bills are prepared quickly during customer conversations.",
        "fit": "Best for users who need quick totals for common GST rates such as 5%, 12%, 18% and 28%.",
        "limits": "RozLedger calculates GST amounts from the numbers entered by the user. It does not decide whether GST applies to a transaction.",
        "next": "Enter the taxable amount and GST percentage, then copy the total into your invoice or payment message.",
    },
    "upi-payment-links-with-rozledger": {
        "why": "A customer is more likely to pay quickly when the amount, payee name and payment note are already clear.",
        "fit": "Best for Indian small businesses that collect through UPI and want a copy-ready link or QR code.",
        "limits": "Users must check their UPI ID carefully. RozLedger cannot recover payments sent to an incorrect UPI ID.",
        "next": "Generate a payment link for a small test amount first, verify it opens correctly, then use it for customer payments.",
    },
    "whatsapp-payment-reminders-with-rozledger": {
        "why": "Payment reminders should be clear without sounding rude. A consistent format helps owners follow up without rewriting messages each time.",
        "fit": "Best for overdue invoices, same-day payment reminders, fee follow-ups and service payment collection.",
        "limits": "RozLedger creates message text. It does not send automated WhatsApp messages or guarantee payment recovery.",
        "next": "Copy the reminder, personalize the customer name if needed, and send it from your WhatsApp account.",
    },
    "daily-collection-targets-with-rozledger": {
        "why": "Many businesses know their monthly target but do not know how much they need to collect today to stay on track.",
        "fit": "Best for owners who collect daily or weekly payments and want a simple view of the collection gap.",
        "limits": "The target tool is an estimate. It does not replace accounting reports or cash-flow planning.",
        "next": "Enter monthly target, working days and collected amount, then use the remaining amount to plan follow-ups.",
    },
    "rozledger-dashboard-benefits": {
        "why": "Saved records become important once a business sends invoices regularly and needs to check what is paid or pending.",
        "fit": "Best for logged-in users who want invoice history, client records, PDF downloads and payment status tracking.",
        "limits": "The dashboard is for simple billing records. It is not a full ERP, inventory system or statutory accounting package.",
        "next": "Create an account, save a test invoice, download the PDF and mark its payment status.",
    },
    "rozledger-pro-benefits": {
        "why": "Some users need more than a free calculator. They need saved clients, repeat invoice history and organized payment follow-up.",
        "fit": "Best for repeat billing users who send multiple invoices every month and want a cleaner workflow.",
        "limits": "Online payment checkout will depend on Razorpay approval and configuration. Until then, Pro access can be handled manually by the site owner.",
        "next": "Review the pricing page and request Pro access if saved billing workflows are useful for your business.",
    },
    "rozledger-for-local-shops": {
        "why": "Local shops and service businesses often collect through cash, UPI and bank transfer, so payment records and receipt messages need to stay clear.",
        "fit": "Best for repair services, small shops, maintenance providers, home service businesses and local vendors.",
        "limits": "RozLedger does not manage stock, barcode billing or POS hardware. It helps with simple bills, receipts and reminders.",
        "next": "Use the receipt template after payment and the payment reminder template when an amount is pending.",
    },
    "rozledger-for-consultants": {
        "why": "Consultants need billing messages that explain the service clearly and keep follow-up professional.",
        "fit": "Best for independent consultants, advisors, trainers and professionals billing for service work.",
        "limits": "RozLedger does not draft contracts or decide tax treatment. It helps with invoice totals, payment messages and saved records.",
        "next": "Create an invoice with service description, GST rate if applicable, due date and payment link.",
    },
    "rozledger-vs-manual-billing": {
        "why": "Manual WhatsApp billing feels fast at first, but repeated typing makes it easy to miss amounts, due dates or payment notes.",
        "fit": "Best for users who currently type every bill manually and want a cleaner repeatable workflow.",
        "limits": "RozLedger still depends on the user entering correct information. It improves structure, not the truth of the underlying data.",
        "next": "Compare your current manual message with the generated RozLedger invoice text and choose the clearer version.",
    },
    "how-rozledger-supports-organic-growth": {
        "why": "Better billing can support business growth because customers receive clear payment instructions and owners spend less time chasing unclear dues.",
        "fit": "Best for businesses that already get customers through referrals, repeat orders or WhatsApp, and now need more organized payment collection.",
        "limits": "RozLedger does not guarantee more customers, revenue or search traffic. It supports the billing and collection side after work is done.",
        "next": "Use RozLedger for invoice text, UPI link and reminder messages on one real customer payment cycle.",
    },
    "rozledger-getting-started-guide": {
        "why": "New users should be able to test the product without learning accounting software first.",
        "fit": "Best for anyone trying RozLedger for the first time with one invoice, receipt or payment reminder.",
        "limits": "The first setup still needs correct business details, customer details, GST rate if applicable and UPI ID.",
        "next": "Open the free tool, create one sample invoice, then register if you want saved invoice history.",
    },
}


def render_blog(slug: str, title: str, description: str, intro: str, points: list[str]) -> str:
    point_items = "\n".join(f"<li>{escape(point)}</li>" for point in points)
    sections = BLOG_SECTIONS[slug]
    body = f"""
    <main class="article-shell">
      <article class="article">
        <p class="eyebrow">RozLedger blog</p>
        <h1>{escape(title)}</h1>
        <p class="article-lead">{escape(intro)}</p>
        <div class="article-actions">
          <a class="button primary" href="/#tool-panel">Use RozLedger free</a>
          <a class="button secondary" href="/pricing/">See Pro pricing</a>
        </div>
        <section>
          <h2>Why this matters</h2>
          <p>{escape(sections["why"])}</p>
        </section>
        <section>
          <h2>How RozLedger helps</h2>
          <ul>{point_items}</ul>
        </section>
        <section>
          <h2>Best fit</h2>
          <p>{escape(sections["fit"])}</p>
        </section>
        <section>
          <h2>Important note</h2>
          <p>{escape(sections["limits"])}</p>
        </section>
        <section>
          <h2>Next step</h2>
          <p>{escape(sections["next"])}</p>
        </section>
      </article>
    </main>
"""
    return layout(title, description, body)


def render_blog_index() -> str:
    items = "\n".join(
        f'<a class="content-link" href="/blog/{slug}/"><span>Blog</span><strong>{escape(title)}</strong><p>{escape(description)}</p></a>'
        for slug, title, description, _intro, _points in BLOGS
    )
    body = f"""
    <main class="article-shell">
      <section class="article">
        <p class="eyebrow">RozLedger blog</p>
        <h1>Guides for easier invoices, payments and daily collections</h1>
        <p class="article-lead">Read practical articles about RozLedger, its benefits, requirements and simple workflows for Indian small businesses.</p>
        <div class="content-grid blog-grid">{items}</div>
      </section>
    </main>
"""
    return layout("RozLedger Blog", "RozLedger guides for invoices, GST, UPI payments, reminders and small business billing workflows.", body)


def render_video(slug: str, title: str, category: str) -> str:
    hindi_hook = {
        "invoice": "Client ko invoice bhejna hai? RozLedger kholo.",
        "upi": "Payment link banana hai? Sirf 60 seconds lagenge.",
        "whatsapp": "Payment follow-up awkward lagta hai? Yeh copy karo.",
        "target": "Aaj kitna collect karna baaki hai? Jaldi check karo.",
        "receipt": "Payment mil gaya? Receipt text turant banao.",
        "lead": "RozLedger me details save ya early access request karna hai?",
        "feedback": "Naya template chahiye? RozLedger ko request bhejo.",
        "overview": "RozLedger ko daily business routine ka part banao.",
        "gst": "GST total manually calculate mat karo.",
    }[category]
    english_hook = {
        "invoice": "Need to send a client invoice? Open RozLedger.",
        "upi": "Need a payment link? Build it in 60 seconds.",
        "whatsapp": "Payment follow-ups feel awkward? Copy this.",
        "target": "Want to know today’s collection gap? Check it fast.",
        "receipt": "Payment received? Generate receipt text quickly.",
        "lead": "Want to save details or request early access?",
        "feedback": "Need a new template? Send a request to RozLedger.",
        "overview": "Make RozLedger part of the daily business routine.",
        "gst": "Stop calculating GST totals manually.",
    }[category]
    return f"""# {title}

## Format

- Length: 20 to 35 seconds
- Aspect ratio: 9:16
- Language: Hindi + English versions
- CTA: Open RozLedger and use the free tool

## Hindi Script

Hook: {hindi_hook}

Scene 1: Show RozLedger homepage on mobile.

Voiceover: "RozLedger me invoice, UPI link aur WhatsApp reminder ek jagah milta hai."

Scene 2: Type a sample amount and GST rate.

Voiceover: "Amount daalo, GST choose karo, total automatically aa jayega."

Scene 3: Copy the generated message.

Voiceover: "Message copy karo aur client ko WhatsApp par bhejo."

CTA: "RozLedger try karo. Link bio me hai."

## English Script

Hook: {english_hook}

Scene 1: Show RozLedger on a phone screen.

Voiceover: "RozLedger gives you invoice text, UPI payment link and payment reminder in one place."

Scene 2: Enter amount and GST rate.

Voiceover: "Add the amount, choose GST and get the final payable total."

Scene 3: Copy the generated message.

Voiceover: "Copy it and send it to your customer on WhatsApp."

CTA: "Try RozLedger. Link in bio."

## Caption

Free daily invoice, UPI and payment reminder tool for Indian small businesses.

## Hashtags

#smallbusinessindia #gstinvoice #upi #freelancerindia #businessowner #rozledger
"""


def main() -> None:
    PAGES_DIR.mkdir(exist_ok=True)
    BLOG_DIR.mkdir(exist_ok=True)
    VIDEOS_DIR.mkdir(exist_ok=True)

    for slug, title, audience, category in PAGES:
        page_dir = PAGES_DIR / slug
        page_dir.mkdir(exist_ok=True)
        (page_dir / "index.html").write_text(render_page(slug, title, audience, category), encoding="utf-8")

    (ROOT / "content.html").write_text(render_content_index(), encoding="utf-8")
    (ROOT / "blog.html").write_text(render_blog_index(), encoding="utf-8")

    for slug, title, description, intro, points in BLOGS:
        blog_dir = BLOG_DIR / slug
        blog_dir.mkdir(exist_ok=True)
        (blog_dir / "index.html").write_text(render_blog(slug, title, description, intro, points), encoding="utf-8")

    sitemap_urls = [f"{BASE_URL}/", f"{BASE_URL}/content/", f"{BASE_URL}/blog/", f"{BASE_URL}/pricing/", f"{BASE_URL}/privacy/", f"{BASE_URL}/terms/", f"{BASE_URL}/contact/"] + [
        f"{BASE_URL}/pages/{slug}/" for slug, *_ in PAGES
    ] + [f"{BASE_URL}/blog/{slug}/" for slug, *_ in BLOGS]
    sitemap = "\n".join(
        f"  <url><loc>{url}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>"
        for url in sitemap_urls
    )
    (ROOT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sitemap}\n</urlset>\n',
        encoding="utf-8",
    )
    (ROOT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n",
        encoding="utf-8",
    )

    for slug, title, category in VIDEOS:
        (VIDEOS_DIR / f"{slug}.md").write_text(render_video(slug, title, category), encoding="utf-8")

    video_index = "\n".join(f"- [{title}](videos/{slug}.md)" for slug, title, _ in VIDEOS)
    (ROOT / "VIDEO_PLAN.md").write_text(
        f"# RozLedger 20 Short Video Plan\n\nThese are ready-to-record scripts for Hindi and English daily-use demos.\n\n{video_index}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
