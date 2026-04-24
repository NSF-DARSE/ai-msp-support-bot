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


# ─── Search Tickets ───────────────────────────────────────────
def search_tickets(query: str) -> list:
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        normalized = normalize_query(query)
        rows = []

        for q in list(dict.fromkeys([query, normalized])):
            cursor.execute("""
                SELECT ticket_id, title, description, resolution_notes,
                    ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
                FROM autotask_tickets
                WHERE search_vector @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC LIMIT 3
            """, (q, q))
            rows = cursor.fetchall()
            if rows:
                break

        if not rows:
            words   = normalized.strip().split()
            pattern = "%" + "%".join(words) + "%"
            cursor.execute("""
                SELECT ticket_id, title, description, resolution_notes, 0.5 AS rank
                FROM autotask_tickets
                WHERE title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s
                ORDER BY ticket_id DESC LIMIT 3
            """, (pattern, pattern, pattern))
            rows = cursor.fetchall()

            STOPWORDS = {
                "the","a","an","is","are","was","were","not","in","on","at","to","for",
                "of","and","or","it","my","i","we","he","she","they","their","its",
                "this","that","be","been","have","has","end","user","users","can",
                "with","from","by","do","did","does","will","would","could","should",
                "what","how","why","when","where","which","who","please","help"
            }
            meaningful = [w for w in words if w not in STOPWORDS and len(w) > 2]
            if not rows and meaningful:
                conditions = " OR ".join(
                    ["title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s"] * len(meaningful)
                )
                params = []
                for w in meaningful:
                    p = f"%{w}%"
                    params.extend([p, p, p])
                cursor.execute(f"""
                    SELECT ticket_id, title, description, resolution_notes, 0.3 AS rank
                    FROM autotask_tickets WHERE {conditions}
                    ORDER BY ticket_id DESC LIMIT 3
                """, params)
                rows = cursor.fetchall()

        cursor.close()
        conn.close()
        return [{"ticket_id": r[0], "title": r[1], "description": r[2], "resolution_notes": r[3], "similarity": min(float(r[4]) * 10, 1.0)} for r in rows]
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


# ─── Smart Answer (Ticket-based) ──────────────────────────────
def get_smart_answer(query: str, tickets: list) -> dict:
    try:
        client  = OpenAI(api_key=OPENAI_API_KEY)
        context = ""
        for i, t in enumerate(tickets[:3], 1):
            context += f"\nTicket {i}:\nTitle: {t['title']}\nDescription: {t['description']}\nResolution: {t['resolution_notes']}\n"

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """You are a friendly IT support chat assistant for an MSP (Managed Service Provider).
You will be given a user IT question and support tickets from the knowledge base.

Rules:
1. Answer based on the ticket resolution in a friendly, conversational chat tone.
2. If the tickets do not closely match, use your IT knowledge to give a helpful general IT answer.
3. If the question is too vague, ask for more specific details.
4. Never mention ticket numbers or IDs.
5. Be warm, friendly and professional. Use simple language.
6. NEVER sign off with "Warm regards", "Best regards", "[Your Name]" or any email-style closing.
7. Write like a chat message, not an email. Keep it concise and direct."""
                },
                {"role": "user", "content": f"User question: {query}\n\nKnowledge base tickets:{context}\n\nProvide a helpful answer"}
            ],
            max_tokens=400,
            temperature=0.5
        )
        answer = response.choices[0].message.content.strip()

        # ── Smart relevance check ──────────────────────
        # Check both title AND description, but require 2+ meaningful
        # word matches to avoid false positives from generic IT words
        STOPWORDS = {
            # Common English words
            "the","a","an","is","are","was","were","not","in","on","at","to","for",
            "of","and","or","it","my","i","we","he","she","they","their","its",
            "this","that","be","been","have","has","end","user","users","can",
            "with","from","by","do","did","does","will","would","could","should",
            "what","how","why","when","where","which","who","please","help",
            # Generic IT words that appear in almost every ticket
            "issue","problem","error","fix","need","support",
            "connect","connected","connecting","disconnected",
            "computer","device","machine","setup","setting","settings","sync","able","using",
            "thank","trying","tried","unable","cannot","getting",
            "work","works","stopped","suddenly","still","keep","keeps",
            "new","old","one","two","three","day","time","after","before",
            "receive","received","receiving","send","sent","sending",
            "open","opening","opened","close","closed","start","started",
            "access","accessing","accessed","run","running","fails","failed"
        }
        query_words = set(w for w in query.lower().split() if w not in STOPWORDS and len(w) > 3)
        # Check title AND description for broader matching
        ticket_text = (
            tickets[0]["title"] + " " +
            (tickets[0]["description"] or "") + " " +
            (tickets[0]["resolution_notes"] or "")
        ).lower()
        ticket_words = set(w for w in ticket_text.split() if w not in STOPWORDS and len(w) > 3)
        overlap = query_words & ticket_words
        # Title match = 1 word enough (title is very specific)
        # Description/notes match = need 2 words (broader text)
        title_words = set(w for w in tickets[0]["title"].lower().split() if w not in STOPWORDS and len(w) > 3)
        title_overlap = query_words & title_words
        ticket_is_relevant = len(title_overlap) >= 1 or len(overlap) >= 2

        return {
            "answer":       answer,
            "source":       "autotask" if ticket_is_relevant else "openai",
            "confidence":   round(tickets[0]["similarity"], 2) if ticket_is_relevant else 0.0,
            "ticket_title": tickets[0]["title"] if ticket_is_relevant else None,
            "ticket_id":    tickets[0]["ticket_id"] if ticket_is_relevant else None
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


# ─── OpenAI IT Fallback ───────────────────────────────────────
def get_openai_answer(query: str) -> dict:
    """Gives a helpful IT answer using general knowledge when no ticket matches."""
    try:
        client   = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """You are a friendly IT support chat assistant for an MSP (Managed Service Provider).
No specific ticket was found, but answer using general IT knowledge.

Rules:
1. Give a helpful, practical IT answer based on best practices.
2. Be warm, friendly and conversational — like a chat message, NOT an email.
3. Keep answers concise and easy to follow.
4. Only answer IT/tech/software/hardware/network related questions.
5. NEVER sign off with "Warm regards", "Best regards", "[Your Name]" or any email-style closing.
6. Do not write greetings like "Hi there!" at the start — just answer directly."""
                },
                {"role": "user", "content": query}
            ],
            temperature=0.5,
            max_tokens=500
        )
        return {
            "answer":       response.choices[0].message.content,
            "source":       "openai",
            "confidence":   0.0,
            "ticket_title": None,
            "ticket_id":    None
        }
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return {
            "answer":       "I'm having trouble connecting right now. Please try again in a moment.",
            "source":       "fallback",
            "confidence":   0.0,
            "ticket_title": None,
            "ticket_id":    None
        }


