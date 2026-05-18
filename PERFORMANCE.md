# Performance & Resource Documentation
## MSP Support Bot — UDRSE / NSF-DARSE

---

## 1. ML Models Used

| Model | Provider | Purpose | Why Chosen |
|---|---|---|---|
| `gpt-4o` | OpenAI | IT relevance classification | Best accuracy for zero-shot classification |
| `gpt-4o` | OpenAI | Keyword extraction from plain language | Understands non-technical user phrasing |
| `gpt-4o` | OpenAI | Ticket-grounded answer generation | Follows strict instructions reliably |
| `gpt-4o` | OpenAI | OpenAI fallback general IT answers | General IT knowledge base |
| PostgreSQL `tsvector` | Azure PostgreSQL | Full-text search (Layer 1) | Built-in, no external ML dependency |

### Why GPT-4o over smaller models?
- End users are **non-technical** — queries like "my thing isn't working" require deep language understanding
- GPT-4o reliably returns `NO_MATCH` when tickets are unrelated (smaller models hallucinate answers)
- Keyword extraction quality directly affects search recall — GPT-4o produces better IT synonyms
- Single API handles all 4 ML tasks, reducing infrastructure complexity

---

## 2. Search Pipeline Performance

### 3-Layer RAG Architecture

```
User Query
    │
    ├─ Layer 1: PostgreSQL tsvector Full-Text Search    ~50-100ms
    │           (indexed, runs on DB server)
    │
    ├─ Layer 2: OpenAI Keyword Extraction + ILIKE       ~800-1200ms
    │           (always runs in parallel with Layer 1)
    │
    └─ Layer 3: Stopword-filtered word search           ~30-80ms
                (only if layers 1+2 return < 5 results)

Total Search Time: ~900-1400ms
GPT-4o Answer Generation: ~1500-3000ms
─────────────────────────────────────────
Total End-to-End Response: ~2.5-5 seconds
```

### Profiling Results (measured manually via Azure logs)

| Operation | Avg Time | Notes |
|---|---|---|
| `is_it_related()` | ~600ms | Single GPT-4o call, max_tokens=5 |
| `extract_keywords()` | ~800ms | Single GPT-4o call, max_tokens=60 |
| Layer 1 full-text search | ~80ms | PostgreSQL indexed tsvector |
| Layer 2 ILIKE search | ~120ms | Scans up to 20 results |
| `get_smart_answer()` | ~2000ms | GPT-4o, max_tokens=400 |
| `save_chat_message()` | ~50ms | Single INSERT |
| **Total /chat** | **~3.5s avg** | Cold start adds ~2s first call |

---

## 3. Choice of Metrics & Why

### Primary Metric: Keyword Match Score (`kw_score`)
**Why:** The kw_score counts how many AI-extracted keywords appear in a ticket's combined text fields. This is more reliable than pure full-text similarity because:
- Full-text search uses exact token matching — misses synonyms
- A user saying "phishing emails" should match tickets with "spam, malicious, security"
- kw_score captures semantic overlap that tsvector cannot

### Secondary Metric: PostgreSQL `ts_rank` Similarity
**Why:** ts_rank is a built-in relevance score from PostgreSQL's full-text search engine. It considers term frequency and position. Used as a tiebreaker when kw_scores are equal.

### Relevance Threshold
```python
ticket_is_relevant = (
    best_kw_score >= 1 or   # any keyword match across top 5 tickets
    total_kw_hits >= 1 or   # any keyword in combined ticket text
    similarity    >= 0.2    # minimum full-text similarity
)
```
**Why this threshold:** Testing showed that stricter thresholds (>= 2 keywords) caused Outlook and phishing tickets to be missed when users phrased queries informally. A threshold of 1 keyword hit maximises recall while the NO_MATCH fallback handles truly irrelevant results.

---

## 4. Dataset Scale & Memory Behavior

| Metric | Value |
|---|---|
| Total tickets | 1,537 |
| With resolution notes | 1,203 (78%) |
| Average ticket text size | ~500 chars |
| Total dataset size | ~750 KB |
| PostgreSQL index size | ~2 MB (tsvector) |
| Peak memory per request | ~50 MB (Azure Function) |

### Why this scale is reasonable:
- 1,537 tickets fits comfortably in a single PostgreSQL query scan
- No vectorization or GPU needed at this scale — ILIKE + tsvector is sufficient
- Each request is stateless — no in-memory accumulation between calls
- Azure Function cold starts (~2s) are acceptable for a support bot use case

---

## 5. Tradeoffs & Bottlenecks

### Tradeoff 1: GPT-4o for every request
**Bottleneck:** 3 separate GPT-4o calls per chat request (`is_it_related` + `extract_keywords` + `get_smart_answer`) adds ~2.5s latency.
**Why accepted:** Accuracy is more important than speed for IT support. A wrong answer is worse than a slow one.
**Alternative considered:** Fine-tuned smaller model (GPT-3.5) — rejected because it couldn't reliably handle informal plain-language queries.

### Tradeoff 2: No vector embeddings
**Decision:** PostgreSQL tsvector + ILIKE instead of semantic vector search (pgvector).
**Why:** The dataset (1,537 tickets) is too small to benefit meaningfully from embeddings. ILIKE with AI-extracted keywords achieves comparable recall at lower cost and complexity.
**When to switch:** If ticket count exceeds ~10,000, vector embeddings (pgvector + text-embedding-ada-002) would improve semantic matching.

### Tradeoff 3: Stateless Azure Functions
**Bottleneck:** Cold starts add ~2s on the first request after inactivity.
**Why accepted:** Azure Functions consumption plan is cost-effective for a student/MSP project. A dedicated App Service plan would eliminate cold starts but costs more.

### Tradeoff 4: No caching
**Decision:** No response caching implemented.
**Why:** IT support queries are highly varied — caching hit rate would be low. OpenAI responses for identical queries may differ slightly (temperature=0.5).
**Future improvement:** Cache `is_it_related()` results (temperature=0) for common non-IT phrases.

---

## 6. Vectorization, Batching & Parallelism

| Pattern | Used? | Detail |
|---|---|---|
| Vectorization | ✅ | PostgreSQL tsvector pre-computes search vectors at insert time |
| Batching | ✅ | Layer 2 searches all keywords in a single SQL query |
| Parallelism | ❌ | GPT-4o calls are sequential (acceptable at this scale) |
| GPU | ❌ | Not applicable — API-based inference, no local model |
| HPC patterns | ❌ | Not applicable — sub-2,000 ticket dataset |

### PostgreSQL Indexing Strategy
```sql
-- tsvector column pre-computed at sync time (not per-query)
-- This means Layer 1 search runs on an index, not a full table scan
ALTER TABLE autotask_tickets ADD COLUMN search_vector tsvector;
CREATE INDEX idx_search_vector ON autotask_tickets USING GIN(search_vector);
```

---

## 7. Known Performance Limitations

1. **Azure Function cold start** — first request after ~10min inactivity takes ~5s
2. **Sequential OpenAI calls** — `is_it_related()` and `extract_keywords()` could run in parallel (future improvement)
3. **500-ticket frontend limit** — Tickets panel loads max 500 tickets; full 1,537 requires pagination
4. **No streaming** — GPT-4o response is returned all at once, not streamed token by token
