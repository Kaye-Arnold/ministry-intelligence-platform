#!/usr/bin/env python3
"""
Ministry Intelligence Platform (MIP) – Webhook Listener
Flask application handling:
  - WhatsApp Cloud API webhook (verification + incoming messages)
  - MTN MoMo and Airtel Money payment callbacks
  - Report trigger detection and PDF distribution

Runs on port 5000 inside the automation container.

Environment variables:
    WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN,
    WHATSAPP_GROUP_ID, PAYMENT_WEBHOOK_SECRET,
    POSTGRES_HOST/USER/PASSWORD/DB, DOMAIN
"""

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import requests
from flask import Flask, jsonify, request

from dotenv import load_dotenv

# ── Load environment ──────────────────────────────────────────────
load_dotenv("/app/.env", override=False)
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"), override=False)

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/webhook_listener.log", mode="a"),
    ],
)
log = logging.getLogger("webhook_listener")

# ── Configuration ─────────────────────────────────────────────────
WHATSAPP_TOKEN           = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN    = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_GROUP_ID        = os.environ.get("WHATSAPP_GROUP_ID", "")
PAYMENT_WEBHOOK_SECRET   = os.environ.get("PAYMENT_WEBHOOK_SECRET", "").encode()
DOMAIN                   = os.environ.get("DOMAIN", "localhost")

# PostgreSQL
PG_HOST = os.environ.get("POSTGRES_HOST", "db")
PG_USER = os.environ.get("POSTGRES_USER", "ministry")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "ministry")
PG_DB   = os.environ.get("POSTGRES_DB", "ministry_db")

WHATSAPP_API_BASE = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}"
REPORT_DIR = "/app/reports"

app = Flask(__name__)


# ── Database helper ───────────────────────────────────────────────

def get_db() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=PG_HOST, user=PG_USER,
        password=PG_PASS, dbname=PG_DB,
        connect_timeout=10,
    )


def db_execute(query: str, params: tuple = ()) -> None:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def db_fetchone(query: str, params: tuple = ()) -> Optional[tuple]:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()
    finally:
        conn.close()


# ── HMAC Verification ─────────────────────────────────────────────

def verify_hmac_signature(payload: bytes, signature_header: str) -> bool:
    """
    Verify HMAC-SHA256 signature from MTN / Airtel payment callbacks.
    Signature header format: 'sha256=<hex_digest>' or just '<hex_digest>'.
    """
    if not PAYMENT_WEBHOOK_SECRET:
        log.warning("PAYMENT_WEBHOOK_SECRET not set – skipping HMAC verification.")
        return True  # Fail open in development; set secret in production

    expected = hmac.new(PAYMENT_WEBHOOK_SECRET, payload, hashlib.sha256).hexdigest()

    if not signature_header:
        log.warning("No signature header provided in payment callback.")
        return False

    # Strip 'sha256=' prefix if present
    received = signature_header.replace("sha256=", "").strip()

    is_valid = hmac.compare_digest(expected, received)
    if not is_valid:
        log.warning("HMAC signature mismatch. Expected: %s, Received: %s", expected, received)
    return is_valid


# ── WhatsApp Cloud API helpers ────────────────────────────────────

