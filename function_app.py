# ============================================================
# function_app.py — Azure Function App
# ============================================================

import azure.functions as func
import json
import logging
import uuid
import os
import pg8000
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

PG_HOST     = os.environ.get("PG_HOST",     "")
PG_DATABASE = os.environ.get("PG_DATABASE", "")
PG_USER     = os.environ.get("PG_USER",     "")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")
PG_PORT     = int(os.environ.get("PG_PORT", "5432"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o")

# ─── Azure Bot Credentials ────────────────────────────────────
AZURE_BOT_APP_ID     = os.environ.get("AZURE_BOT_APP_ID",     "")
AZURE_BOT_APP_SECRET = os.environ.get("AZURE_BOT_APP_SECRET", "")


def get_connection():
    import ssl
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return pg8000.connect(
        host=PG_HOST, database=PG_DATABASE, user=PG_USER,
        password=PG_PASSWORD, port=PG_PORT, ssl_context=ssl_context
    )


def check_connection():
    try:
        conn = get_connection()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        return False


def normalize_query(query: str) -> str:
    q = query.lower()
    q = q.replace("wifi", "wi-fi")
    q = q.replace("wi fi", "wi-fi")
    q = q.replace("internet not working", "wi-fi network")
    q = q.replace("internet", "network")
    q = q.replace("cant", "cannot")
    q = q.replace("wont", "will not")
    q = q.replace("doesnt", "does not")
    q = q.replace("slow computer", "slow performance")
    q = q.replace("pc", "computer")
    return q


# ─── IT Relevance Check ───────────────────────────────────────
def is_it_related(query: str) -> bool:
    """Uses OpenAI to check if the question is IT/tech support related."""
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """You are a classifier. Determine if a question is related to IT support, technology, software, hardware, networking, cybersecurity, or managed services (MSP).

Reply with ONLY one word:
- "YES" if the question is IT/tech/software/hardware/network related
- "NO" if the question is about cooking, sports, entertainment, politics, general knowledge, finance, health, or anything not IT related"""
                },
                {"role": "user", "content": f"Is this IT related? '{query}'"}
            ],
            max_tokens=5,
            temperature=0
        )
        answer = response.choices[0].message.content.strip().upper()
        return "YES" in answer
    except:
        return True  # If check fails, allow the question through


# ─── Extract Keywords via OpenAI ─────────────────────────────
def extract_keywords(query: str) -> list:
    """Extract key IT terms from plain language query for ticket search."""
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """You are an IT support specialist. A non-technical end user has described an IT problem in plain language.
Extract 4-8 IT/technical keywords for searching a support ticket database.
Include: technical terms, synonyms, product names, related concepts.
The user may use informal language — translate it to proper IT terms.

Examples:
- "user getting lots of phishing emails" -> phishing,spam,email,malicious,security,scam,suspicious
- "end user not receiving outlook emails" -> outlook,email,receive,mailbox,inbox,sync
- "wifi keeps dropping" -> wifi,wi-fi,network,wireless,disconnect,connection,internet
- "laptop very slow" -> slow,performance,laptop,speed,lagging,freeze,hang
- "VPN not working" -> vpn,network,remote,tunnel,connection,access
- "printer offline" -> printer,offline,print,driver,queue
- "password reset" -> password,reset,account,login,locked,credentials
- "screen is black" -> display,screen,monitor,black,blank,boot
- "teams calls dropping" -> teams,call,audio,video,connection,drop
- "computer keeps restarting" -> restart,reboot,crash,blue screen,bsod

Return ONLY a comma-separated list of keywords. No explanations."""
                },
                {"role": "user", "content": f"User said: {query}"}
            ],
            max_tokens=60,
            temperature=0
        )
        raw = response.choices[0].message.content.strip().lower()
        # Clean up and return keywords
        keywords = [k.strip().strip('"').strip("'") for k in raw.split(',') if k.strip() and len(k.strip()) > 1]
        logger.info(f"Keywords for '{query[:50]}': {keywords}")
        return keywords
    except Exception as e:
        logger.error(f"Keyword extraction failed: {e}")
        # Fallback: use meaningful words from the query itself
        STOPWORDS = {"the","a","an","is","are","was","not","in","on","at","to","for","of","and","or",
                     "it","my","i","we","he","she","they","can","with","from","by","do","does","will",
                     "what","how","why","when","where","who","please","help","user","end","getting","lots"}
        return [w for w in query.lower().split() if w not in STOPWORDS and len(w) > 2]


