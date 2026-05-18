# ============================================================
# database.py
# Azure PostgreSQL connection + table setup
# Uses pg8000 (pure Python — works on all platforms including Azure)
# ============================================================

import pg8000
import ssl
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Load Config ──────────────────────────────────────────────
try:
    from config import PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD, PG_PORT
except:
    PG_HOST     = os.environ.get("PG_HOST",     "udrsechatbotdb.postgres.database.azure.com")
    PG_DATABASE = os.environ.get("PG_DATABASE", "postgres")
    PG_USER     = os.environ.get("PG_USER",     "dbadmin")
    PG_PASSWORD = os.environ.get("PG_PASSWORD", "FhDmbGChY5klxMc")
    PG_PORT     = int(os.environ.get("PG_PORT", "5432"))


# ─── Connection ───────────────────────────────────────────────
def get_connection():
    """Returns an Azure PostgreSQL connection using pg8000."""
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode    = ssl.CERT_NONE
    return pg8000.connect(
        host=PG_HOST,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
        port=PG_PORT,
        ssl_context=ssl_context
    )


# ─── Setup Tables ─────────────────────────────────────────────
def setup_database():
    """Creates all required tables with full-text search."""
    conn   = get_connection()
    cursor = conn.cursor()

    # Main tickets table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS autotask_tickets (
            id               SERIAL PRIMARY KEY,
            ticket_id        BIGINT UNIQUE NOT NULL,
            title            TEXT,
            description      TEXT,
            resolution_notes TEXT,
            search_vector    TSVECTOR,
            synced_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Full-text search index
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS tickets_search_idx
        ON autotask_tickets USING GIN(search_vector);
    """)

    # Auto-update trigger for search vector
    cursor.execute("""
        CREATE OR REPLACE FUNCTION update_search_vector()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(NEW.resolution_notes, '')), 'C');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    cursor.execute("""
        DROP TRIGGER IF EXISTS tickets_search_trigger ON autotask_tickets;
        CREATE TRIGGER tickets_search_trigger
        BEFORE INSERT OR UPDATE ON autotask_tickets
        FOR EACH ROW EXECUTE FUNCTION update_search_vector();
    """)

    # Chat history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id         SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            role       TEXT NOT NULL,
            message    TEXT NOT NULL,
            source     TEXT,
            confidence FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()
    logger.info("✅ Database tables ready (full-text search enabled).")


# ─── Health Check ─────────────────────────────────────────────
def check_connection():
    """Tests the database connection. Returns True if OK."""
    try:
        conn = get_connection()
        conn.close()
        logger.info("✅ Azure PostgreSQL connection successful.")
        return True
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return False


if __name__ == "__main__":
    if check_connection():
        setup_database()
        print("\n✅ Database setup complete!")
        print("▶️  Next step: python autotask_sync.py\n")