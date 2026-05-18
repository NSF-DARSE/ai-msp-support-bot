# ============================================================
# autotask_sync.py — FULL SYNC WITH RESUME
# Resumes from where it left off if connection drops
# Run: python autotask_sync.py
# ============================================================

import requests
import psycopg2
import psycopg2.extras
import logging
import time
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

PAGE_SIZE   = 500
BATCH_SIZE  = 500
MAX_RETRIES = 5


# ─── DB Connection with Keepalive ─────────────────────────────
def get_connection():
    return psycopg2.connect(
        host=PG_HOST, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
        port=PG_PORT, sslmode="require",
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5
    )


# ─── Get Last Stored Ticket ID ────────────────────────────────
def get_last_ticket_id():
    """Get the highest ticket_id in DB so we can resume."""
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(ticket_id), COUNT(*) FROM autotask_tickets")
        row    = cursor.fetchone()
        cursor.close()
        conn.close()
        max_id = row[0] or 0
        count  = row[1] or 0
        return max_id, count
    except Exception as e:
        logger.error(f"Could not check DB: {e}")
        return 0, 0


# ─── Fetch One Page ───────────────────────────────────────────
def fetch_page(page: int, last_id: int = 0):
    """Fetch a single page. If resuming, filter by ticket_id > last_id."""
    url = f"{AUTOTASK_BASE_URL}/Tickets/query"

    # If resuming, only fetch tickets after last stored ID
    if last_id > 0:
        filter_val = [{"op": "gt", "field": "id", "value": last_id}]
    else:
        filter_val = [{"op": "gte", "field": "id", "value": 0}]

    payload = {
        "filter":     filter_val,
        "MaxRecords": PAGE_SIZE,
        "page":       page
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(url, headers=HEADERS, json=payload, timeout=60)
            response.raise_for_status()
            return response.json().get("items", [])

        except requests.exceptions.Timeout:
            logger.warning(f"⏳ Page {page} timed out (attempt {attempt}/{MAX_RETRIES}). Retrying in {3*attempt}s...")
            time.sleep(3 * attempt)
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ HTTP error on page {page}: {e}")
            return []
        except Exception as e:
            logger.warning(f"⚠️ Error on page {page} attempt {attempt}: {e}")
            time.sleep(2)

    logger.error(f"❌ Failed page {page} after {MAX_RETRIES} attempts. Skipping.")
    return []


# ─── Store Batch ──────────────────────────────────────────────
def store_batch(batch: list, synced_at) -> int:
    """Store batch with fresh connection each time to avoid timeouts."""
    if not batch:
        return 0

    records = [(t["ticket_id"], t["title"], t["description"], t["resolution_notes"], synced_at) for t in batch]

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
            """, records, page_size=500)
            conn.commit()
            cursor.close()
            conn.close()
            return len(records)
        except Exception as e:
            logger.warning(f"⚠️ Store attempt {attempt} failed: {e}. Retrying...")
            time.sleep(3 * attempt)

    logger.error("❌ Failed to store batch after 3 attempts.")
    return 0


# ─── Display Summary ──────────────────────────────────────────
def display_summary():
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), MAX(ticket_id), MIN(ticket_id) FROM autotask_tickets")
    count, max_id, min_id = cursor.fetchone()
    cursor.execute("SELECT ticket_id, title, resolution_notes FROM autotask_tickets ORDER BY ticket_id DESC LIMIT 3")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    print(f"\n{'='*70}")
    print(f"  TOTAL TICKETS IN DATABASE: {count:,}")
    print(f"  Ticket ID range: {min_id:,} → {max_id:,}")
    print(f"{'='*70}")
    print("  Latest 3 tickets:")
    for row in rows:
        print(f"  #{row[0]} | {str(row[1] or '')[:40]} | {str(row[2] or '')[:30]}")
    print(f"{'='*70}\n")


# ─── Main ─────────────────────────────────────────────────────
def run_full_sync():
    # Check if resuming
    last_id, current_count = get_last_ticket_id()

    if last_id > 0:
        logger.info(f"🔄 RESUMING sync — {current_count:,} tickets already in DB")
        logger.info(f"   Fetching tickets with ID > {last_id:,}")
    else:
        logger.info("🚀 Starting FULL sync from scratch...")

    start_time    = time.time()
    synced_at     = datetime.utcnow()
    total_fetched = 0
    total_stored  = 0
    page          = 1
    batch         = []

    while True:
        tickets = fetch_page(page, last_id)

        if not tickets:
            logger.info(f"✅ No more tickets at page {page}. Done!")
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
            elapsed = time.time() - start_time
            rate    = total_stored / elapsed if elapsed > 0 else 0
            logger.info(
                f"  Page {page} | New: {total_stored:,} | "
                f"Total in DB: {current_count + total_stored:,} | "
                f"Speed: {rate:.0f}/sec | Elapsed: {elapsed:.0f}s"
            )

        if len(tickets) < PAGE_SIZE:
            logger.info("✅ Last page reached.")
            break

        page += 1

    # Store remaining
    if batch:
        stored = store_batch(batch, synced_at)
        total_stored += stored

    elapsed = time.time() - start_time
    logger.info(f"\n{'='*60}")
    logger.info(f"  SYNC COMPLETE!")
    logger.info(f"  New tickets added: {total_stored:,}")
    logger.info(f"  Time taken: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    logger.info(f"{'='*60}")

    display_summary()


if __name__ == "__main__":
    run_full_sync()