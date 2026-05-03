# ============================================================
# function_app.py — Azure Function App Entry Point
# MSP Support Bot — UDRSE / NSF-DARSE
#
# This module exposes all HTTP endpoints for the chatbot:
#   POST /api/chat         — main RAG chat endpoint
#   POST /api/chatopenai   — user-triggered OpenAI fallback
#   GET  /api/tickets      — list/search Autotask tickets
#   GET  /api/stats        — ticket database statistics
#   GET  /api/health       — database health check
#   POST /api/messages     — Azure Bot Service (Teams) endpoint
# ============================================================

import azure.functions as func
import json
import logging
import uuid
import os
import pg8000
import ssl
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ─── Configuration (loaded from Azure App Settings) ───────────
# Credentials are stored as environment variables, never hardcoded.
PG_HOST            = os.environ.get("PG_HOST",            "")
PG_DATABASE        = os.environ.get("PG_DATABASE",        "")
PG_USER            = os.environ.get("PG_USER",            "")
PG_PASSWORD        = os.environ.get("PG_PASSWORD",        "")
PG_PORT            = int(os.environ.get("PG_PORT",        "5432"))
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY",     "")
OPENAI_MODEL       = os.environ.get("OPENAI_MODEL",       "gpt-4o")
AZURE_BOT_APP_ID   = os.environ.get("AZURE_BOT_APP_ID",   "")
AZURE_BOT_APP_SECRET = os.environ.get("AZURE_BOT_APP_SECRET", "")
AZURE_TENANT_ID    = os.environ.get("AZURE_TENANT_ID",    "")

# Standard CORS headers applied to every response
CORS = {"Access-Control-Allow-Origin": "*"}


# ─── Database ─────────────────────────────────────────────────

def get_connection() -> pg8000.Connection:
    """
    Creates and returns a new SSL-secured PostgreSQL connection.
    SSL verification is disabled because Azure PostgreSQL uses
    a self-signed certificate that would otherwise fail validation.
    """
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return pg8000.connect(
        host=PG_HOST, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD,
        port=PG_PORT, ssl_context=ssl_context
    )


def check_connection() -> bool:
    """Returns True if the database is reachable, False otherwise."""
    try:
        conn = get_connection()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        return False


def save_chat_message(
    session_id: str, role: str, message: str,
    source: str | None, confidence: float | None
) -> None:
    """
    Persists a chat message to the chat_history table.
    Silently swallows errors so a logging failure never breaks
    the main chat response.
    """
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_history (session_id, role, message, source, confidence) VALUES (%s, %s, %s, %s, %s)",
            (session_id, role, message, source, confidence)
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        pass  # Logging failures should never break the chat response


# ─── Query Preprocessing ──────────────────────────────────────

def normalize_query(query: str) -> str:
    """
    Applies rule-based text normalisations to improve full-text search recall.
    Maps informal spellings and abbreviations to their canonical forms
    so PostgreSQL tsvector matching finds more relevant tickets.
    """
    q = query.lower()
    # Normalise WiFi variants — all map to the indexed form 'wi-fi'
    q = q.replace("wifi", "wi-fi").replace("wi fi", "wi-fi")
    # Expand contractions so tsquery tokens match ticket text
    q = q.replace("cant",    "cannot")
    q = q.replace("wont",    "will not")
    q = q.replace("doesnt",  "does not")
    # Map common plain-language phrases to indexed IT terminology
    q = q.replace("internet not working", "wi-fi network")
    q = q.replace("internet",             "network")
    q = q.replace("slow computer",        "slow performance")
    q = q.replace("pc",                   "computer")
    return q


# ─── IT Relevance Gate ────────────────────────────────────────

def is_it_related(query: str) -> bool:
    """
    Uses a zero-shot OpenAI classification call to decide whether
    the user's message is IT/tech-support related.

    Returns True (allow) on classification failure so a transient
    OpenAI error never silently blocks a legitimate support request.
    """
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a classifier. Determine if a question is related to "
                        "IT support, technology, software, hardware, networking, "
                        "cybersecurity, or managed services (MSP).\n\n"
                        "Reply with ONLY one word:\n"
                        "- YES if IT/tech/software/hardware/network related\n"
                        "- NO  if cooking, sports, entertainment, politics, finance, "
                        "health, or anything not IT related"
                    )
                },
                {"role": "user", "content": f"Is this IT related? '{query}'"}
            ],
            max_tokens=5,
            temperature=0
        )
        return "YES" in response.choices[0].message.content.strip().upper()
    except Exception:
        # Fail open — allow the question through rather than blocking valid IT queries
        return True


