# Thunder AI Agent Gateway — Engineering & Architecture Instructions

Welcome to the **Thunder AI Agent Gateway** (internally `streams-ai-agent-gateway`). This document serves as the single source of truth for the architecture, engineering principles, design patterns, coding conventions, and operational workflows of this repository.

All engineers and AI agents working on this codebase must adhere strictly to the guidelines and patterns defined herein.

---

## 1. System Overview & Core Mission

The **Thunder AI Agent Gateway** is a high-performance, asynchronous FastAPI-based AI gateway service. It connects real-time enterprise communication and streams platforms (telephony, PBX, team chats, omnichannel streams, and data warehouses) with conversational intelligence agents (notably **Luna AI**).

### Primary Capabilities:
- **Luna Conversational Intelligence**: Real-time multi-turn chat handling, user prompt upgrades, thread reply generation, and clarify-or-explain flows.
- **Dynamic Intent Routing**: Fast intent classification with tool invocation routing without bloated third-party orchestration frameworks.
- **Chat & Stream Summarization**: History extraction, conversation sanitation, token-safe windowing, and structured summaries.
- **Provider-Agnostic LLM Routing**: Unified abstraction layer decoupling business feature logic from specific model providers (OpenAI, Anthropic, local models).
- **Token & Cost Accounting**: Automated, non-intrusive auditing and metering of input, output, and cached tokens with precise cost calculations stored in MySQL.
- **Enterprise Security & Auth**: High-security dual-token system (short-lived JWT access tokens + HttpOnly encrypted refresh cookies) with failover to database session validation.
- **Resilient Multi-Pool Database Access**: Multi-database async connection pooling with automated read/write replica failover and retry mechanisms.

---

## 2. Technology Stack

| Layer | Technology | Details |
|---|---|---|
| **Runtime** | Python >= 3.13 | High performance, modern typing, asynchronous native features |
| **Package Manager** | `uv` | High-speed dependency resolver and virtual environment manager |
| **Web Framework** | FastAPI (standard) | Fully asynchronous ASGI application, Starlette middleware, lifespan handlers |
| **Data Validation** | Pydantic v2 | Model validation, JSON schema serialization, strict tool validation |
| **Database & Pooling** | SQLAlchemy 2.x + `aiomysql` | Async engines, raw SQL execution with parameterized bindings, multi-pool failover |
| **AI / LLM Clients** | Async OpenAI SDK | Responses API, function calling, streaming-ready |
| **Tokenization** | `tiktoken` | Exact pre-call token calculation and budget enforcement |
| **Text Processing** | `clean-text`, `ftfy`, `emoji` | Conversation string cleaning, Unicode sanitization, artifact removal |
| **Security** | `PyJWT`, `cryptography` | HS256/RS256 JWT signature verification and cookie session management |
| **Networking & I/O** | `aiohttp`, `aiofiles` | Async file downloads, resilient external service integration with backoff retry |
| **Process Manager** | Gunicorn (`uvicorn.workers.UvicornWorker`) | Production multi-worker ASGI daemon |

---

## 3. Repository Architecture & Directory Structure

