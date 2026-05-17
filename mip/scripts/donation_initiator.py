#!/usr/bin/env python3
"""
Ministry Intelligence Platform (MIP) – Donation Initiator
Initiates mobile money payment requests via MTN MoMo and Airtel Money APIs.

Usage:
    python3 donation_initiator.py --donation-id 123
    python3 donation_initiator.py --donation-id 123 --dry-run

Environment variables required:
    MTN_API_USER, MTN_API_KEY, MTN_SUBSCRIPTION_KEY, MTN_ENVIRONMENT
    AIRTEL_CLIENT_ID, AIRTEL_CLIENT_SECRET, AIRTEL_ENVIRONMENT
    POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
"""

import argparse
import base64
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Optional

import psycopg2
import requests
from dotenv import load_dotenv

# ── Load environment variables ────────────────────────────────────
load_dotenv("/app/.env", override=False)
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"), override=False)

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/donation_initiator.log", mode="a"),
    ],
)
log = logging.getLogger("donation_initiator")

# ── Configuration ─────────────────────────────────────────────────
# MTN MoMo
MTN_API_USER = os.environ.get("MTN_API_USER", "")
MTN_API_KEY = os.environ.get("MTN_API_KEY", "")
MTN_SUBSCRIPTION_KEY = os.environ.get("MTN_SUBSCRIPTION_KEY", "")
MTN_ENVIRONMENT = os.environ.get("MTN_ENVIRONMENT", "sandbox")

MTN_BASE_URL = {
    "sandbox":    "https://sandbox.momodeveloper.mtn.com",
    "production": "https://momodeveloper.mtn.com",
}[MTN_ENVIRONMENT]

# Airtel Money
AIRTEL_CLIENT_ID = os.environ.get("AIRTEL_CLIENT_ID", "")
AIRTEL_CLIENT_SECRET = os.environ.get("AIRTEL_CLIENT_SECRET", "")
AIRTEL_ENVIRONMENT = os.environ.get("AIRTEL_ENVIRONMENT", "sandbox")

AIRTEL_BASE_URL = {
    "sandbox":    "https://openapiuat.airtel.africa",
    "production": "https://openapi.airtel.africa",
}[AIRTEL_ENVIRONMENT]

# PostgreSQL
PG_HOST = os.environ.get("POSTGRES_HOST", "db")
PG_USER = os.environ.get("POSTGRES_USER", "ministry")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "ministry")
PG_DB   = os.environ.get("POSTGRES_DB", "ministry_db")

# Request timeout
HTTP_TIMEOUT = 30


# ── Database helpers ──────────────────────────────────────────────

def get_db_connection() -> psycopg2.extensions.connection:
    """Open a PostgreSQL connection."""
    return psycopg2.connect(
        host=PG_HOST,
        user=PG_USER,
        password=PG_PASS,
        dbname=PG_DB,
        connect_timeout=10,
    )


def fetch_donation(donation_id: int) -> Optional[dict]:
    """Fetch a donation record by ID. Returns None if not found."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT donation_id, member_id, donor_phone, amount, currency,
                       giving_category, payment_method, status, transaction_ref
                FROM donations
                WHERE donation_id = %s
                """,
                (donation_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "donation_id":      row[0],
                "member_id":        row[1],
                "donor_phone":      row[2],
                "amount":           float(row[3]),
                "currency":         row[4],
                "giving_category":  row[5],
                "payment_method":   row[6],
                "status":           row[7],
                "transaction_ref":  row[8],
            }
    finally:
        conn.close()


def update_donation(donation_id: int, status: str, transaction_ref: str) -> None:
    """Update donation status and transaction reference."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE donations
                SET status = %s, transaction_ref = %s, initiated_at = NOW()
                WHERE donation_id = %s
                """,
                (status, transaction_ref, donation_id),
            )
        conn.commit()
        log.info("Updated donation %d: status=%s, ref=%s", donation_id, status, transaction_ref)
    finally:
        conn.close()


# ── MTN MoMo Integration ──────────────────────────────────────────

def mtn_get_access_token() -> str:
    """Obtain an OAuth2 Bearer token from MTN MoMo."""
    # Encode API User and Key as Basic Auth
    credentials = base64.b64encode(
        f"{MTN_API_USER}:{MTN_API_KEY}".encode()
    ).decode()

    response = requests.post(
        f"{MTN_BASE_URL}/collection/token/",
        headers={
            "Authorization": f"Basic {credentials}",
            "Ocp-Apim-Subscription-Key": MTN_SUBSCRIPTION_KEY,
        },
        timeout=HTTP_TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"MTN token request failed: {response.status_code} {response.text}"
        )

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("MTN token response missing 'access_token'.")

    log.info("MTN access token obtained successfully.")
    return token