# ─── Score tickets by keyword overlap ────────────────────────
def score_ticket(ticket: dict, keywords: list) -> int:
    """Score a ticket by how many keywords appear in title/description/resolution."""
    text = (
        (ticket.get("title") or "") + " " +
        (ticket.get("description") or "") + " " +
        (ticket.get("resolution_notes") or "")
    ).lower()
    return sum(1 for kw in keywords if kw in text)


# ─── Search Tickets ───────────────────────────────────────────
def search_tickets(query: str) -> list:
    """
    Multi-layer search that ALWAYS runs keyword search regardless of Layer 1 results.
    Results are re-ranked by keyword relevance score.
    """
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        normalized = normalize_query(query)
        all_rows = {}  # ticket_id -> row (deduplicate)

        # ── Always extract keywords first ──────────────
        keywords = extract_keywords(query)
        logger.info(f"Keywords extracted: {keywords}")

        # ── Layer 1: Full-text search ──────────────────
        for q in list(dict.fromkeys([query, normalized])):
            try:
                cursor.execute("""
                    SELECT ticket_id, title, description, resolution_notes,
                        ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
                    FROM autotask_tickets
                    WHERE search_vector @@ plainto_tsquery('english', %s)
                    ORDER BY rank DESC LIMIT 10
                """, (q, q))
                for r in cursor.fetchall():
                    if r[0] not in all_rows:
                        all_rows[r[0]] = r
            except:
                pass

        # ── Layer 2: Keyword ILIKE search (ALWAYS runs) ─
        if keywords:
            conditions = " OR ".join(
                ["title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s"] * len(keywords)
            )
            params = []
            for kw in keywords:
                p = f"%{kw}%"
                params.extend([p, p, p])
            cursor.execute(f"""
                SELECT ticket_id, title, description, resolution_notes, 0.6 AS rank
                FROM autotask_tickets
                WHERE {conditions}
                ORDER BY ticket_id DESC LIMIT 20
            """, params)
            for r in cursor.fetchall():
                if r[0] not in all_rows:
                    all_rows[r[0]] = r

        # ── Layer 3: Normalized word search fallback ────
        if len(all_rows) < 5:
            STOPWORDS = {
                "the","a","an","is","are","was","were","not","in","on","at","to","for",
                "of","and","or","it","my","i","we","he","she","they","their","its",
                "this","that","be","been","have","has","end","user","users","can",
                "with","from","by","do","did","does","will","would","could","should",
                "what","how","why","when","where","which","who","please","help"
            }
            words = normalized.strip().split()
            meaningful = [w for w in words if w not in STOPWORDS and len(w) > 2]
            if meaningful:
                conditions = " OR ".join(
                    ["title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s"] * len(meaningful)
                )
                params = []
                for w in meaningful:
                    p = f"%{w}%"
                    params.extend([p, p, p])
                cursor.execute(f"""
                    SELECT ticket_id, title, description, resolution_notes, 0.3 AS rank
                    FROM autotask_tickets
                    WHERE {conditions}
                    ORDER BY ticket_id DESC LIMIT 10
                """, params)
                for r in cursor.fetchall():
                    if r[0] not in all_rows:
                        all_rows[r[0]] = r

        cursor.close()
        conn.close()

        if not all_rows:
            return []

        # ── Re-rank ALL results by keyword relevance ────
        tickets = [
            {
                "ticket_id":        r[0],
                "title":            r[1],
                "description":      r[2],
                "resolution_notes": r[3],
                "similarity":       min(float(r[4]) * 10, 1.0)
            }
            for r in all_rows.values()
        ]

        # Score each ticket by keyword matches
        if keywords:
            for t in tickets:
                t["kw_score"] = score_ticket(t, keywords)
            # Sort: first by keyword score (desc), then by similarity (desc)
            tickets.sort(key=lambda x: (x["kw_score"], x["similarity"]), reverse=True)
        
        logger.info(f"Top ticket: '{tickets[0]['title']}' kw_score={tickets[0].get('kw_score', 0)}")
        return tickets[:5]

    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