```
thunder-ai-agent-gateway/
├── app/
│   ├── ai/                             # Provider-agnostic AI Core
│   │   ├── config_builder.py           # Prepares feature configs (model, system prompts, hyperparams)
│   │   ├── constants.py                # Cross-feature AI IDs, intent function names, billing operation tags
│   │   ├── prompts/                    # Versioned, domain-specific system prompts & constants
│   │   │   ├── intent_detection.py     # Prompt rules for intent classification & Luna tool choices
│   │   │   ├── chat_summary.py         # Summary generation & chunking prompts
│   │   │   ├── reply_to_thread.py      # Thread-reply context prompts
│   │   │   └── upgrade_user_chat.py    # Draft formatting & message refinement prompts
│   │   ├── providers/                  # Vendor adapter implementations
│   │   │   ├── base.py                 # Abstract AIProvider interface
│   │   │   └── openai/                 # OpenAI adapter & tokenizer
│   │   │       ├── client.py           # Async OpenAI Responses API caller
│   │   │       └── tokenizer.py        # Token counting using tiktoken
│   │   ├── registry.py                 # Master catalog of supported models, pricing, and limits
│   │   ├── response.py                 # Normalized AIResponse, ToolCall, and Usage data classes
│   │   ├── router.py                   # ModelRouter: single entry point for model calls + billing hook
│   │   ├── settings_cache.py           # In-memory + disk cache for dynamic runtime AI settings
│   │   ├── tokenizer.py                # Universal token validation & window truncation
│   │   └── tools.py                    # LangChain-free Pydantic <-> OpenAI Tool converter & validator
│   ├── api/                            # HTTP Transport Layer
│   │   └── v1/
│   │       ├── auth_router.py          # Session auth & token generation/refresh endpoints
│   │       └── luna_router.py          # Luna AI chat & intent routing entrypoint
│   ├── billing/                        # Usage Metering & Billing
│   │   ├── repository.py               # Database persistence for billing logs
│   │   └── service.py                  # Calculation of cached/non-cached prompt & completion costs
│   ├── core/                           # Foundation Utilities & Configs
│   │   ├── configs/                    # App & DB configuration objects
│   │   │   ├── app_config.py           # API routes, prefixes, and global server parameters
│   │   │   └── mysql_config.py         # Database hosts, replicas, pools, and credentials
│   │   ├── constants.py                # Application-wide constants, public endpoints, header keys
│   │   ├── log.py                      # Logging initialization & teardown
│   │   ├── message_cleaner.py          # Stream message cleaning and conversation sanitization
│   │   ├── settings.py                 # Environment variable management
│   │   └── utils.py                    # Shared helper utilities
│   ├── db/                             # Multi-Database Layer
│   │   └── mysql/
│   │       ├── connection/
│   │       │   ├── db_pool.py          # Async connection pool manager (Master/Slave engines)
│   │       │   └── db_connector.py     # Fault-tolerant query executor with automated retry/failover
│   │       ├── queries/                # Parameterized SQL statement definitions
│   │       │   ├── opensips_sql.py
│   │       │   ├── streams_sql.py
│   │       │   └── warehouse_sql.py
│   │       └── repositories/           # Domain-specific data access objects
│   │           ├── cloud_repo.py       # User auth session verification
│   │           ├── livepbx_repo.py     # PBX & telephony metadata queries
│   │           ├── opensips_repo.py    # SIP signaling records
│   │           ├── streams_repo.py     # Chat messages, streams, threads, and participant data
│   │           └── warehouse_repo.py   # Historical analytics and archive logs
│   ├── features/                       # Business Domain Capabilities
│   │   ├── auth/                       # Token payload schemas & response contracts
│   │   ├── chat_summary/               # Summarization workflow, chunking logic, and NLP schemas
│   │   ├── general_chat/               # Standalone chat, message rewriting, and thread replying
│   │   └── intent_detection/           # Intent handler, dispatch logic, and tool schemas
│   ├── integrations/                   # External Services & Protocols
│   │   ├── mcp/                        # Model Context Protocol (MCP) clients & adapters
│   │   └── search/                     # Web search integration client
│   ├── middleware/                     # Global ASGI Middlewares
│   │   └── manager.py                  # Middleware registration pipeline
│   ├── security/                       # Authentication & Authorization Security
│   │   ├── jwt_auth_middleware.py      # Starlette HTTP Bearer token validation middleware
│   │   └── jwt_manager.py              # JWT generation, token rotation, signature verification
│   ├── services/                       # Cross-cutting Domain Services
│   │   └── gatekeeper_service.py       # Secure media/file retrieval with retry & fallbacks
│   └── workers/                        # Background Task Workers
│       ├── attachment_worker.py        # Async document and media ingestion
│       ├── embedding_worker.py         # Vector embeddings generator
│       └── summarization_worker.py     # Asynchronous batch conversation summarizer
├── gunicorn_config.py                  # Production Gunicorn configuration
├── instructions.md                     # Engineering & Architecture Instructions (this file)
├── main.py                             # Application entry point & lifespan setup
├── pyproject.toml                      # Project metadata & dependency specs
└── uv.lock                             # Locked dependency tree
```

---

## 4. Fundamental Design Invariants & Architectural Patterns

### 4.1. LangChain-Free, Single-Source-of-Truth Tool Architecture
- **No Heavy Frameworks**: Do NOT add `langchain`, `langgraph`, or heavy agent wrappers.
- **Pydantic Model as the Single Truth**: Tool definitions are derived directly from Pydantic models via `pydantic_model_to_openai_tool()` in `app/ai/tools.py`.
- **Bidirectional Strict Validation**:
  - The model schema is auto-generated in OpenAI `strict: true` format.
  - The model's returned JSON arguments are validated back through `parse_tool_call_arguments(ModelClass, tool_call.arguments)`.
  - Schema definitions and runtime payload parsing never drift apart.