def send_whatsapp_message(to: str, text: str) -> Optional[str]:
    """Send a plain text WhatsApp message; return message ID or None."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        log.error("WhatsApp credentials not configured.")
        return None

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    try:
        r = requests.post(
            f"{WHATSAPP_API_BASE}/messages",
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if r.status_code == 200:
            msg_id = r.json().get("messages", [{}])[0].get("id")
            log.info("WhatsApp message sent to %s (id=%s)", to, msg_id)
            return msg_id
        else:
            log.error("WhatsApp send failed: %s %s", r.status_code, r.text)
            return None
    except requests.RequestException as exc:
        log.error("WhatsApp send error: %s", exc)
        return None


def send_whatsapp_document(to: str, doc_url: str, filename: str, caption: str = "") -> Optional[str]:
    """Send a document (PDF) via WhatsApp; return message ID or None."""
    if not WHATSAPP_TOKEN:
        log.error("WhatsApp token not configured.")
        return None

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "document",
        "document": {
            "link": doc_url,
            "filename": filename,
            "caption": caption,
        },
    }

    try:
        r = requests.post(
            f"{WHATSAPP_API_BASE}/messages",
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if r.status_code == 200:
            msg_id = r.json().get("messages", [{}])[0].get("id")
            log.info("Document sent to %s: %s (id=%s)", to, filename, msg_id)
            return msg_id
        else:
            log.error("Document send failed: %s %s", r.status_code, r.text)
            return None
    except requests.RequestException as exc:
        log.error("Document send error: %s", exc)
        return None


def trigger_report_generation_and_send(group_id: str, report_type: str = "weekly") -> None:
    """
    Background thread: generate PDF report and send to WhatsApp group.
    Called after service window is opened by a clerk trigger.
    """
    try:
        log.info("Starting %s report generation and WhatsApp delivery...", report_type)

        # Generate the report
        report_script = os.path.join(os.path.dirname(__file__), "generate_report.py")
        output_filename = f"{report_type}_report_{datetime.now().strftime('%Y%m%d')}.pdf"
        output_path = os.path.join(REPORT_DIR, output_filename)

        result = subprocess.run(
            [sys.executable, report_script, "--type", report_type, "--output", output_path],
            capture_output=True, text=True, timeout=120,
        )

        if result.returncode != 0:
            log.error("Report generation failed:\nSTDOUT: %s\nSTDERR: %s",
                      result.stdout, result.stderr)
            send_whatsapp_message(
                group_id,
                "⚠️ Report generation encountered an error. Please contact the system administrator."
            )
            return

        log.info("Report generated: %s", output_path)

        # Build public URL (served by Nginx from reports volume)
        public_url = f"https://{DOMAIN}/reports/{output_filename}"

        # Send to WhatsApp group
        month_year = datetime.now().strftime("%B %Y")
        caption = (
            f"📊 *MIP {report_type.title()} Report – {month_year}*\n"
            f"Generated: {datetime.now().strftime('%d %b %Y, %H:%M EAT')}\n"
            f"System: Ministry Intelligence Platform v1.0"
        )

        msg_id = send_whatsapp_document(
            to=group_id,
            doc_url=public_url,
            filename=output_filename,
            caption=caption,
        )

        # Log to report_log table
        if msg_id:
            db_execute(
                """
                INSERT INTO report_log
                    (report_type, generated_at, sent_at, status, file_path, whatsapp_msg_id)
                VALUES (%s, NOW(), NOW(), 'sent', %s, %s)
                """,
                (report_type, output_path, msg_id),
            )
            log.info("Report successfully sent. WhatsApp message ID: %s", msg_id)
        else:
            db_execute(
                """
                INSERT INTO report_log (report_type, generated_at, status, file_path)
                VALUES (%s, NOW(), 'send_failed', %s)
                """,
                (report_type, output_path),
            )

    except subprocess.TimeoutExpired:
        log.error("Report generation timed out after 120 seconds.")
    except Exception as exc:
        log.exception("Unexpected error in report generation thread: %s", exc)


# ── Flask Routes ──────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health_check():
    """Simple health endpoint for Nginx and Docker healthchecks."""
    return jsonify({"status": "ok", "service": "MIP webhook listener"}), 200


# ── WhatsApp Webhook ──────────────────────────────────────────────

@app.route("/webhook/whatsapp", methods=["GET"])
def whatsapp_verify():
    """
    Meta webhook verification endpoint.
    Meta sends a GET request with hub.mode, hub.challenge, hub.verify_token.
    We must return hub.challenge if the verify token matches.
    """
    mode = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    verify_token = request.args.get("hub.verify_token")

    if mode == "subscribe" and verify_token == WHATSAPP_VERIFY_TOKEN:
        log.info("WhatsApp webhook verified successfully.")
        return challenge, 200

    log.warning(
        "WhatsApp verification failed. mode=%s, token_match=%s",
        mode, verify_token == WHATSAPP_VERIFY_TOKEN
    )
    return jsonify({"error": "Verification failed"}), 403


@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_incoming():
    """
    Receive incoming WhatsApp messages.
    Detects:
      1. Clerk triggers report distribution (message containing "SEND REPORT" or "YES")
      2. Any message that opens/extends the 24-hour free service window.
    """
    data = request.get_json(silent=True) or {}
    log.debug("WhatsApp incoming: %s", json.dumps(data))

    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        for message in messages:
            msg_type = message.get("type", "")
            from_number = message.get("from", "")
            msg_id = message.get("id", "")

            # Only process text messages
            if msg_type != "text":
                continue

            text_body = message.get("text", {}).get("body", "").strip()
            log.info(
                "Incoming WhatsApp from %s (id=%s): %s",
                from_number, msg_id, text_body[:100]
            )

            # ── Detect report trigger ─────────────────────────────
            # Clerk sends "SEND REPORT", "SEND WEEKLY REPORT", etc.
            # Any group member reply of "YES" also triggers if a report is ready.
            trigger_patterns = [
                r"\bSEND\s+REPORT\b",
                r"\bSEND\s+WEEKLY\b",
                r"\bSEND\s+MONTHLY\b",
                r"\bSEND\s+SEMESTER\b",
            ]

            report_type = "weekly"  # default
            triggered = False

            for pattern in trigger_patterns:
                if re.search(pattern, text_body, re.IGNORECASE):
                    triggered = True
                    if "MONTHLY" in text_body.upper():
                        report_type = "monthly"
                    elif "SEMESTER" in text_body.upper():
                        report_type = "semester"
                    break

            # Also trigger on "YES" reply (clerk pre-sends trigger; member replies YES)
            if not triggered and text_body.strip().upper() in ("YES", "Y"):
                # Check if there's a pending report trigger in the last 10 minutes
                recent_trigger = db_fetchone(
                    """
                    SELECT id FROM report_log
                    WHERE status = 'triggered'
                      AND generated_at > NOW() - INTERVAL '10 minutes'
                    ORDER BY generated_at DESC LIMIT 1
                    """
                )
                if recent_trigger:
                    triggered = True
                    log.info("YES reply received within trigger window – sending report.")

            if triggered:
                # Record trigger
                try:
                    db_execute(
                        """
                        INSERT INTO report_log (report_type, generated_at, status)
                        VALUES (%s, NOW(), 'triggered')
                        """,
                        (report_type,),
                    )
                except Exception as e:
                    log.warning("Could not record trigger: %s", e)

                # Launch report generation in background thread
                target_group = WHATSAPP_GROUP_ID or from_number
                thread = threading.Thread(
                    target=trigger_report_generation_and_send,
                    args=(target_group, report_type),
                    daemon=True,
                )
                thread.start()

                log.info(
                    "Report trigger received from %s. Generating %s report...",
                    from_number, report_type
                )

    except (KeyError, IndexError, TypeError) as exc:
        log.error("Error processing WhatsApp message: %s", exc)

    # Always return 200 to Meta (prevents retries)
    return jsonify({"status": "received"}), 200


# ── Payment Webhook (MTN MoMo & Airtel Money) ─────────────────────

@app.route("/webhook/payment", methods=["POST"])
def payment_callback():
    """
    Receive payment confirmation callbacks from MTN MoMo and Airtel Money.
    Verifies HMAC signature, then updates donation status.
    """
    raw_payload = request.get_data()
    data = request.get_json(silent=True) or {}

    # ── Signature verification ────────────────────────────────────
    # MTN uses X-Signature header; Airtel uses X-Airtel-Signature
    sig_header = (
        request.headers.get("X-Signature")
        or request.headers.get("X-Airtel-Signature")
        or ""
    )

    if not verify_hmac_signature(raw_payload, sig_header):
        log.warning("Payment callback rejected: invalid HMAC signature.")
        return jsonify({"error": "Invalid signature"}), 401

    log.info("Payment callback received: %s", json.dumps(data))

    # ── Detect provider and extract fields ────────────────────────
    # MTN MoMo callback format:
    # { "financialTransactionId": "...", "externalId": "...", "status": "SUCCESSFUL" }
    #
    # Airtel Money callback format:
    # { "transaction": { "id": "...", "status": "TS", "message": "..." } }

    try:
        if "financialTransactionId" in data:
            # MTN MoMo
            external_id = data.get("externalId", "")
            txn_ref = data.get("financialTransactionId", "")
            status_raw = data.get("status", "").upper()
            is_success = status_raw in ("SUCCESSFUL", "SUCCESS")
            new_status = "completed" if is_success else "failed"

        elif "transaction" in data:
            # Airtel Money
            txn = data.get("transaction", {})
            external_id = txn.get("id", "").replace("MIP-", "")
            txn_ref = txn.get("id", "")
            status_code = txn.get("status", "")
            # Airtel: TS = Transaction Successful, TF = Transaction Failed
            is_success = status_code in ("TS", "200", "SUCCESS")
            new_status = "completed" if is_success else "failed"

        else:
            log.warning("Unknown payment callback format: %s", data)
            return jsonify({"status": "unknown format"}), 200

        if not external_id:
            log.error("Payment callback missing external_id: %s", data)
            return jsonify({"error": "Missing external_id"}), 400

        # ── Update donation record ────────────────────────────────
        donation_id = int(external_id)
        db_execute(
            """
            UPDATE donations
            SET status = %s,
                transaction_ref = %s,
                confirmed_at = CASE WHEN %s = 'completed' THEN NOW() ELSE confirmed_at END
            WHERE donation_id = %s
              AND status IN ('pending', 'failed')
            """,
            (new_status, txn_ref, new_status, donation_id),
        )

        log.info(
            "Donation %d updated: status=%s, txn_ref=%s",
            donation_id, new_status, txn_ref
        )
        return jsonify({"status": "processed", "donation_id": donation_id}), 200

    except (ValueError, KeyError, TypeError) as exc:
        log.error("Error processing payment callback: %s – Data: %s", exc, data)
        return jsonify({"error": "Processing failed"}), 500


# ── Donation Trigger Endpoint (called by NocoDB webhook) ──────────

@app.route("/webhook/donation-submitted", methods=["POST"])
def donation_submitted():
    """
    Called when a donation is submitted via NocoDB form.
    Triggers donation_initiator.py to start the mobile money flow.
    """
    data = request.get_json(silent=True) or {}
    donation_id = data.get("donation_id")

    if not donation_id:
        return jsonify({"error": "Missing donation_id"}), 400

    try:
        donation_id = int(donation_id)
    except ValueError:
        return jsonify({"error": "Invalid donation_id"}), 400

    log.info("Donation submission webhook received for ID: %d", donation_id)

    def initiate_in_background(did: int) -> None:
        time.sleep(2)  # Brief delay to ensure DB record is committed
        initiator = os.path.join(os.path.dirname(__file__), "donation_initiator.py")
        result = subprocess.run(
            [sys.executable, initiator, "--donation-id", str(did)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.error(
                "donation_initiator.py failed for ID %d:\n%s",
                did, result.stderr
            )
        else:
            log.info("donation_initiator.py succeeded for ID %d.", did)

    thread = threading.Thread(
        target=initiate_in_background, args=(donation_id,), daemon=True
    )
    thread.start()

    return jsonify({"status": "queued", "donation_id": donation_id}), 202


# ── Application Entry Point ───────────────────────────────────────

if __name__ == "__main__":
    # Ensure reports directory exists
    os.makedirs(REPORT_DIR, exist_ok=True)

    log.info("=" * 60)
    log.info("MIP Webhook Listener starting on port 5000")
    log.info("Domain: %s", DOMAIN)
    log.info("WhatsApp Phone Number ID: %s", WHATSAPP_PHONE_NUMBER_ID or "NOT SET")
    log.info("Payment HMAC: %s", "configured" if PAYMENT_WEBHOOK_SECRET else "NOT SET")
    log.info("=" * 60)

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True,
    )
