# ============================================================
# main.py — FastAPI Backend
# Run: python -m uvicorn main:app --reload
# ============================================================

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import logging
import uuid
import sys
import os
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_connection, setup_database, check_connection
from config import OPENAI_API_KEY, OPENAI_MODEL, SIMILARITY_THRESHOLD

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="MSP Support Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)


# ─── Models ───────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:    str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    answer:       str
    source:       str
    confidence:   float
    session_id:   str
    ticket_title: Optional[str] = None
    ticket_id:    Optional[int] = None


# ─── Startup ──────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    logger.info("🚀 Starting MSP Support Bot API...")
    if check_connection():
        setup_database()
        logger.info("✅ Database ready.")
    else:
        logger.warning("⚠️ Database not connected.")


# ─── Health ───────────────────────────────────────────────────
@app.get("/health")
def health():
    db_ok = check_connection()
    return {"status": "ok" if db_ok else "db_error", "database": "connected" if db_ok else "error"}


# ─── Normalize Query ──────────────────────────────────────────
def normalize_query(query: str) -> str:
    """Normalize common user terms to match ticket terminology."""
    q = query.lower()
    q = q.replace("wifi", "wi-fi")
    q = q.replace("wi fi", "wi-fi")
    q = q.replace("internet not working", "wi-fi network")
    q = q.replace("internet", "network")
    q = q.replace("cant", "cannot")
    q = q.replace("wont", "will not")
    q = q.replace("doesnt", "does not")
    # Removed: email -> outlook (was breaking email-related searches)
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
        return True  # If check fails, allow through


# ─── Search Tickets ───────────────────────────────────────────
def search_tickets(query: str) -> list:
    """Search tickets using full-text search + keyword fallback."""
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        normalized = normalize_query(query)
        rows = []

        # Layer 1: Full-text search on both original and normalized query
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

        # Layer 2: ILIKE normalized search
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

            # Layer 3: Individual word search (skip stopwords to avoid irrelevant matches)
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
                    FROM autotask_tickets
                    WHERE {conditions}
                    ORDER BY ticket_id DESC LIMIT 3
                """, params)
                rows = cursor.fetchall()

        cursor.close()
        conn.close()

        return [
            {
                "ticket_id":        r[0],
                "title":            r[1],
                "description":      r[2],
                "resolution_notes": r[3],
                "similarity":       min(float(r[4]) * 10, 1.0)
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


# ─── Smart Answer ─────────────────────────────────────────────
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
You will be given a user question and up to 3 support tickets from the knowledge base.

Follow these rules strictly:
1. ALWAYS try to answer using the ticket data provided — the tickets are already pre-filtered as relevant.
2. Even if the match is not perfect, give the best possible answer based on the closest ticket resolution.
3. Only if the user question is completely unrelated to all tickets, use your general IT knowledge to help.
4. If the question is too vague, ask for more specific details.
5. Never mention ticket numbers or IDs in your answer text.
6. Be friendly, concise and conversational — like a chat message, NOT an email.
7. NEVER sign off with "Warm regards", "Best regards", "[Your Name]" or any email-style closing.
8. Do not start with "Hi there!" — just answer directly and helpfully."""
                },
                {
                    "role": "user",
                    "content": f"User question: {query}\n\nAvailable tickets:{context}\n\nProvide a helpful answer"
                }
            ],
            max_tokens=400,
            temperature=0.4
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
            "source":       "autotask" if ticket_is_relevant else "no_match",
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


# ─── OpenAI Fallback ──────────────────────────────────────────
def get_openai_answer(query: str, history: list = []) -> dict:
    """Fallback to OpenAI when no ticket match found — always gives a helpful IT answer."""
    if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        return {
            "answer":       "I could not find a matching ticket. Please contact IT support directly.",
            "source":       "fallback",
            "confidence":   0.0,
            "ticket_title": None,
            "ticket_id":    None
        }
    try:
        client   = OpenAI(api_key=OPENAI_API_KEY)
        messages = [
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
6. Do not start with "Hi there!" — just answer directly."""
            }
        ]
        for msg in history[-6:]:
            messages.append({"role": msg["role"], "content": msg["message"]})
        messages.append({"role": "user", "content": query})

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.4,
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
        "answer":       "I can only help with IT support related questions 🖥️\n\nI specialise in:\n• Password resets & account issues\n• Network & WiFi problems\n• Hardware & software troubleshooting\n• Email & Teams issues\n• Printer & device setup\n\nPlease ask me an IT related question!",
        "source":       "fallback",
        "confidence":   0.0,
        "ticket_title": None,
        "ticket_id":    None
    }


# ─── Main Chat Endpoint ───────────────────────────────────────
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    session_id = req.session_id or str(uuid.uuid4())
    query      = req.message.strip()
    history    = get_chat_history(session_id)

    # Step 1: Check if IT related
    if not is_it_related(query):
        result = get_not_it_response()
    else:
        # Step 2: Search tickets
        tickets = search_tickets(query)
        result  = get_smart_answer(query, tickets) if tickets else get_openai_answer(query, history)

    save_chat_message(session_id, "user",      query,           None,           None)
    save_chat_message(session_id, "assistant", result["answer"], result["source"], result["confidence"])

    return ChatResponse(
        answer=result["answer"],
        source=result["source"],
        confidence=result["confidence"],
        session_id=session_id,
        ticket_title=result.get("ticket_title"),
        ticket_id=result.get("ticket_id")
    )


# ─── Chat History ─────────────────────────────────────────────
def get_chat_history(session_id: str) -> list:
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, message FROM chat_history
            WHERE session_id = %s ORDER BY created_at ASC LIMIT 20
        """, (session_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [{"role": r[0], "message": r[1]} for r in rows]
    except:
        return []


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


# ─── Tickets Endpoint ─────────────────────────────────────────
@app.get("/tickets")
def get_tickets(search: str = "", limit: int = 100):
    try:
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
            cursor.execute("""
                SELECT ticket_id, title, description, resolution_notes, synced_at
                FROM autotask_tickets ORDER BY ticket_id DESC LIMIT %s
            """, (limit,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return {"total": len(rows), "tickets": [
            {"ticket_id": r[0], "title": r[1], "description": r[2],
             "resolution_notes": r[3], "synced_at": str(r[4])} for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Stats Endpoint ───────────────────────────────────────────
@app.get("/stats")
def get_stats():
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
        return {"total_tickets": total, "with_resolution": with_res, "last_synced": str(last_sync)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))