# ─── Keyword Extraction ───────────────────────────────────────

def extract_keywords(query: str) -> list[str]:
    """
    Uses OpenAI to translate plain-language user descriptions into
    precise IT terminology for ILIKE ticket searching.

    Falls back to stopword-filtered query words so search still
    works even when the OpenAI call fails.
    """
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an IT support specialist. A non-technical end user "
                        "has described an IT problem in plain language.\n"
                        "Extract 4-8 IT/technical keywords for searching a support ticket database.\n"
                        "Include: technical terms, synonyms, product names, related concepts.\n\n"
                        "Examples:\n"
                        "- \"user getting lots of phishing emails\" -> phishing,spam,email,malicious,security,scam,suspicious\n"
                        "- \"end user not receiving outlook emails\" -> outlook,email,receive,mailbox,inbox,sync\n"
                        "- \"wifi keeps dropping\" -> wifi,wi-fi,network,wireless,disconnect,connection\n"
                        "- \"laptop very slow\" -> slow,performance,laptop,speed,lagging,freeze\n"
                        "- \"VPN not working\" -> vpn,network,remote,tunnel,connection,access\n"
                        "- \"printer offline\" -> printer,offline,print,driver,queue\n\n"
                        "Return ONLY a comma-separated list of keywords. No explanations."
                    )
                },
                {"role": "user", "content": f"User said: {query}"}
            ],
            max_tokens=60,
            temperature=0
        )
        raw = response.choices[0].message.content.strip().lower()
        keywords = [k.strip().strip('"').strip("'") for k in raw.split(',') if k.strip() and len(k.strip()) > 1]
        logger.info(f"Keywords for '{query[:50]}': {keywords}")
        return keywords
    except Exception as e:
        logger.error(f"Keyword extraction failed: {e}")
        # Fallback: extract meaningful words directly from the query
        STOPWORDS = {
            "the","a","an","is","are","was","not","in","on","at","to","for","of","and",
            "or","it","my","i","we","he","she","they","can","with","from","by","do",
            "does","will","what","how","why","when","where","who","please","help",
            "user","end","getting","lots"
        }
        return [w for w in query.lower().split() if w not in STOPWORDS and len(w) > 2]


# ─── Ticket Scoring ───────────────────────────────────────────

def score_ticket(ticket: dict, keywords: list[str]) -> int:
    """
    Counts how many extracted keywords appear in the combined
    ticket text (title + description + resolution_notes).
    Used to re-rank search results by topic relevance.
    """
    text = (
        (ticket.get("title")            or "") + " " +
        (ticket.get("description")      or "") + " " +
        (ticket.get("resolution_notes") or "")
    ).lower()
    return sum(1 for kw in keywords if kw in text)


# ─── Ticket Search ────────────────────────────────────────────