# ─── Smart Answer (Ticket-based) ──────────────────────────────
def get_smart_answer(query: str, tickets: list) -> dict:
    try:
        client  = OpenAI(api_key=OPENAI_API_KEY)
        context = ""
        for i, t in enumerate(tickets[:5], 1):
            context += f"\nTicket {i}:\nTitle: {t['title']}\nDescription: {t['description']}\nResolution: {t['resolution_notes']}\n"

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """You are a friendly IT support chat assistant for an MSP (Managed Service Provider).
End users are NON-TECHNICAL people who describe problems in plain everyday language.
You will be given the user's IT question and related support tickets from the knowledge base.

Rules:
1. Understand the user's intent even if they use informal or imprecise language.
2. Answer based on the ticket resolution — use simple language a non-tech person understands.
3. If tickets relate to the topic (even partially), give a helpful answer using them.
4. ONLY reply with exactly NO_MATCH if tickets are completely unrelated to the question.
   Example: question about phishing emails, tickets mention email security → USE THE TICKETS
   Example: question about printers, tickets only about VPN → NO_MATCH
5. Never mention ticket numbers or IDs.
6. Be warm, friendly and professional.
7. NEVER sign off with email-style closings.
8. Write like a helpful chat message. Keep it concise and clear."""
                },
                {"role": "user", "content": f"User question: {query}\n\nKnowledge base tickets:{context}\n\nProvide a helpful answer"}
            ],
            max_tokens=400,
            temperature=0.5
        )
        raw_answer = response.choices[0].message.content.strip()

        # If OpenAI says NO_MATCH, treat as no_match
        if raw_answer.strip().upper() == "NO_MATCH" or raw_answer.strip() == "NO_MATCH":
            return {
                "answer":          "I couldn't find a relevant answer in the Autotask ticket database for your question.",
                "source":          "no_match",
                "confidence":      0.0,
                "ticket_title":    None,
                "ticket_id":       None,
                "related_tickets": []
            }

        answer = raw_answer

        # ── Smart relevance check ────────────────────
        # Philosophy: if the search engine found tickets, TRUST it.
        # Only reject if there is ZERO keyword overlap across ALL tickets.
        keywords = extract_keywords(query)
        logger.info(f"Relevance check — keywords: {keywords}")

        # Check ALL top tickets for keyword matches
        best_kw_score  = max((t.get("kw_score", 0) for t in tickets[:5]), default=0)
        similarity     = tickets[0]["similarity"]

        # Build combined text from all top 5 tickets
        all_ticket_text = " ".join([
            (t["title"] or "") + " " + (t["description"] or "") + " " + (t["resolution_notes"] or "")
            for t in tickets[:5]
        ]).lower()

        total_kw_hits = sum(1 for kw in keywords if kw in all_ticket_text)

        # TRUST the search: if search found tickets with kw_score > 0, they're relevant
        # Only fall back to OpenAI if truly zero keyword overlap
        ticket_is_relevant = (
            best_kw_score  >= 1 or    # search engine scored them relevant
            total_kw_hits  >= 1 or    # at least one keyword found across all tickets
            similarity     >= 0.2     # any reasonable full-text match
        )
        logger.info(f"Relevance: kw_score={best_kw_score} total_kw_hits={total_kw_hits} sim={similarity:.2f} keywords={keywords} → relevant={ticket_is_relevant}")

        return {
            "answer":       answer,
            "source":       "autotask" if ticket_is_relevant else "no_match",
            "confidence":   round(tickets[0]["similarity"], 2) if ticket_is_relevant else 0.0,
            "ticket_title": tickets[0]["title"] if ticket_is_relevant else None,
            "ticket_id":    tickets[0]["ticket_id"] if ticket_is_relevant else None,
            "related_tickets": [
                {"ticket_id": t["ticket_id"], "title": t["title"],
                 "description": t["description"], "resolution_notes": t["resolution_notes"],
                 "similarity": round(t["similarity"], 2)}
                for t in tickets[:5]
            ]
        }
    except Exception as e:
        logger.error(f"Smart answer error: {e}")
        top = tickets[0]
        return {
            "answer":       top.get("resolution_notes") or top.get("description") or "No resolution found.",
            "source":       "autotask",
            "confidence":   round(top["similarity"], 2),
            "ticket_title": top["title"],
            "ticket_id":    top["ticket_id"]
        }


# ─── No Ticket Found Response ────────────────────────────────
def get_openai_answer(query: str) -> dict:
    """Returns a prompt asking user if they want OpenAI to answer."""
    return {
        "answer":          "The question you raised could not be found in our ticket database. Do you want me to proceed with answering using the open source knowledge database?",
        "source":          "no_ticket",
        "confidence":      0.0,
        "ticket_title":    None,
        "ticket_id":       None,
        "related_tickets": [],
        "show_openai_btn": True,
        "original_query":  query
    }


