from __future__ import annotations

import os
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "pages"
VIDEOS_DIR = ROOT / "videos"
BASE_URL = os.getenv("ROZLEDGER_PUBLIC_URL", "https://rozledger.in").rstrip("/")


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
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="description" content="{escape(description)}" />
    <title>{escape(title)} | RozLedger</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="content-page">
    <header class="topbar">
      <a class="brand" href="/"><span class="brand-mark">R</span><span>RozLedger</span></a>
      <nav aria-label="Primary navigation">
        <a href="/">Tool</a>
        <a href="/content/">Templates</a>
        <a href="/#pro">Pro</a>
      </nav>
    </header>
    {body}
    <footer>
      <p>
        RozLedger templates are practical business helpers. Verify tax and legal details with a professional.
        <a href="/privacy/">Privacy</a> <a href="/terms/">Terms</a> <a href="/contact/">Contact</a>
      </p>
    </footer>
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
          <a class="button secondary" href="/#pro">Request early access</a>
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
    VIDEOS_DIR.mkdir(exist_ok=True)

    for slug, title, audience, category in PAGES:
        page_dir = PAGES_DIR / slug
        page_dir.mkdir(exist_ok=True)
        (page_dir / "index.html").write_text(render_page(slug, title, audience, category), encoding="utf-8")

    (ROOT / "content.html").write_text(render_content_index(), encoding="utf-8")

    sitemap_urls = [f"{BASE_URL}/", f"{BASE_URL}/content/", f"{BASE_URL}/privacy/", f"{BASE_URL}/terms/", f"{BASE_URL}/contact/"] + [
        f"{BASE_URL}/pages/{slug}/" for slug, *_ in PAGES
    ]
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