def search_tickets(query: str) -> list[dict]:
    """
    Multi-layer ticket search strategy:

    Layer 1 — PostgreSQL full-text search (tsvector/tsquery).
               Fast and precise for exact terminology matches.

    Layer 2 — AI keyword ILIKE search (ALWAYS runs).
               Catches plain-language queries that Layer 1 misses
               because the user didn't use the exact indexed terms.

    Layer 3 — Stopword-filtered word search fallback.
               Runs only when layers 1 and 2 return fewer than 5 results.

    All layers are merged, deduplicated, then re-ranked by keyword
    match score so the most topic-relevant ticket appears first.
    """
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        normalized = normalize_query(query)
        all_rows: dict = {}  # ticket_id -> row (deduplication key)

        # Extract keywords before any search so Layer 2 always has them
        keywords = extract_keywords(query)
        logger.info(f"Search keywords: {keywords}")

        # ── Layer 1: Full-text search ──────────────────
        for q in list(dict.fromkeys([query, normalized])):
            try:
                cursor.execute("""
                    SELECT ticket_id, title, description, resolution_notes,
                           ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
                    FROM   autotask_tickets
                    WHERE  search_vector @@ plainto_tsquery('english', %s)
                    ORDER  BY rank DESC LIMIT 10
                """, (q, q))
                for r in cursor.fetchall():
                    if r[0] not in all_rows:
                        all_rows[r[0]] = r
            except Exception:
                pass  # Malformed tsquery — continue to keyword search

        # ── Layer 2: Keyword ILIKE search (always runs) ─
        if keywords:
            conditions = " OR ".join(
                ["title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s"] * len(keywords)
            )
            params = [p for kw in keywords for p in (f"%{kw}%", f"%{kw}%", f"%{kw}%")]
            cursor.execute(
                f"SELECT ticket_id, title, description, resolution_notes, 0.6 AS rank "
                f"FROM autotask_tickets WHERE {conditions} ORDER BY ticket_id DESC LIMIT 20",
                params
            )
            for r in cursor.fetchall():
                if r[0] not in all_rows:
                    all_rows[r[0]] = r

        # ── Layer 3: Meaningful-word fallback ──────────
        if len(all_rows) < 5:
            STOPWORDS = {
                "the","a","an","is","are","was","were","not","in","on","at","to","for",
                "of","and","or","it","my","i","we","he","she","they","their","its",
                "this","that","be","been","have","has","end","user","users","can",
                "with","from","by","do","did","does","will","would","could","should",
                "what","how","why","when","where","which","who","please","help"
            }
            meaningful = [w for w in normalized.split() if w not in STOPWORDS and len(w) > 2]
            if meaningful:
                conditions = " OR ".join(
                    ["title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s"] * len(meaningful)
                )
                params = [p for w in meaningful for p in (f"%{w}%", f"%{w}%", f"%{w}%")]
                cursor.execute(
                    f"SELECT ticket_id, title, description, resolution_notes, 0.3 AS rank "
                    f"FROM autotask_tickets WHERE {conditions} ORDER BY ticket_id DESC LIMIT 10",
                    params
                )
                for r in cursor.fetchall():
                    if r[0] not in all_rows:
                        all_rows[r[0]] = r

        cursor.close()
        conn.close()

        if not all_rows:
            return []

        # ── Re-rank by keyword relevance ───────────────
        tickets = [
            {
                "ticket_id":        r[0],
                "title":            r[1],
                "description":      r[2],
                "resolution_notes": r[3],
                "similarity":       min(float(r[4]) * 10, 1.0),
                "kw_score":         0  # populated below
            }
            for r in all_rows.values()
        ]
        if keywords:
            for t in tickets:
                t["kw_score"] = score_ticket(t, keywords)
            tickets.sort(key=lambda x: (x["kw_score"], x["similarity"]), reverse=True)

        logger.info(f"Top result: '{tickets[0]['title']}' kw_score={tickets[0]['kw_score']}")
        return tickets[:5]

    except Exception as e:
        logger.error(f"search_tickets error: {e}")
        return []


# ─── Answer Generation ────────────────────────────────────────

