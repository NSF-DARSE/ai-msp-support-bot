# Changelog
## MSP Support Bot — UDRSE / NSF-DARSE

All notable changes to this project are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [v1.0.0] — 2026-04-27 — Production Release

### 🚀 First stable production release.
Full RAG pipeline, Microsoft Teams integration, and professional web UI deployed to Azure.

---

### Added

#### RAG Search Pipeline
- `extract_keywords()` — uses GPT-4o to translate plain-language queries into IT terminology
- `score_ticket()` — scores retrieved tickets by keyword overlap for relevance ranking
- 3-layer search: PostgreSQL full-text (Layer 1) → AI keyword ILIKE (Layer 2) → stopword-filtered fallback (Layer 3)
- Layer 2 now **always runs** regardless of Layer 1 results — fixes Outlook/phishing ticket misses
- All 3 layers merge and deduplicate results before re-ranking by keyword score
- Relevance check scans **all top 5 tickets** instead of just ticket #1

#### Plain Language Support
- System prompt updated for non-technical end users
- GPT-4o correctly interprets informal queries ("my thing won't connect", "getting phishing stuff")
- Keyword extraction generates synonyms (phishing → spam, malicious, scam, suspicious)
- `NO_MATCH` signal from GPT-4o when tickets are completely unrelated

#### Microsoft Teams Bot
- `/api/messages` endpoint for Azure Bot Service (Bot Framework Activity format)
- Fixed Single Tenant OAuth2 token URL (`login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token`)
- Bot replies correctly back to Teams and Azure Web Chat
- Teams app `UDRSEChatBotV1` approved and installed in UDRSE organization

#### Frontend Web UI (Complete Redesign)
- Professional white/navy corporate theme replacing dark gaming aesthetic
- DM Sans + DM Mono fonts for clean readability
- Navy header with Live status indicator
- Source banner on every bot reply (🎫 Autotask / 🤖 OpenAI)
- Ticket cards below each answer (horizontal scrollable, View button)
- Ticket detail modal popup with resolution notes
- No-match permission box with styled "Proceed with OpenAI" button
- 📋 Tickets panel — searchable table of all 1,537 tickets
- ℹ️ Help modal with 10 clickable example questions
- Conversation history sidebar with delete per conversation
- Character counter (250 char limit)
- Mobile responsive layout

#### Testing
- `test_bot.py` — 30 automated tests
- Section 1: `TestNormalizeQuery` — 10 unit tests for query normalization
- Section 2: `TestNotITResponse` — 6 unit tests for non-IT rejection
- Section 3: `TestNormalizeQueryEdgeCases` — 4 edge case tests
- Section 4: `TestAPIEndpoints` — 8 integration tests against live Azure API
- Section 5: `TestEndToEnd` — 2 full workflow tests
- All 30 tests passing in ~71 seconds

#### Documentation
- `README.md` — full quickstart, API reference, architecture diagram
- `PERFORMANCE.md` — ML models, profiling, tradeoffs, benchmarks
- `CHANGELOG.md` — this file
- `PERFORMANCE.md` — ML models used, runtime profiling, tradeoffs
- `UDRSE_Chatbot_Documentation_v3.docx` — full Phase 3 documentation
- `LICENSE` — MIT License (Sameer Rithwik & Senthurapandi, NSF-DARSE)

#### Code Quality
- Type hints on all functions (`-> dict`, `-> list[str]`, `-> bool`, `-> None`)
- Docstrings explaining design decisions (why, not what)
- Comments explain architectural choices (e.g. why SSL verification is disabled)
- All credentials moved to environment variables — zero hardcoded secrets
- Consistent naming: `snake_case` functions, `UPPER_CASE` constants

---

### Changed