### 4.2. Complete Decoupling of AI Providers from Feature Handlers
- **No Direct Vendor Imports**: Feature handlers (`app/features/*`) must **never** import `openai`, any provider SDK, or vendor-specific response types.
- **Uniform Interface**: All features call `model_router.generate(request_data) -> AIResponse`.
- **Normalized Response Object**: `AIResponse` encapsulates `text`, `tool_calls: list[ToolCall]`, and `usage: Usage`. Handlers parse only `AIResponse`.
- **Extensibility**: Integrating a new LLM provider (e.g. Anthropic Claude, Google Gemini, Ollama) requires only adding an implementation of `AIProvider` in `app/ai/providers/` and registering it in `app/ai/registry.py`.

### 4.3. Automated Token & Cost Metering
- **Non-Intrusive Billing**: The `ModelRouter` automatically intercepts every successful generation call and invokes `billing_service.record_usage(response, request_data)`.
- **Granular Token Breakdowns**: Accurate accounting for non-cached input tokens, cached input tokens, and completion output tokens.
- **Audit Persistence**: All usage records are persisted to the database with tenant identifiers (`siteid`, `sitename`, `agentid`), model name, and computed dollar amounts.

### 4.4. Dual Database Pool & Failover Resilience
- **Master/Slave Separation**: Read queries (`execute`) target slave pools; write queries (`execute_statement`) target master pools.
- **Automated Failover**: `DBConnector` automatically iterates through configured host pools with exponential/retry backoff on connection errors.
- **Parameterized SQL**: Always bind variables using SQLAlchemy `bindparam` or dictionary parameters. Never interpolate strings into SQL.

### 4.5. Multi-Tier Authentication & Token Rotation
- **Stateless Verification**: Protected endpoints are validated via `JWTAuthMiddleware` inspecting Bearer tokens in the `Authorization` header.
- **Public Path Whitelist**: `/health`, `/api/v1/auth/generate_tokens`, and preflight `OPTIONS` requests are exempt from middleware interception.
- **Double Token Flow**:
  - Access Token: Short-lived (15 minutes), Bearer header.
  - Refresh Token: Long-lived (7 days), stored in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie.
- **Graceful Fallback**: If a refresh token is expired or missing, `/generate_tokens` validates the underlying user session in `cloud_repo` using `authkey` and `username`.

---

## 5. Coding Standards & Conventions

### 5.1. Python Conventions & Typing
- **Python Version**: Minimum Python 3.13. Utilize native union syntax (`X | Y`), built-in generics (`list[str]`, `dict[str, Any]`), and modern standard library features.
- **Asynchronous Everywhere**: All network, database, file, and external service I/O must be strictly `async`/`await`. Never use blocking libraries (e.g., `requests`, blocking `urllib`, blocking `time.sleep`). Use `aiohttp`, `aiofiles`, and `asyncio.sleep`.
- **Singleton Pattern**: Core service objects and database pools use the `_instance` pattern:
  ```python
  class ServiceName:
      _instance = None
      def __new__(cls):
          if cls._instance is None:
              cls._instance = super().__new__(cls)
          return cls._instance
  
  service_name = ServiceName()
  ```

### 5.2. Pydantic Models & Request Schemas
- Place all schemas within the `schemas.py` file inside the relevant feature directory (e.g., `app/features/<feature>/schemas.py`).
- Every field must include appropriate type annotations and descriptive `Field(..., description="...")` tags.
- For tool arguments, docstrings on the Pydantic classes provide the description passed to LLM function definitions.

### 5.3. Error Handling & Standard Responses
- Never silence exceptions without logging. Use `logger.exception("Descriptive message :: %s", err)` to preserve stack traces.
- APIs should return standardized structured status dictionaries on caught failure states:
  ```python
  return {
      "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
      "error": str(e),
      "msg": "Failed",
  }
  ```
- Always include tenant context in logs (`agentid`, `siteid`) to facilitate trace debugging in production.

---

## 6. How-To Extension Guides

### 6.1. Adding a New Luna Intent / Tool

1. **Define Constants**: Add the function name constant in `app/ai/constants.py`:
   ```python
   FUNCTION_NEW_INTENT = "new_intent_name"
   ```
