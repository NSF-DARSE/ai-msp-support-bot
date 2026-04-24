# ============================================================
# scripts/embeddings.py
# Converts ticket text into vector embeddings for RAG search
# ============================================================

import openai
import logging
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)
openai.api_key = OPENAI_API_KEY

EMBEDDING_MODEL = "text-embedding-3-small"  # 1536 dimensions, cost-efficient


def generate_embedding(text: str) -> list:
    """
    Converts text into a vector embedding using OpenAI.
    Used for RAG similarity search in PostgreSQL.
    """
    if not text or not text.strip():
        return [0.0] * 1536  # Return zero vector for empty text

    try:
        response = openai.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text.strip()
        )
        return response.data[0].embedding

    except openai.AuthenticationError:
        logger.error("❌ Invalid OpenAI API key. Check config.py")
        return [0.0] * 1536
    except Exception as e:
        logger.error(f"❌ Embedding error: {e}")
        return [0.0] * 1536