def get_smart_answer(query: str, tickets: list[dict]) -> dict:
    """
    Generates a ticket-grounded answer using GPT-4o.

    Relevance check: trusts the search engine — if any of the top-5
    tickets scored at least one keyword hit, the answer is marked
    as 'autotask'. Otherwise it returns source='no_match' so the
    frontend can ask the user's permission before calling OpenAI.
    """
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)

        # Build ticket context block for the LLM prompt
        context = "".join(
            f"\nTicket {i}:\nTitle: {t['title']}\nDescription: {t['description']}\nResolution: {t['resolution_notes']}\n"
            for i, t in enumerate(tickets[:5], 1)
        )

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a friendly IT support chat assistant for an MSP "
                        "(Managed Service Provider).\n"
                        "End users are NON-TECHNICAL people who describe problems "
                        "in plain everyday language.\n"
                        "You will be given the user's IT question and related support "
                        "tickets from the knowledge base.\n\n"
                        "Rules:\n"
                        "1. Understand the user's intent even if informal or imprecise.\n"
                        "2. Answer based on the ticket resolution in simple language.\n"
                        "3. If tickets relate to the topic (even partially), use them.\n"
                        "4. ONLY reply with exactly NO_MATCH if tickets are completely "
                        "unrelated (e.g. question about printers, tickets only about VPN).\n"
                        "5. Never mention ticket numbers or IDs.\n"
                        "6. NEVER use email-style sign-offs.\n"
                        "7. Write like a helpful chat message — concise and clear."
                    )
                },
                {"role": "user", "content": f"User question: {query}\n\nKnowledge base tickets:{context}\n\nProvide a helpful answer"}
            ],
            max_tokens=400,
            temperature=0.5
        )
        raw_answer = response.choices[0].message.content.strip()

        # If the model signals no relevant match, return no_match immediately
        if raw_answer.strip().upper() == "NO_MATCH":
            return {
                "answer":          "I couldn't find a relevant answer in the Autotask ticket database for your question.",
                "source":          "no_match",
                "confidence":      0.0,
                "ticket_title":    None,
                "ticket_id":       None,
                "related_tickets": []
            }

        # ── Relevance check: scan all top-5 tickets ────
        keywords       = extract_keywords(query)
        best_kw_score  = max((t.get("kw_score", 0) for t in tickets[:5]), default=0)
        all_ticket_text = " ".join(
            (t["title"] or "") + " " + (t["description"] or "") + " " + (t["resolution_notes"] or "")
            for t in tickets[:5]
        ).lower()
        total_kw_hits  = sum(1 for kw in keywords if kw in all_ticket_text)
        similarity     = tickets[0]["similarity"]

        # Trust the search: any keyword hit across all tickets = relevant
        ticket_is_relevant = (
            best_kw_score >= 1 or
            total_kw_hits >= 1 or
            similarity    >= 0.2
        )
        logger.info(
            f"Relevance: kw_score={best_kw_score} kw_hits={total_kw_hits} "
            f"sim={similarity:.2f} → relevant={ticket_is_relevant}"
        )

        return {
            "answer":       raw_answer,
            "source":       "autotask" if ticket_is_relevant else "no_match",
            "confidence":   round(tickets[0]["similarity"], 2) if ticket_is_relevant else 0.0,
            "ticket_title": tickets[0]["title"]     if ticket_is_relevant else None,
            "ticket_id":    tickets[0]["ticket_id"] if ticket_is_relevant else None,
            "related_tickets": [
                {
                    "ticket_id":        t["ticket_id"],
                    "title":            t["title"],
                    "description":      t["description"],
                    "resolution_notes": t["resolution_notes"],
                    "similarity":       round(t["similarity"], 2)
                }
                for t in tickets[:5]
            ]
        }

    except Exception as e:
        logger.error(f"get_smart_answer error: {e}")
        top = tickets[0]
        return {
            "answer":       top.get("resolution_notes") or top.get("description") or "No resolution found.",
            "source":       "autotask",
            "confidence":   round(top["similarity"], 2),
            "ticket_title": top["title"],
            "ticket_id":    top["ticket_id"],
            "related_tickets": []
        }


def get_actual_openai_answer(query: str) -> dict:
    """
    Called only when the user explicitly clicks 'Proceed with OpenAI'.
    Answers from general IT knowledge rather than the ticket database.
    """
    try:
        client   = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a friendly IT support chat assistant for an MSP.\n"
                        "Answer using general IT knowledge and best practices.\n"
                        "Be conversational — like a chat message, NOT an email.\n"
                        "NEVER use email-style sign-offs. Answer directly."
                    )
                },
                {"role": "user", "content": query}
            ],
            temperature=0.5,
            max_tokens=500
        )
        return {
            "answer":          response.choices[0].message.content,
            "source":          "openai",
            "confidence":      0.0,
            "ticket_title":    None,
            "ticket_id":       None,
            "related_tickets": [],
            "show_openai_btn": False
        }
    except Exception as e:
        logger.error(f"get_actual_openai_answer error: {e}")
        return {
            "answer":          "I'm having trouble connecting right now. Please try again.",
            "source":          "fallback",
            "confidence":      0.0,
            "ticket_title":    None,
            "ticket_id":       None,
            "related_tickets": [],
            "show_openai_btn": False
        }