# ─── Actual OpenAI Answer ─────────────────────────────────────
def get_actual_openai_answer(query: str) -> dict:
    """Called when user clicks the OpenAI button."""
    try:
        client   = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """You are a friendly IT support chat assistant for an MSP (Managed Service Provider).
Answer using general IT knowledge and best practices.

Rules:
1. Give a helpful, practical IT answer.
2. Be warm, friendly and conversational — like a chat message, NOT an email.
3. Keep answers concise and easy to follow.
4. NEVER sign off with "Warm regards" or any email-style closing.
5. Do not start with "Hi there!" — answer directly."""
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
        logger.error(f"OpenAI error: {e}")
        return {
            "answer":          "I'm having trouble connecting right now. Please try again in a moment.",
            "source":          "fallback",
            "confidence":      0.0,
            "ticket_title":    None,
            "ticket_id":       None,
            "related_tickets": [],
            "show_openai_btn": False
        }


# ─── Not IT Related Response ──────────────────────────────────
def get_not_it_response() -> dict:
    return {
        "answer":       "I'm sorry, I can only help with IT support related questions 🖥️\n\nI specialise in topics like:\n• Password resets & account issues\n• Network & WiFi problems\n• Hardware & software troubleshooting\n• Email & Teams issues\n• Printer & device setup\n\nPlease ask me an IT related question and I'll be happy to help!",
        "source":          "fallback",
        "confidence":      0.0,
        "ticket_title":    None,
        "ticket_id":       None,
        "related_tickets": [],
        "show_openai_btn": False
    }


