# ============================================================
# backend/fallback.py
# OpenAI fallback — called automatically when RAG score is low
# ============================================================

import openai
import logging
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)
openai.api_key = OPENAI_API_KEY


def get_openai_answer(query: str, chat_history: list = []) -> dict:
    """
    Fallback to OpenAI GPT when Autotask RAG cannot find a good answer.
    Includes chat history for context-aware multi-turn conversation.
    """
    logger.info(f"🤖 OpenAI fallback triggered for: {query}")

    system_prompt = """You are a helpful IT support assistant for UDRS\u0415.
A user has asked a question that could not be answered from the internal Autotask ticket database.
Provide a helpful, professional IT support response.
If you don't know the specific answer, guide the user on next steps or who to contact."""

    # Build messages with history for multi-turn support
    messages = [{"role": "system", "content": system_prompt}]

    # Add recent chat history (last 6 messages for context)
    for msg in chat_history[-6:]:
        messages.append({"role": msg["role"], "content": msg["message"]})

    # Add current question
    messages.append({"role": "user", "content": query})

    try:
        response = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.5,
            max_tokens=600
        )

        answer = response.choices[0].message.content
        logger.info("✅ OpenAI fallback answered successfully.")

        return {
            "answer":     answer,
            "source":     "openai",
            "confidence": 0.0
        }

    except openai.AuthenticationError:
        logger.error("❌ Invalid OpenAI API key.")
        return {
            "answer":     "I'm sorry, I cannot process your request right now. Please contact support directly.",
            "source":     "error",
            "confidence": 0.0
        }
    except Exception as e:
        logger.error(f"❌ OpenAI fallback error: {e}")
        return {
            "answer":     "I'm sorry, something went wrong. Please try again or contact support.",
            "source":     "error",
            "confidence": 0.0
        }