- `get_smart_answer()` — relevance check now checks all top 5 tickets, not just #1
- `search_tickets()` — results re-ranked by `kw_score` before returning top 5
- System prompt — explicitly handles non-technical plain language users
- System prompt — `NO_MATCH` signal replaces silent fallback to OpenAI
- `chat` endpoint — returns `source=no_match` when no relevant ticket found
- `requirements.txt` — all dependency versions pinned for reproducibility
- Frontend — removed auto-OpenAI call; now requires explicit user permission

---

### Fixed

- **Outlook tickets not found** — Layer 2 was being skipped when Layer 1 returned results
- **Phishing tickets not found** — keyword extraction now generates synonyms (spam, malicious, scam)
- **Wrong ticket referenced** — relevance check now scans all top 5, not just top 1
- **Bot email sign-offs** — system prompt explicitly prohibits email-style closings
- **Teams token failure** — Single Tenant bots need tenant-specific token URL not `botframework.com`
- **View button not responding** — ticket data now stored in `data-*` attributes (no closure bugs)
- **Tickets panel stuck on loading** — `allTicketsData` moved to top-level globals
- **Stats showing N/A** — `loadStats()` now safely handles missing DOM elements
- **OpenAI called without permission** — `no_match` box now correctly shown outside meta block
- **Empty OpenAI query** — `window._lastQuery` now set immediately when user sends message
- **Syntax error on deploy** — multiline string in `messages` endpoint fixed
- **Duplicate `chatopenai` route** — removed duplicate Azure Function route registration

---

### Security

- Removed all hardcoded credentials from codebase
- All secrets stored as Azure App Settings environment variables
- `AZURE_TENANT_ID` moved to environment variable (was previously hardcoded)
- GitHub push protection enforced — no secrets committed to repository

---

### Known Issues

- Azure Function cold start adds ~2-5 seconds on first request after inactivity
- Tickets panel loads maximum 500 tickets (full 1,537 requires pagination)
- Teams bot replies as plain text (no adaptive cards/rich formatting yet)
- Custom domain not yet configured (is-a.dev approval pending)

---

## [v0.2.0] — 2026-03-15 — Phase 2

### Added
- Autotask API integration and ticket sync pipeline
- PostgreSQL database with tsvector full-text search index
- Basic RAG search (2-layer: full-text + word fallback)
- Azure Function App deployment
- Azure Static Web Apps deployment
- Basic chat UI (dark theme)
- `/api/health`, `/api/stats`, `/api/tickets` endpoints
- `auto_sync.py` — scheduled ticket sync every 6 hours
- Conversation history in sidebar
- OpenAI GPT-4o integration for answer generation

### Changed
- Migrated from local SQLite to Azure PostgreSQL
- Replaced simple keyword matching with PostgreSQL tsvector

---

## [v0.1.0] — 2026-02-01 — Phase 1 (Proof of Concept)

### Added
- Initial project structure and repository
- FastAPI local development server (`main.py`)
- Basic PostgreSQL connection
- Initial Autotask ticket data exploration
- Simple keyword-based ticket search
- Basic HTML frontend prototype

---

## Migration Guide (v0.x → v1.0.0)

If upgrading from a previous version:

1. **Update environment variables** — add `AZURE_TENANT_ID` to Azure App Settings
2. **Redeploy function app** — `func azure functionapp publish UDRSEChatBotFunction --build remote`
3. **Redeploy frontend** — copy new `index.html` to `wwwroot/` and run `swa deploy`
4. **Run tests** — `python -m pytest test_bot.py -v` to verify all 30 pass
5. **Firewall** — ensure Azure PostgreSQL AllowAll rule is active

---

## Installation Guide

See [README.md](README.md) for full setup instructions.

**Quick install:**
```bash
git clone https://github.com/NSF-DARSE/ai-msp-support-bot.git
cd ai-msp-support-bot
git checkout MSP-support-bot
pip install -r requirements.txt
cp .env.example .env   # fill in your credentials
uvicorn main:app --reload
```

---

*Maintained by Sameer Rithwik & Senthurapandi — NSF-DARSE*  
*Mentor: Tony Tancredi — Diamond Technologies*
