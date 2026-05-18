# ============================================================
# auto_sync.py — Automatic Ticket Sync Scheduler
# Runs every 6 hours and pulls new tickets from Autotask
# Run: python auto_sync.py
# Keep this running in a separate terminal
# ============================================================

import psycopg2
import psycopg2.extras
import requests
import logging
import time
import schedule
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    AUTOTASK_BASE_URL, AUTOTASK_USERNAME,
    AUTOTASK_SECRET, AUTOTASK_INTEGRATION_CODE,
    PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD, PG_PORT
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "ApiIntegrationCode": AUTOTASK_INTEGRATION_CODE,
    "UserName":           AUTOTASK_USERNAME,
    "Secret":             AUTOTASK_SECRET,
    "Content-Type":       "application/json"
}

PAGE_SIZE  = 500
BATCH_SIZE = 100


# ─── DB Connection ────────────────────────────────────────────
def get_connection():
    return psycopg2.connect(
        host=PG_HOST, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
        port=PG_PORT, sslmode="require",
        keepalives=1, keepalives_idle=30,
        keepalives_interval=10, keepalives_count=5
    )


# ─── Get Last Synced Ticket ID ────────────────────────────────
def get_last_ticket_id():
    """Get highest ticket ID in DB to resume from."""
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(ticket_id), COUNT(*) FROM autotask_tickets")
        row    = cursor.fetchone()
        cursor.close()
        conn.close()
        return row[0] or 0, row[1] or 0
    except Exception as e:
        logger.error(f"DB check error: {e}")
        return 0, 0


# ─── Fetch Page from Autotask ─────────────────────────────────
def fetch_page(page: int, last_id: int = 0):
    """Fetch one page of tickets after last_id."""
    url = f"{AUTOTASK_BASE_URL}/Tickets/query"
    payload = {
        "filter":     [{"op": "gt", "field": "id", "value": last_id}],
        "MaxRecords": PAGE_SIZE,
        "page":       page
    }
    for attempt in range(1, 4):
        try:
            response = requests.post(url, headers=HEADERS, json=payload, timeout=60)
            response.raise_for_status()
            return response.json().get("items", [])
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on page {page}, attempt {attempt}. Retrying...")
            time.sleep(3 * attempt)
        except Exception as e:
            logger.warning(f"Error page {page} attempt {attempt}: {e}")
            time.sleep(2)
    return []


# ─── Store Batch ──────────────────────────────────────────────
def store_batch(batch: list, synced_at) -> int:
    if not batch:
        return 0
    records = [
        (t["ticket_id"], t["title"], t["description"], t["resolution_notes"], synced_at)
        for t in batch
    ]
    for attempt in range(1, 4):
        try:
            conn   = get_connection()
            cursor = conn.cursor()
            psycopg2.extras.execute_values(cursor, """
                INSERT INTO autotask_tickets
                    (ticket_id, title, description, resolution_notes, synced_at)
                VALUES %s
                ON CONFLICT (ticket_id) DO UPDATE SET
                    title            = EXCLUDED.title,
                    description      = EXCLUDED.description,
                    resolution_notes = EXCLUDED.resolution_notes,
                    synced_at        = EXCLUDED.synced_at
            """, records, page_size=100)
            conn.commit()
            cursor.close()
            conn.close()
            return len(records)
        except Exception as e:
            logger.warning(f"Store attempt {attempt} failed: {e}")
            time.sleep(3)
    return 0


# ─── Main Sync Job ────────────────────────────────────────────
def sync_job():
    """
    Main sync function — runs automatically on schedule.
    Fetches ALL tickets after last stored ID.
    """
    logger.info("=" * 55)
    logger.info("🔄 AUTO-SYNC STARTED")
    logger.info(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    start_time    = time.time()
    last_id, count = get_last_ticket_id()
    synced_at     = datetime.utcnow()

    logger.info(f"   Current DB count: {count:,} tickets")
    logger.info(f"   Fetching tickets with ID > {last_id:,}")

    total_fetched = 0
    total_stored  = 0
    page          = 1
    batch         = []

    while True:
        tickets = fetch_page(page, last_id)

        if not tickets:
            logger.info("✅ No more new tickets.")
            break

        for t in tickets:
            batch.append({
                "ticket_id":        t.get("id"),
                "title":            t.get("title", "") or "",
                "description":      t.get("description", "") or "",
                "resolution_notes": t.get("resolution", "") or ""
            })
        total_fetched += len(tickets)

        if len(batch) >= BATCH_SIZE:
            stored = store_batch(batch, synced_at)
            total_stored += stored
            batch = []

        if len(tickets) < PAGE_SIZE:
            break

        page += 1

    # Store remaining
    if batch:
        stored = store_batch(batch, synced_at)
        total_stored += stored

    elapsed = time.time() - start_time
    _, new_count = get_last_ticket_id()

    logger.info(f"✅ SYNC COMPLETE")
    logger.info(f"   New tickets added: {total_stored:,}")
    logger.info(f"   Total in DB now:   {new_count:,}")
    logger.info(f"   Time taken:        {elapsed:.0f}s")
    logger.info("=" * 55)


# ─── Scheduler ────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 UDRSE Auto-Sync Scheduler Started")
    logger.info("   Syncs every 6 hours automatically")
    logger.info("   Press Ctrl+C to stop")
    logger.info("=" * 55)

    # Run immediately on start
    logger.info("⚡ Running initial sync now...")
    sync_job()

    # Then schedule every 6 hours
    schedule.every(6).hours.do(sync_job)

    logger.info("⏰ Next sync in 6 hours. Scheduler is running...")

    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute
