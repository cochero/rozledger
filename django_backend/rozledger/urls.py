from django.contrib import admin
from django.urls import path

from core import views


urlpatterns = [
    path("", views.index, name="index"),
    path("app.js", views.asset, {"filename": "app.js"}, name="app_js"),
    path("styles.css", views.asset, {"filename": "styles.css"}, name="styles_css"),
    path("robots.txt", views.asset, {"filename": "robots.txt"}, name="robots_txt"),
    path("sitemap.xml", views.asset, {"filename": "sitemap.xml"}, name="sitemap_xml"),
    path("content/", views.content_index, name="content_index"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("contact/", views.contact, name="contact"),
    path("pages/<slug:slug>/", views.seo_page, name="seo_page"),
    path("invoice/<str:token>/", views.invoice_print, name="invoice_print"),
    path("pro/thanks/<str:token>/", views.lead_thanks, name="lead_thanks"),
    path("accounts/register/", views.register_view, name="register"),
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("api/health", views.health, name="health"),
    path("api/options", views.options, name="options"),
    path("api/leads", views.create_lead, name="create_lead"),
    path("api/invoices", views.create_invoice, name="create_invoice"),
    path("api/affiliate-clicks", views.affiliate_click, name="affiliate_click"),
]