def get_not_it_response() -> dict:
    """Returns a friendly rejection for non-IT questions."""
    return {
        "answer": (
            "I'm sorry, I can only help with IT support related questions 🖥️\n\n"
            "I specialise in topics like:\n"
            "• Password resets & account issues\n"
            "• Network & WiFi problems\n"
            "• Hardware & software troubleshooting\n"
            "• Email & Teams issues\n"
            "• Printer & device setup\n\n"
            "Please ask me an IT related question and I'll be happy to help!"
        ),
        "source":          "fallback",
        "confidence":      0.0,
        "ticket_title":    None,
        "ticket_id":       None,
        "related_tickets": [],
        "show_openai_btn": False
    }


# ─── HTTP Endpoints ───────────────────────────────────────────

@app.route(route="chat", methods=["POST", "OPTIONS"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main RAG chat endpoint.
    Flow: IT gate → ticket search → grounded answer generation.
    Returns source='no_match' when no relevant ticket is found
    so the frontend can ask the user's permission before calling OpenAI.
    """
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers={
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type"
        })
    try:
        body       = req.get_json()
        message    = body.get("message", "").strip()
        session_id = body.get("session_id") or str(uuid.uuid4())

        if not message:
            return func.HttpResponse(
                json.dumps({"error": "Message cannot be empty"}),
                status_code=400, mimetype="application/json", headers=CORS
            )

        if not is_it_related(message):
            result = get_not_it_response()
        else:
            tickets = search_tickets(message)
            result  = get_smart_answer(message, tickets) if tickets else {
                "answer":          "I couldn't find a specific answer in our support ticket database.",
                "source":          "no_match",
                "confidence":      0.0,
                "ticket_title":    None,
                "ticket_id":       None,
                "related_tickets": []
            }

        save_chat_message(session_id, "user",      message,         None,              None)
        save_chat_message(session_id, "assistant", result["answer"], result["source"],  result["confidence"])

        result["session_id"] = session_id
        result.setdefault("related_tickets", [])
        return func.HttpResponse(json.dumps(result), mimetype="application/json", headers=CORS)

    except Exception as e:
        logger.error(f"chat endpoint error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


@app.route(route="chatopenai", methods=["POST", "OPTIONS"])
def chat_openai(req: func.HttpRequest) -> func.HttpResponse:
    """
    User-triggered OpenAI fallback endpoint.
    Only called when the user explicitly clicks 'Proceed with OpenAI'
    after the frontend shows the no-match permission box.
    """
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=200, headers={
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type"
        })
    try:
        body       = req.get_json()
        message    = body.get("message", "").strip()
        session_id = body.get("session_id") or str(uuid.uuid4())

        if not message:
            return func.HttpResponse(
                json.dumps({"error": "Message cannot be empty"}),
                status_code=400, mimetype="application/json", headers=CORS
            )

        result             = get_actual_openai_answer(message)
        result["session_id"] = session_id
        result.setdefault("related_tickets", [])
        save_chat_message(session_id, "assistant", result["answer"], "openai", 0.0)
        return func.HttpResponse(json.dumps(result), mimetype="application/json", headers=CORS)

    except Exception as e:
        logger.error(f"chat_openai endpoint error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


@app.route(route="tickets", methods=["GET"])
def tickets(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns a paginated, optionally filtered list of Autotask tickets.
    Supports ?search=<term> and ?limit=<n> query parameters.
    """
    try:
        search = req.params.get("search", "")
        limit  = int(req.params.get("limit", 100))
        conn   = get_connection()
        cursor = conn.cursor()

        if search:
            cursor.execute("""
                SELECT ticket_id, title, description, resolution_notes, synced_at
                FROM   autotask_tickets
                WHERE  title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s
                ORDER  BY ticket_id DESC LIMIT %s
            """, (f"%{search}%", f"%{search}%", f"%{search}%", limit))
        else:
            cursor.execute(
                "SELECT ticket_id, title, description, resolution_notes, synced_at "
                "FROM autotask_tickets ORDER BY ticket_id DESC LIMIT %s", (limit,)
            )

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        return func.HttpResponse(
            json.dumps({
                "total":   len(rows),
                "tickets": [
                    {"ticket_id": r[0], "title": r[1], "description": r[2],
                     "resolution_notes": r[3], "synced_at": str(r[4])}
                    for r in rows
                ]
            }),
            mimetype="application/json", headers=CORS
        )
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


@app.route(route="stats", methods=["GET"])
def stats(req: func.HttpRequest) -> func.HttpResponse:
    """Returns ticket count statistics for the sidebar display."""
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM autotask_tickets")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM autotask_tickets WHERE resolution_notes IS NOT NULL AND resolution_notes != ''")
        with_res = cursor.fetchone()[0]
        cursor.execute("SELECT MAX(synced_at) FROM autotask_tickets")
        last_sync = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return func.HttpResponse(
            json.dumps({"total_tickets": total, "with_resolution": with_res, "last_synced": str(last_sync)}),
            mimetype="application/json", headers=CORS
        )
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Liveness probe — returns database connectivity status."""
    db_ok = check_connection()
    return func.HttpResponse(
        json.dumps({"status": "ok" if db_ok else "db_error", "database": "connected" if db_ok else "error"}),
        mimetype="application/json", headers=CORS
    )


@app.route(route="messages", methods=["POST"])
def messages(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure Bot Service messaging endpoint.
    Receives Bot Framework Activity objects from Teams/Web Chat,
    generates an answer, then POSTs the reply back to the service URL.

    Uses Single-Tenant OAuth2 (tenant-specific token URL) because
    the Azure Bot resource was registered as a Single Tenant app.
    Multi-tenant bots use 'botframework.com' — that would fail here.
    """
    import requests as http_requests

    try:
        body     = req.get_json()
        act_type = body.get("type", "")
        logger.info(f"Bot activity: type={act_type} channel={body.get('channelId')} text={str(body.get('text',''))[:50]}")

        # Only process message activities; acknowledge everything else silently
        if act_type != "message":
            return func.HttpResponse(status_code=200, headers=CORS)

        user_text   = (body.get("text") or "").strip()
        service_url = body.get("serviceUrl", "").rstrip("/") + "/"
        conv_id     = body.get("conversation", {}).get("id", "")
        activity_id = body.get("id", "")
        session_id  = conv_id or str(uuid.uuid4())

        if not user_text:
            return func.HttpResponse(status_code=200, headers=CORS)

        # Generate answer using the same RAG pipeline as the web chat
        if not is_it_related(user_text):
            answer = (
                "I can only help with IT support related questions.\n\n"
                "I specialise in:\n- Password resets\n- Network & WiFi\n"
                "- Hardware & software\n- Email & Teams\n- Printer & device setup"
            )
        else:
            tickets = search_tickets(user_text)
            result  = get_smart_answer(user_text, tickets) if tickets else {
                "answer": "I couldn't find a specific answer. Please contact IT support directly.",
                "source": "no_match", "ticket_id": None, "ticket_title": None
            }
            answer = result["answer"]
            if result.get("ticket_id"):
                answer += "\n\nReference: Ticket #" + str(result["ticket_id"]) + " - " + str(result.get("ticket_title", ""))

        save_chat_message(session_id, "user",      user_text, None, None)
        save_chat_message(session_id, "assistant", answer,    None, None)

        # Single-Tenant token — must use the specific tenant ID, not botframework.com
        token_url  = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
        token_resp = http_requests.post(token_url, data={
            "grant_type":    "client_credentials",
            "client_id":     AZURE_BOT_APP_ID,
            "client_secret": AZURE_BOT_APP_SECRET,
            "scope":         "https://api.botframework.com/.default"
        }, timeout=10)
        token_json   = token_resp.json()
        access_token = token_json.get("access_token", "")
        logger.info(f"Token status: {token_resp.status_code} | error: {token_json.get('error','none')}")

        if not access_token:
            logger.error(f"No access token: {token_json}")
            return func.HttpResponse(status_code=200, headers=CORS)

        # Post reply back to Bot Framework
        reply_url = f"{service_url}v3/conversations/{conv_id}/activities/{activity_id}"
        reply_resp = http_requests.post(reply_url, json={
            "type":         "message",
            "text":         answer,
            "from":         body.get("recipient", {}),
            "conversation": body.get("conversation", {}),
            "recipient":    body.get("from", {}),
            "replyToId":    activity_id
        }, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, timeout=15)
        logger.info(f"Reply status: {reply_resp.status_code}")

        return func.HttpResponse(status_code=200, headers=CORS)

    except Exception as e:
        logger.error(f"messages endpoint error: {e}", exc_info=True)
        return func.HttpResponse(status_code=200, headers=CORS)