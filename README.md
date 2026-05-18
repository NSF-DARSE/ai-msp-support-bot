# 🤖 MSP Support Bot

An AI-powered IT support chatbot for Managed Service Providers (MSPs).  
Built with a 3-layer RAG pipeline, GPT-4o, Azure PostgreSQL, and Microsoft Teams integration.

**Live Demo:** https://delightful-hill-0cdee7f0f.7.azurestaticapps.net  
**Teams App:** UDRSEChatBotV1 (installed in UDRSE org)  
**API:** https://udrsechatbotfunction-afc3hhehfyf9cddb.eastus-01.azurewebsites.net/api

---

## 📋 Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Repository Layout](#repository-layout)
4. [Quick Start — Local](#quick-start--local)
5. [Quick Start — Azure](#quick-start--azure)
6. [Environment Variables](#environment-variables)
7. [API Endpoints](#api-endpoints)
8. [Running Tests](#running-tests)
9. [Expected Runtime](#expected-runtime)
10. [Works On](#works-on)

---

## Overview

MSP Support Bot answers IT support questions by searching a real Autotask ticket database first.  
If no relevant ticket is found, it asks the user's permission before falling back to OpenAI's general knowledge base.

**Key features:**
- 3-layer RAG search (full-text → AI keyword → fallback word search)
- AI-powered keyword extraction for plain-language queries
- Source attribution banner (Autotask vs OpenAI)
- Microsoft Teams Bot integration (UDRSEChatBotV1)
- 1,537 real Autotask tickets with auto-sync every 6 hours
- 30 automated tests (unit + integration + end-to-end)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User                                  │
│          Web Browser          Microsoft Teams                │
└──────────────┬───────────────────────┬──────────────────────┘
               │                       │
               ▼                       ▼
┌──────────────────────┐   ┌───────────────────────┐
│  Azure Static Web    │   │  Azure Bot Service     │
│  Apps (index.html)   │   │  (UDRSEAzureChatBot)   │
└──────────┬───────────┘   └───────────┬───────────┘
           │                           │
           └──────────┬────────────────┘
                      ▼
         ┌────────────────────────┐
         │  Azure Function App    │
         │  (function_app.py)     │
         │                        │
         │  POST /api/chat        │
         │  POST /api/chatopenai  │
         │  GET  /api/tickets     │
         │  GET  /api/stats       │
         │  GET  /api/health      │
         │  POST /api/messages    │
         └────────┬───────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
┌──────────────┐   ┌─────────────────┐
│  Azure       │   │  OpenAI GPT-4o  │
│  PostgreSQL  │   │  API            │
│  (1,537      │   │  - IT gate      │
│   tickets)   │   │  - Keywords     │
│              │   │  - Answers      │
└──────────────┘   └─────────────────┘
```

---

## Repository Layout

```
ai-msp-support-bot/
│
├── function_app.py      # Azure Function App — all HTTP endpoints + RAG pipeline
├── main.py              # FastAPI local development server (mirrors function_app.py)
├── config.py            # Shared configuration (environment variables)
├── database.py          # Database connection helpers
├── auto_sync.py         # Autotask ticket sync scheduler (runs every 6 hours)
│
├── test_bot.py          # Test suite — 30 tests (unit + integration + E2E)
├── requirements.txt     # Pinned Python dependencies
│
├── index.html           # Frontend — single-file web chatbot UI
│
├── README.md            # This file
├── CHANGELOG.md         # Version history and release notes
├── PERFORMANCE.md       # ML models, profiling, tradeoffs
├── LICENSE              # MIT License
│
└── wwwroot/
    └── index.html       # Azure Static Web Apps deployment target
```

---

## Quick Start — Local

### Prerequisites
- Python 3.11 or higher
- pip
- Access to an Azure PostgreSQL database (or use the live one)
- OpenAI API key

### Step 1 — Clone the repository
```bash
git clone https://github.com/NSF-DARSE/ai-msp-support-bot.git
cd ai-msp-support-bot
git checkout MSP-support-bot
```

### Step 2 — Create virtual environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3 — Install dependencies
```bash
pip install -r requirements.txt
```

### Step 4 — Configure environment variables
Create a `.env` file in the project root:
```env
PG_HOST=your-postgres-host.postgres.database.azure.com
PG_DATABASE=postgres
PG_USER=dbadmin
PG_PASSWORD=your-password
PG_PORT=5432
OPENAI_API_KEY=sk-proj-your-key-here
OPENAI_MODEL=gpt-4o
AZURE_BOT_APP_ID=your-bot-app-id
AZURE_BOT_APP_SECRET=your-bot-secret
AZURE_TENANT_ID=your-tenant-id
```

### Step 5 — Run the local server
```bash
uvicorn main:app --reload --port 8000
```

### Step 6 — Open the chatbot
Open `index.html` in your browser, or visit http://localhost:8000

> **Note:** The frontend (`index.html`) points to the live Azure API by default.  
> To use your local server, change `const API = 'http://localhost:8000'` in `index.html`.

---

## Quick Start — Azure

### Prerequisites
- Azure CLI installed (`az --version`)
- Azure Functions Core Tools (`func --version`)
- Active Azure subscription
- Static Web Apps CLI (`npm install -g @azure/static-web-apps-cli`)

### Deploy Backend (Azure Function App)
```bash
# Login to Azure
az login

# Deploy the function app
func azure functionapp publish UDRSEChatBotFunction --build remote
```

### Deploy Frontend (Azure Static Web Apps)
```powershell
# Windows
copy index.html wwwroot\index.html
swa deploy ./wwwroot --deployment-token YOUR_TOKEN --env production

# macOS / Linux
cp index.html wwwroot/index.html
swa deploy ./wwwroot --deployment-token YOUR_TOKEN --env production
```

### Set Azure App Settings (environment variables)
```bash
az functionapp config appsettings set \
  --name UDRSEChatBotFunction \
  --resource-group UDRSEChatBotResourceGroup \
  --settings \
    PG_HOST="your-host" \
    PG_PASSWORD="your-password" \
    OPENAI_API_KEY="your-key" \
    AZURE_BOT_APP_ID="your-app-id" \
    AZURE_BOT_APP_SECRET="your-secret" \
    AZURE_TENANT_ID="your-tenant-id"
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PG_HOST` | ✅ | Azure PostgreSQL server hostname |
| `PG_DATABASE` | ✅ | Database name (default: `postgres`) |
| `PG_USER` | ✅ | Database username |
| `PG_PASSWORD` | ✅ | Database password |
| `PG_PORT` | ✅ | Database port (default: `5432`) |
| `OPENAI_API_KEY` | ✅ | OpenAI API key (GPT-4o access required) |
| `OPENAI_MODEL` | ✅ | Model name (default: `gpt-4o`) |
| `AZURE_BOT_APP_ID` | ⚠️ Teams only | Azure Bot Service App ID |
| `AZURE_BOT_APP_SECRET` | ⚠️ Teams only | Azure Bot Service App Secret |
| `AZURE_TENANT_ID` | ⚠️ Teams only | Azure tenant ID (Single Tenant bot) |

> ⚠️ **Never commit credentials to Git.** All values must be set as environment variables or Azure App Settings.

---

## API Endpoints

| Method | Endpoint | Description | Input | Output |
|---|---|---|---|---|
| `POST` | `/api/chat` | Main RAG chat | `{message, session_id}` | `{answer, source, confidence, related_tickets}` |
| `POST` | `/api/chatopenai` | OpenAI fallback | `{message, session_id}` | `{answer, source}` |
| `GET` | `/api/tickets` | List tickets | `?search=&limit=` | `{total, tickets[]}` |
| `GET` | `/api/stats` | DB statistics | — | `{total_tickets, with_resolution, last_synced}` |
| `GET` | `/api/health` | Health check | — | `{status, database}` |
| `POST` | `/api/messages` | Teams Bot | Bot Framework Activity | 200 OK |

### Example Request
```bash
curl -X POST https://udrsechatbotfunction-afc3hhehfyf9cddb.eastus-01.azurewebsites.net/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I reset my password?"}'
```

### Example Response
```json
{
  "answer": "You can reset your password using the self-service portal...",
  "source": "autotask",
  "confidence": 0.85,
  "session_id": "abc-123",
  "ticket_id": 9000,
  "ticket_title": "Password reset request",
  "related_tickets": [
    {"ticket_id": 9000, "title": "Password reset request", "resolution_notes": "..."}
  ]
}
```

---

## Running Tests

```bash
# Install test dependencies
pip install pytest requests

# Run all 30 tests with verbose output
python -m pytest test_bot.py -v

# Run a specific section only
python -m pytest test_bot.py::TestNormalizeQuery -v
python -m pytest test_bot.py::TestAPIEndpoints -v
python -m pytest test_bot.py::TestEndToEnd -v
```

### Test Coverage

| Section | Tests | What's Tested |
|---|---|---|
| `TestNormalizeQuery` | 10 | Query normalization rules |
| `TestNotITResponse` | 6 | Non-IT rejection response |
| `TestNormalizeQueryEdgeCases` | 4 | Empty, special chars, multiple replacements |
| `TestAPIEndpoints` | 8 | Live API: health, stats, chat, tickets |
| `TestEndToEnd` | 2 | Full RAG workflow + non-IT rejection |

**Expected output:**
```
30 passed in ~71 seconds
```

---

## Expected Runtime

| Operation | Expected Time |
|---|---|
| Local server startup | ~2 seconds |
| First Azure request (cold start) | ~5 seconds |
| Subsequent Azure requests | ~3-4 seconds |
| Full test suite | ~60-90 seconds |
| Ticket sync (auto_sync.py) | ~30-60 seconds |

---

## Works On

| System | Tested | Notes |
|---|---|---|
| Windows 11 | ✅ | Primary development environment |
| Azure (cloud) | ✅ | Production deployment |
| Microsoft Teams | ✅ | Via UDRSEChatBotV1 app |
| macOS | ✅ | Same Python/pip setup |
| Linux/Ubuntu | ✅ | Azure Functions runs on Ubuntu 24 |

---

## Built With

- [Azure Functions](https://azure.microsoft.com/en-us/products/functions) — Serverless backend
- [Azure PostgreSQL](https://azure.microsoft.com/en-us/products/postgresql) — Ticket database
- [Azure Static Web Apps](https://azure.microsoft.com/en-us/products/app-service/static) — Frontend hosting
- [Azure Bot Service](https://azure.microsoft.com/en-us/products/bot-services) — Teams integration
- [OpenAI GPT-4o](https://platform.openai.com) — LLM for keyword extraction and answer generation
- [pg8000](https://github.com/tlocke/pg8000) — Pure Python PostgreSQL driver

---

## Authors

| Name | Role | Organization |
|---|---|---|
| Sameer Rithwik | Student Developer | NSF-DARSE |
| Senthurapandi | Student Developer | NSF-DARSE |
| Tony Tancredi | VP, Diamond Technologies | Mentor |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