def save_chat_message(session_id, role, message, source, confidence):
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history (session_id, role, message, source, confidence)
            VALUES (%s, %s, %s, %s, %s)
        """, (session_id, role, message, source, confidence))
        conn.commit()
        cursor.close()
        conn.close()
    except:
        pass


CORS = {"Access-Control-Allow-Origin": "*"}


# ─── /chat ────────────────────────────────────────────────────
@app.route(route="chat", methods=["POST", "OPTIONS"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
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
            return func.HttpResponse(json.dumps({"error": "Message cannot be empty"}), status_code=400, mimetype="application/json", headers=CORS)

        # ── Step 1: Check if IT related ───────────────
        if not is_it_related(message):
            result = get_not_it_response()
        else:
            # ── Step 2: Search tickets ─────────────────
            tickets = search_tickets(message)
            if tickets:
                result = get_smart_answer(message, tickets)
            else:
                # No tickets found - prompt user to use OpenAI
                result = {
                    "answer":       "I couldn't find a specific answer in our support ticket database for this question.",
                    "source":       "no_match",
                    "confidence":   0.0,
                    "ticket_title": None,
                    "ticket_id":    None,
                    "related_tickets": []
                }

        save_chat_message(session_id, "user",      message,         None,           None)
        save_chat_message(session_id, "assistant", result["answer"], result["source"], result["confidence"])

        result["session_id"] = session_id
        if "related_tickets" not in result:
            result["related_tickets"] = []
        return func.HttpResponse(json.dumps(result), mimetype="application/json", headers=CORS)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


# ─── /chat/openai ────────────────────────────────────────────
@app.route(route="chatopenai", methods=["POST", "OPTIONS"])
def chat_openai(req: func.HttpRequest) -> func.HttpResponse:
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
            return func.HttpResponse(json.dumps({"error": "Message cannot be empty"}), status_code=400, mimetype="application/json", headers=CORS)
        result = get_actual_openai_answer(message)
        save_chat_message(session_id, "assistant", result["answer"], "openai", 0.0)
        result["session_id"] = session_id
        if "related_tickets" not in result:
            result["related_tickets"] = []
        return func.HttpResponse(json.dumps(result), mimetype="application/json", headers=CORS)
    except Exception as e:
        logger.error(f"OpenAI chat error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


# ─── /tickets ─────────────────────────────────────────────────
@app.route(route="tickets", methods=["GET"])
def tickets(req: func.HttpRequest) -> func.HttpResponse:
    try:
        search = req.params.get("search", "")
        limit  = int(req.params.get("limit", 100))
        conn   = get_connection()
        cursor = conn.cursor()
        if search:
            cursor.execute("""
                SELECT ticket_id, title, description, resolution_notes, synced_at
                FROM autotask_tickets
                WHERE title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s
                ORDER BY ticket_id DESC LIMIT %s
            """, (f"%{search}%", f"%{search}%", f"%{search}%", limit))
        else:
            cursor.execute("SELECT ticket_id, title, description, resolution_notes, synced_at FROM autotask_tickets ORDER BY ticket_id DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return func.HttpResponse(json.dumps({"total": len(rows), "tickets": [
            {"ticket_id": r[0], "title": r[1], "description": r[2], "resolution_notes": r[3], "synced_at": str(r[4])} for r in rows
        ]}), mimetype="application/json", headers=CORS)
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


# ─── /stats ───────────────────────────────────────────────────
@app.route(route="stats", methods=["GET"])
def stats(req: func.HttpRequest) -> func.HttpResponse:
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
        return func.HttpResponse(json.dumps({"total_tickets": total, "with_resolution": with_res, "last_synced": str(last_sync)}), mimetype="application/json", headers=CORS)
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json", headers=CORS)


# ─── /health ──────────────────────────────────────────────────
@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    db_ok = check_connection()
    return func.HttpResponse(json.dumps({"status": "ok" if db_ok else "db_error", "database": "connected" if db_ok else "error"}), mimetype="application/json", headers=CORS)




# ─── /chat/openai (User-triggered OpenAI answer) ──────────────

# ─── /messages (Azure Bot Service endpoint) ───────────────────
@app.route(route="messages", methods=["POST"])
def messages(req: func.HttpRequest) -> func.HttpResponse:
    """
    Receives messages from Azure Bot Service.
    Single Tenant bot — uses actual tenant ID for token.
    """
    import requests as http_requests

    TENANT_ID = "417b5070-1e2f-47be-b6fa-bf6392bf9666"  # App Tenant ID from Azure Portal

    try:
        body = req.get_json()
        logger.info(f"Bot activity received: type={body.get('type')} channel={body.get('channelId')} text={str(body.get('text',''))[:50]}")

        act_type = body.get("type", "")

        # Return 200 for non-message activities (conversationUpdate, typing etc.)
        if act_type != "message":
            logger.info(f"Skipping non-message activity: {act_type}")
            return func.HttpResponse(status_code=200, headers=CORS)

        user_text   = (body.get("text") or "").strip()
        service_url = body.get("serviceUrl", "").rstrip("/") + "/"
        conv_id     = body.get("conversation", {}).get("id", "")
        activity_id = body.get("id", "")
        session_id  = conv_id or str(uuid.uuid4())

        logger.info(f"Processing message: '{user_text[:50]}' | serviceUrl: {service_url}")

        if not user_text:
            return func.HttpResponse(status_code=200, headers=CORS)

        # ── Get answer ────────────────────────────────
        if not is_it_related(user_text):
            answer = "I can only help with IT support related questions.\n\nI specialise in:\n- Password resets\n- Network & WiFi\n- Hardware & software\n- Email & Teams\n- Printer & device setup"
        else:
            tickets = search_tickets(user_text)
            result  = get_smart_answer(user_text, tickets) if tickets else {
                "answer": "I couldn't find a specific answer in our ticket database. Please contact IT support directly.",
                "source": "no_match",
                "ticket_id": None,
                "ticket_title": None
            }
            answer = result["answer"]
            if result.get("ticket_id"):
                answer += "\n\n Reference: Ticket #" + str(result.get('ticket_id','')) + " - " + str(result.get('ticket_title',''))

        save_chat_message(session_id, "user",      user_text, None, None)
        save_chat_message(session_id, "assistant", answer,    None, None)

        # ── Get Bot Framework access token (Single Tenant) ──
        token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
        token_data = {
            "grant_type":    "client_credentials",
            "client_id":     AZURE_BOT_APP_ID,
            "client_secret": AZURE_BOT_APP_SECRET,
            "scope":         "https://api.botframework.com/.default"
        }
        token_resp = http_requests.post(token_url, data=token_data, timeout=10)
        token_json = token_resp.json()
        access_token = token_json.get("access_token", "")
        logger.info(f"Token status: {token_resp.status_code} | error: {token_json.get('error','none')}")

        if not access_token:
            logger.error(f"No access token! Response: {token_json}")
            return func.HttpResponse(status_code=200, headers=CORS)

        # ── Post reply back to Bot Service ────────────
        reply_url = f"{service_url}v3/conversations/{conv_id}/activities/{activity_id}"
        logger.info(f"Posting reply to: {reply_url}")

        reply_body = {
            "type":         "message",
            "text":         answer,
            "from":         body.get("recipient", {}),
            "conversation": body.get("conversation", {}),
            "recipient":    body.get("from", {}),
            "replyToId":    activity_id
        }
        reply_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json"
        }
        reply_resp = http_requests.post(reply_url, json=reply_body, headers=reply_headers, timeout=15)
        logger.info(f"Reply status: {reply_resp.status_code} | body: {reply_resp.text[:200]}")

        return func.HttpResponse(status_code=200, headers=CORS)

    except Exception as e:
        logger.error(f"Messages endpoint error: {e}", exc_info=True)
        return func.HttpResponse(status_code=200, headers=CORS)