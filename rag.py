# ============================================================
# backend/rag.py
# RAG Pipeline — searches tickets by semantic similarity
# If confidence too low → signals fallback to OpenAI
# ============================================================

import openai
import logging
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import OPENAI_API_KEY, OPENAI_MODEL, SIMILARITY_THRESHOLD, TOP_K_RESULTS
from backend.database import get_connection
from scripts.embeddings import generate_embedding

logger = logging.getLogger(__name__)
openai.api_key = OPENAI_API_KEY


# ─── Search Relevant Tickets ──────────────────────────────────
def search_tickets(query: str) -> list:
    """
    Finds the most relevant tickets using vector similarity search.
    Returns list of tickets with their similarity scores.
    """
    query_embedding = generate_embedding(query)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            ticket_id,
            title,
            description,
            resolution_notes,
            1 - (embedding <=> %s::vector) AS similarity
        FROM autotask_tickets
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """, (query_embedding, query_embedding, TOP_K_RESULTS))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    results = []
    for row in rows:
        results.append({
            "ticket_id":        row[0],
            "title":            row[1],
            "description":      row[2],
            "resolution_notes": row[3],
            "similarity":       round(float(row[4]), 4)
        })

    return results


# ─── Build Context from Tickets ───────────────────────────────
def build_context(tickets: list) -> str:
    """Formats retrieved tickets into a readable context block for the LLM."""
    if not tickets:
        return ""

    context_parts = []
    for i, t in enumerate(tickets, 1):
        part = f"[Ticket {i} — ID: {t['ticket_id']} | Similarity: {t['similarity']*100:.1f}%]\n"
        if t["title"]:            part += f"Title: {t['title']}\n"
        if t["description"]:      part += f"Description: {t['description']}\n"
        if t["resolution_notes"]: part += f"Resolution: {t['resolution_notes']}\n"
        context_parts.append(part)

    return "\n---\n".join(context_parts)


# ─── Generate Answer from Autotask Data ──────────────────────
def get_autotask_answer(query: str) -> dict:
    """
    Main RAG function:
    1. Search relevant tickets
    2. Check if confidence is high enough
    3. If yes → generate answer from ticket data
    4. If no  → signal fallback to OpenAI
    """
    logger.info(f"🔍 RAG search for: {query}")

    # Step 1: Search tickets
    tickets = search_tickets(query)

    if not tickets:
        logger.info("⚠️ No tickets found → fallback to OpenAI")
        return {"answer": None, "source": "fallback", "confidence": 0.0, "tickets": []}

    top_score = tickets[0]["similarity"]
    logger.info(f"📊 Top similarity score: {top_score:.4f} (threshold: {SIMILARITY_THRESHOLD})")

    # Step 2: Check confidence threshold
    if top_score < SIMILARITY_THRESHOLD:
        logger.info(f"⚠️ Score {top_score:.2f} below threshold {SIMILARITY_THRESHOLD} → fallback to OpenAI")
        return {
            "answer":     None,
            "source":     "fallback",
            "confidence": top_score,
            "tickets":    tickets
        }

    # Step 3: Build context and generate answer
    context = build_context(tickets)

    system_prompt = """You are a helpful IT support assistant for UDRS\u0415.
You answer questions based ONLY on the Autotask support ticket data provided to you.
Be concise, professional, and helpful.
If the ticket data partially answers the question, provide what you can.
Never make up information not found in the tickets."""

    user_prompt = f"""Based on the following support tickets, answer this question:

Question: {query}

Relevant Tickets:
{context}

Please provide a clear, helpful answer based on the ticket data above."""

    try:
        response = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=600
        )

        answer = response.choices[0].message.content

        return {
            "answer":     answer,
            "source":     "autotask",
            "confidence": top_score,
            "tickets":    tickets
        }

    except Exception as e:
        logger.error(f"❌ RAG answer generation error: {e}")
        return {"answer": None, "source": "fallback", "confidence": top_score, "tickets": tickets}