def mtn_request_to_pay(donation: dict, dry_run: bool = False) -> str:
    """
    Send a Request-to-Pay to MTN MoMo.
    Returns the X-Reference-Id used as the transaction reference.
    """
    if not all([MTN_API_USER, MTN_API_KEY, MTN_SUBSCRIPTION_KEY]):
        raise ValueError(
            "MTN MoMo credentials are not configured. "
            "Set MTN_API_USER, MTN_API_KEY, MTN_SUBSCRIPTION_KEY in .env"
        )

    reference_id = str(uuid.uuid4())
    amount_str = str(int(donation["amount"]))  # MTN requires integer string
    phone = donation["donor_phone"].lstrip("+")  # Remove leading +

    payload = {
        "amount":      amount_str,
        "currency":    donation["currency"],
        "externalId":  str(donation["donation_id"]),
        "payer": {
            "partyIdType": "MSISDN",
            "partyId":     phone,
        },
        "payerMessage": f"MIP Donation – {donation['giving_category']}",
        "payeeNote":    "Thank you for your generous giving. God bless you.",
    }

    log.info(
        "MTN Request-to-Pay: donation_id=%d, phone=%s, amount=%s UGX, ref=%s%s",
        donation["donation_id"], phone, amount_str, reference_id,
        " [DRY RUN]" if dry_run else "",
    )

    if dry_run:
        log.info("[DRY RUN] Would send payload: %s", payload)
        return f"DRY-RUN-{reference_id}"

    token = mtn_get_access_token()

    response = requests.post(
        f"{MTN_BASE_URL}/collection/v1_0/requesttopay",
        json=payload,
        headers={
            "Authorization":              f"Bearer {token}",
            "X-Reference-Id":             reference_id,
            "X-Target-Environment":       MTN_ENVIRONMENT,
            "Ocp-Apim-Subscription-Key":  MTN_SUBSCRIPTION_KEY,
            "Content-Type":               "application/json",
        },
        timeout=HTTP_TIMEOUT,
    )

    if response.status_code == 202:
        log.info(
            "MTN Request-to-Pay accepted (202). Reference ID: %s", reference_id
        )
        return reference_id
    else:
        raise RuntimeError(
            f"MTN Request-to-Pay failed: {response.status_code} {response.text}"
        )


# ── Airtel Money Integration ──────────────────────────────────────

