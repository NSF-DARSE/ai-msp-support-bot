# ============================================================
# config.py — Reads from Azure environment variables
# ============================================================
import os

# --- Autotask API ---
AUTOTASK_BASE_URL         = os.environ.get("AUTOTASK_BASE_URL", "https://webservices2.autotask.net/ATServicesRest/v1.0")
AUTOTASK_USERNAME         = os.environ.get("AUTOTASK_USERNAME", "eluqxadfenbejdb@EntegriaSysSandbox.com")
AUTOTASK_SECRET           = os.environ.get("AUTOTASK_SECRET", "2Ez*$3WpiX@4~g7ZA#c96rC#B")
AUTOTASK_INTEGRATION_CODE = os.environ.get("AUTOTASK_INTEGRATION_CODE", "EXKTEVRFPOWHU42C5NP7A3GFGIG")

# --- Azure PostgreSQL ---
PG_HOST     = os.environ.get("PG_HOST",     "udrsechatbotdb.postgres.database.azure.com")
PG_DATABASE = os.environ.get("PG_DATABASE", "postgres")
PG_USER     = os.environ.get("PG_USER",     "dbadmin")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "FhDmbGChY5klxMc")
PG_PORT     = int(os.environ.get("PG_PORT", "5432"))

# --- OpenAI ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL",   "gpt-4o")

# --- RAG Settings ---
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.70"))
TOP_K_RESULTS        = 5

# --- Azure Bot ---
AZURE_BOT_APP_ID     = os.environ.get("AZURE_BOT_APP_ID",     "cc2380e5-8e27-4c93-99ab-c18bb5b14d4f")
AZURE_BOT_APP_SECRET = os.environ.get("AZURE_BOT_APP_SECRET", "")
AZURE_BOT_TENANT_ID  = os.environ.get("AZURE_BOT_TENANT_ID",  "417b5070-1e2f-47be-b6fa-bf6392bf9666")