2. **Create Argument Schema**: In `app/features/intent_detection/schemas.py`, define the arguments model:
   ```python
   class NewIntentArgs(BaseModel):
       """Clear description for the LLM explaining when and how to call this tool."""
       target_field: str = Field(..., description="Description of the parameter")
   ```
3. **Register in Tool Catalog**: In `INTENT_TOOL_SCHEMAS` in `app/features/intent_detection/schemas.py`:
   ```python
   INTENT_TOOL_SCHEMAS[ai_constants.FUNCTION_NEW_INTENT] = (
       NewIntentArgs,
       "High-level routing explanation for Luna intent classification.",
   )
   ```
4. **Implement Handler Logic**: Add a case handler in `IntentDetectionHandler.handle_function_calls` in `app/features/intent_detection/handler.py`:
   ```python
   case ai_constants.FUNCTION_NEW_INTENT:
       tool_call_response = await new_feature_handler.process_request(args, request_data)
   ```

### 6.2. Adding a New AI Provider

1. **Create Adapter**: Create a new class under `app/ai/providers/<provider_name>/client.py` inheriting from `app.ai.providers.base.AIProvider`.
2. **Implement Generate**: Implement `async def generate(self, ...) -> AIResponse`. Ensure that:
   - Tool calls are normalized into `list[ToolCall]`.
   - Token usage is mapped into `Usage(input_tokens, output_tokens, cached_input_tokens)`.
   - `provider` is tagged with the provider name.
3. **Register in Catalog**: Add model IDs, pricing definitions, and token limits in `app/ai/registry.py`.
4. **Attach to Router**: Wire the new provider into `app/ai/router.py`.

### 6.3. Adding a Database Query & Repository Method

1. **Define SQL**: Add the parameterized SQL query string in `app/db/mysql/queries/<domain>_sql.py`.
2. **Add Repository Method**: In `app/db/mysql/repositories/<domain>_repo.py`:
   ```python
   async def fetch_record(self, record_id: int):
       query = domain_sql.SELECT_RECORD_BY_ID
       params = {"record_id": record_id}
       return await self.db_connector.execute(db_config.DB_STREAMS, query, params)
   ```

---

## 7. Development & Operations Runbook

### 7.1. Environment Setup
Create a `.env` file in the project root containing:
```env
# Server
PORT=5006
HOST=0.0.0.0
ENVIRONMENT=development

# AI Provider Keys
OPENAI_API_KEY=sk-...

# JWT Secrets
JWT_SECRET_KEY=your-super-secret-key-min-32-chars
JWT_ALGORITHM=HS256

# External Services
GATEKEEPER_API_URL=https://gatekeeper.example.com/api/file
GATEKEEPER_API_FALL_BACK_URL=https://gatekeeper-fallback.example.com/api/file
GATEKEEPER_AUTH_TOKEN=your-gatekeeper-token

# Database Credentials
# (See app/core/configs/mysql_config.py for mapped variables)
```

### 7.2. Package & Environment Management (using `uv`)
- **Install / sync all dependencies**:
  ```bash
  uv sync
  ```
- **Add a new package**:
  ```bash
  uv add <package_name>
  ```
- **Clean pycache**:
  ```bash
  find . -name "__pycache__" -type d -exec rm -rf {} +
  ```

### 7.3. Running the Server

- **Local Development (Hot Reloading)**:
  ```bash
  uv run fastapi dev -e app.main:app --host 0.0.0.0 --port 5006
  ```

- **Production Daemon (via Gunicorn)**:
  ```bash
  nohup uv run gunicorn -c gunicorn_config.py app.main:app 2>&1 &
  ```

- **Health Verification**:
  ```bash
  curl http://localhost:5006/health
  # Expected output: {"status":"ok"}
  ```

---

## 8. Summary of Engineering Invariants

1. **Safety & Security First**: All endpoints requiring authentication must go through `JWTAuthMiddleware`. Never log raw auth tokens or sensitive customer message content in unmasked formats.
2. **LangChain-Free Simplicity**: Derive OpenAI tools from Pydantic models. Avoid heavy agent frameworks.
3. **Provider Agnostic**: Keep business logic separated from LLM vendor SDKs via `ModelRouter` and `AIResponse`.
4. **Billing Accountability**: Every model invocation must flow through `ModelRouter` to guarantee token auditing.
5. **Zero Blocking Calls**: Use async I/O exclusively across the entire codebase.