def airtel_get_access_token() -> str:
    """Obtain an OAuth2 Bearer token from Airtel Money."""
    if not all([AIRTEL_CLIENT_ID, AIRTEL_CLIENT_SECRET]):
        raise ValueError(
            "Airtel Money credentials not configured. "
            "Set AIRTEL_CLIENT_ID and AIRTEL_CLIENT_SECRET in .env"
        )

    response = requests.post(
        f"{AIRTEL_BASE_URL}/auth/oauth2/token",
        data={
            "client_id":     AIRTEL_CLIENT_ID,
            "client_secret": AIRTEL_CLIENT_SECRET,
            "grant_type":    "client_credentials",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=HTTP_TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Airtel token request failed: {response.status_code} {response.text}"
        )

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Airtel token response missing 'access_token'.")

    log.info("Airtel access token obtained.")
    return token


def airtel_request_to_pay(donation: dict, dry_run: bool = False) -> str:
    """
    Send a payment request to Airtel Money.
    Returns the transaction reference from Airtel.
    """
    reference_id = str(donation["donation_id"])
    phone = donation["donor_phone"].lstrip("+")

    payload = {
        "reference":   f"MIP-{reference_id}",
        "subscriber": {
            "country":  "UG",
            "currency": donation["currency"],
            "msisdn":   phone,
        },
        "transaction": {
            "amount":   int(donation["amount"]),
            "country":  "UG",
            "currency": donation["currency"],
            "id":       f"MIP-{reference_id}",
        },
    }

    log.info(
        "Airtel Request-to-Pay: donation_id=%d, phone=%s, amount=%s UGX%s",
        donation["donation_id"], phone, donation["amount"],
        " [DRY RUN]" if dry_run else "",
    )

    if dry_run:
        log.info("[DRY RUN] Would send payload: %s", payload)
        return f"AIRTEL-DRY-{reference_id}"

    token = airtel_get_access_token()

    response = requests.post(
        f"{AIRTEL_BASE_URL}/merchant/v1/payments",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Country":     "UG",
            "X-Currency":    "UGX",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        timeout=HTTP_TIMEOUT,
    )

    if response.status_code == 200:
        data = response.json()
        status_code = data.get("status", {}).get("code", "")
        if status_code == "200":
            txn_id = data.get("data", {}).get("transaction", {}).get("id", reference_id)
            log.info("Airtel payment initiated. Transaction ID: %s", txn_id)
            return txn_id
        else:
            raise RuntimeError(
                f"Airtel payment not successful: {data.get('status', {}).get('message', 'Unknown error')}"
            )
    else:
        raise RuntimeError(
            f"Airtel payment request failed: {response.status_code} {response.text}"
        )


# ── Main Logic ────────────────────────────────────────────────────

def initiate_donation(donation_id: int, dry_run: bool = False) -> bool:
    """
    Initiate payment for a specific donation.
    Returns True on success, False on failure.
    """
    # ── Fetch donation record ─────────────────────────────────────
    log.info("Processing donation ID: %d", donation_id)
    donation = fetch_donation(donation_id)

    if not donation:
        log.error("Donation ID %d not found in database.", donation_id)
        return False

    if donation["status"] != "pending":
        log.warning(
            "Donation %d is in status '%s', not 'pending'. Skipping.",
            donation_id, donation["status"]
        )
        return False

    if donation["transaction_ref"]:
        log.warning(
            "Donation %d already has a transaction_ref '%s'. Skipping.",
            donation_id, donation["transaction_ref"]
        )
        return False

    # ── Validate amount ───────────────────────────────────────────
    if donation["amount"] <= 0:
        log.error("Invalid donation amount: %s", donation["amount"])
        update_donation(donation_id, "failed", "ERR-INVALID-AMOUNT")
        return False

    # ── Dispatch by payment method ────────────────────────────────
    try:
        if donation["payment_method"] == "MTN_MoMo":
            transaction_ref = mtn_request_to_pay(donation, dry_run=dry_run)
        elif donation["payment_method"] == "Airtel_Money":
            transaction_ref = airtel_request_to_pay(donation, dry_run=dry_run)
        elif donation["payment_method"] in ("Cash", "Bank_Transfer"):
            # No API call needed; mark as completed immediately for cash/bank
            log.info(
                "Cash/Bank transfer donation %d – marking as completed.",
                donation_id
            )
            transaction_ref = f"MANUAL-{donation_id}-{int(time.time())}"
            if not dry_run:
                update_donation(donation_id, "completed", transaction_ref)
            return True
        else:
            log.error(
                "Unknown payment method '%s' for donation %d.",
                donation["payment_method"], donation_id
            )
            return False

        # ── Update DB with transaction ref (status stays 'pending' for webhook) ──
        if not dry_run:
            update_donation(donation_id, "pending", transaction_ref)

        log.info(
            "Donation %d initiated successfully. Transaction ref: %s",
            donation_id, transaction_ref
        )
        return True

    except (requests.RequestException, RuntimeError, ValueError) as exc:
        log.error(
            "Error initiating donation %d via %s: %s",
            donation_id, donation["payment_method"], exc
        )
        if not dry_run:
            update_donation(donation_id, "failed", f"ERR-{type(exc).__name__}")
        return False


def process_pending_donations(dry_run: bool = False) -> None:
    """Process all donations in 'pending' status without a transaction_ref."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT donation_id FROM donations
                WHERE status = 'pending'
                  AND transaction_ref IS NULL
                  AND payment_method IN ('MTN_MoMo', 'Airtel_Money')
                ORDER BY donation_id
                """
            )
            pending = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    if not pending:
        log.info("No pending donations to process.")
        return

    log.info("Found %d pending donation(s) to process.", len(pending))
    successes = 0
    failures = 0

    for did in pending:
        if initiate_donation(did, dry_run=dry_run):
            successes += 1
        else:
            failures += 1
        time.sleep(1)  # Rate limiting

    log.info(
        "Batch processing complete: %d succeeded, %d failed.", successes, failures
    )


# ── CLI Entry Point ───────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MIP Donation Initiator – initiate mobile money payments"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--donation-id", type=int,
        help="Process a specific donation by ID"
    )
    group.add_argument(
        "--process-all-pending", action="store_true",
        help="Process all pending donations without a transaction ref"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate API calls without sending real requests or updating DB"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.donation_id:
        success = initiate_donation(args.donation_id, dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    elif args.process_all_pending:
        process_pending_donations(dry_run=args.dry_run)
