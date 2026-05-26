from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "rozledger.db"

app = Flask(__name__, static_folder=str(ROOT_DIR), static_url_path="")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                business_type TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_name TEXT NOT NULL,
                client_name TEXT NOT NULL,
                service_name TEXT NOT NULL,
                amount_before_gst REAL NOT NULL,
                gst_rate REAL NOT NULL,
                due_days INTEGER NOT NULL,
                total_text TEXT NOT NULL,
                upi_link TEXT NOT NULL,
                invoice_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS affiliate_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_name TEXT NOT NULL,
                destination_url TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value).strip()[:2000]


@app.get("/")
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "database": str(DB_PATH)})


@app.get("/api/options")
def options():
    return jsonify(
        {
            "frontend": [
                {
                    "name": "Static HTML MVP",
                    "cost": "Lowest",
                    "best_for": "Launch in 1 day, SEO pages, affiliate links",
                },
                {
                    "name": "React or Next.js",
                    "cost": "Medium",
                    "best_for": "User accounts, dashboard, subscriptions, richer app UI",
                },
                {
                    "name": "WordPress plus tool page",
                    "cost": "Low",
                    "best_for": "Fast blogging and SEO if you prefer admin editing",
                },
            ],
            "backend": [
                {
                    "name": "Flask plus SQLite",
                    "cost": "Lowest",
                    "best_for": "Local MVP, leads, invoice history, simple admin exports",
                },
                {
                    "name": "Supabase",
                    "cost": "Low",
                    "best_for": "Hosted database, auth, storage, fast SaaS prototype",
                },
                {
                    "name": "Django",
                    "cost": "Medium",
                    "best_for": "Admin panel, billing workflows, larger business app",
                },
            ],
        }
    )


@app.post("/api/leads")
def create_lead():
    payload = request.get_json(silent=True) or {}
    name = clean_text(payload.get("name"))
    phone = clean_text(payload.get("phone"))
    business_type = clean_text(payload.get("business_type"), "Unknown")
    source = clean_text(payload.get("source"), "website")

    if len(name) < 2 or len(phone) < 8:
        return jsonify({"error": "Name and phone are required."}), 400

    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO leads (name, phone, business_type, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, phone, business_type, source, now_iso()),
        )

    return jsonify({"ok": True, "id": cursor.lastrowid}), 201


@app.post("/api/invoices")
def create_invoice():
    payload = request.get_json(silent=True) or {}
    business_name = clean_text(payload.get("business_name"), "Your business")
    client_name = clean_text(payload.get("client_name"), "Client")
    service_name = clean_text(payload.get("service_name"), "Service")
    amount_before_gst = float(payload.get("amount_before_gst") or 0)
    gst_rate = float(payload.get("gst_rate") or 0)
    due_days = int(payload.get("due_days") or 0)
    total_text = clean_text(payload.get("total_text"))
    upi_link = clean_text(payload.get("upi_link"))
    invoice_text = clean_text(payload.get("invoice_text"))

    if amount_before_gst <= 0:
        return jsonify({"error": "Invoice amount must be greater than zero."}), 400

    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO invoices (
                business_name,
                client_name,
                service_name,
                amount_before_gst,
                gst_rate,
                due_days,
                total_text,
                upi_link,
                invoice_text,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                business_name,
                client_name,
                service_name,
                amount_before_gst,
                gst_rate,
                due_days,
                total_text,
                upi_link,
                invoice_text,
                now_iso(),
            ),
        )

    return jsonify({"ok": True, "id": cursor.lastrowid}), 201


@app.post("/api/affiliate-clicks")
def affiliate_click():
    payload = request.get_json(silent=True) or {}
    offer_name = clean_text(payload.get("offer_name"), "unknown")
    destination_url = clean_text(payload.get("destination_url"), "#")

    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO affiliate_clicks (offer_name, destination_url, created_at)
            VALUES (?, ?, ?)
            """,
            (offer_name, destination_url, now_iso()),
        )

    return jsonify({"ok": True, "id": cursor.lastrowid}), 201


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=8000, debug=True)