# ─── Not IT Related Response ──────────────────────────────────
def get_not_it_response() -> dict:
    return {
        "answer":       "I'm sorry, I can only help with IT support related questions 🖥️\n\nI specialise in topics like:\n• Password resets & account issues\n• Network & WiFi problems\n• Hardware & software troubleshooting\n• Email & Teams issues\n• Printer & device setup\n\nPlease ask me an IT related question and I'll be happy to help!",
        "source":       "fallback",
        "confidence":   0.0,
        "ticket_title": None,
        "ticket_id":    None
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
            result  = get_smart_answer(message, tickets) if tickets else get_openai_answer(message)

        save_chat_message(session_id, "user",      message,         None,           None)
        save_chat_message(session_id, "assistant", result["answer"], result["source"], result["confidence"])

        result["session_id"] = session_id
        return func.HttpResponse(json.dumps(result), mimetype="application/json", headers=CORS)
    except Exception as e:
        logger.error(f"Chat error: {e}")
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



# ─── /messages (Azure Bot Service endpoint) ───────────────────
@app.route(route="messages", methods=["POST"])
def messages(req: func.HttpRequest) -> func.HttpResponse:
    """
    Receives messages from Azure Bot Service and posts reply back.
    Works with Teams, Web Chat, and other Bot Framework channels.
    """
    import requests as http_requests

    try:
        body     = req.get_json()
        act_type = body.get("type", "")

        # Only process message activities
        if act_type != "message":
            return func.HttpResponse(status_code=200, headers=CORS)

        user_text   = (body.get("text") or "").strip()
        session_id  = body.get("conversation", {}).get("id", str(uuid.uuid4()))
        service_url = body.get("serviceUrl", "")
        channel_id  = body.get("channelId", "")
        activity_id = body.get("id", "")
        conv_id     = body.get("conversation", {}).get("id", "")

        if not user_text:
            return func.HttpResponse(status_code=200, headers=CORS)

        # ── Get answer ─────────────────────────────────
        if not is_it_related(user_text):
            answer = (
                "I\'m sorry, I can only help with IT support related questions 🖥️\n\n"
                "I specialise in:\n"
                "• Password resets & account issues\n"
                "• Network & WiFi problems\n"
                "• Hardware & software troubleshooting\n"
                "• Email & Teams issues\n"
                "• Printer & device setup\n\n"
                "Please ask me an IT related question!"
            )
        else:
            tickets = search_tickets(user_text)
            result  = get_smart_answer(user_text, tickets) if tickets else get_openai_answer(user_text)
            answer  = result["answer"]
            if result.get("ticket_id"):
                answer += f"\n\n📎 Reference: Ticket #{result['ticket_id']} — {result.get('ticket_title', '')}"

        save_chat_message(session_id, "user",      user_text, None, None)
        save_chat_message(session_id, "assistant", answer,    None, None)

        # ── Get Bot Framework access token ─────────────
        token_url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
        token_data = {
            "grant_type":    "client_credentials",
            "client_id":     AZURE_BOT_APP_ID,
            "client_secret": AZURE_BOT_APP_SECRET,
            "scope":         "https://api.botframework.com/.default"
        }
        token_resp = http_requests.post(token_url, data=token_data, timeout=10)
        token_json = token_resp.json()
        access_token = token_json.get("access_token", "")

        logger.info(f"Token status: {token_resp.status_code}")
        logger.info(f"Token error: {token_json.get('error', 'none')} - {token_json.get('error_description', '')}")
        logger.info(f"Service URL: {service_url}")
        logger.info(f"Conv ID: {conv_id}")
        logger.info(f"Activity ID: {activity_id}")

        # ── Post reply back to Bot Service ─────────────
        reply_url = f"{service_url}v3/conversations/{conv_id}/activities/{activity_id}"
        reply_body = {
            "type":         "message",
            "text":         answer,
            "from":         body.get("recipient", {}),
            "conversation": body.get("conversation", {}),
            "recipient":    body.get("from", {}),
            "replyToId":    activity_id
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json"
        }
        reply_resp = http_requests.post(reply_url, json=reply_body, headers=headers, timeout=15)
        logger.info(f"Reply status: {reply_resp.status_code} - {reply_resp.text[:200]}")

        return func.HttpResponse(status_code=200, headers=CORS)

    except Exception as e:
        logger.error(f"Messages endpoint error: {e}")
        return func.HttpResponse(status_code=200, headers=CORS)