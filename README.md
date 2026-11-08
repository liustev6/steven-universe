# Steven Universe - Monorepo for Steven

My personal monorepo — a unified space that brings together my work across AI, software development, GPU computing, cloud infrastructure, and full-stack applications.

## Project Structure

```
steven-universe/
├── js/
│   └── apps/
│       └── personal-website/     # React-based personal portfolio website
├── python/
│   ├── libs/
│   │   └── shared-schemas/       # Shared Pydantic schemas for API contracts
│   ├── services/
│   │   ├── file-management/      # File management microservice (MinIO/S3)
│   │   ├── web-gateway/          # Backend API gateway
│   │   ├── stevenai-service/     # RAG-powered AI chatbot
│   │   └── gpu-server/           # GPU task execution service
│   └── workers/
│       └── gpu-server/           # GPU worker container images
└── .github/
    └── workflows/                # CI/CD pipelines
```

## Projects

### Personal Website

Portfolio website showcasing my projects, skills, and experience.

**Main Features:**
- Interactive 3D graphics and animations
- AI-powered chatbot
- Project showcase with video demos
- ML model integrations (food classification, land sink prediction)

**Path:** `js/apps/personal-website/`
**Live:** https://liustev6.ca

---

### File Management Service

The only service that talks to object storage (RustFS, S3 API). LAN only; other services call it
with auth-service tokens.

**Main Features:**
- One generic `/objects/{bucket}/{key}` API (PUT, GET, HEAD, DELETE, list)
- Access from the caller's token: a scope plus a `file_access` grant on a prefix, checked by one `authorize()`
- Buckets `public` and `private` (`private/<client>/…` per service)
- Streaming uploads (multipart, SHA-256) and downloads (`Range`, `If-None-Match`)
- Hardened Docker Compose, secrets as files, TLS (`files.lan.liustev6.ca`)

**Path:** `python/services/file-management-service/`

### Web gateway (API Gateway)

FastAPI-based API Gateway that routes requests to specialized microservices.

**Main Features:**
- Proxmox server monitoring and statistics
- API gateway routing to downstream microservices
- Health checks for all connected services
- CORS configuration for frontend integration
- Docker + LXC deployment with automated sync

**Current Migration Status:**
- ✅ Phase 1: Proxmox stats API (completed)
- ✅ Phase 2: StevenAI chatbot service (completed)
- 🚧 Phase 3: Food101 classification service (planned)
- 🚧 Phase 4: Landsink prediction service (planned)

**Architecture:**
```
Frontend → Web Server (Gateway) → Specialized Microservices
                                → File Management Service
                                → GPU Service
                                → Proxmox API
```

**Path:** `python/services/web-server/`

---

### StevenAI Service

RAG-powered AI chatbot that answers questions about Steven using semantic search over personal documents and Q&A pairs.

**Main Features:**
- FAISS vector search with BGE-large-en-v1.5 embeddings
- Multiple models: OpenAI GPT-4o-mini, Qwen, Llama (via GPU service)
- SSE streaming responses with character buffering
- Follow-up question rewriting for contextual queries
- Two RAG datasets: documents and Q&A pairs

**Example Usage:**
```bash
# Query with GPT-4o-mini
curl -X POST http://localhost:8002/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "What projects has Steven worked on?",
    "use_docs_of_fact": true,
    "use_qa_pairs": true
  }'

# Use Qwen model for local inference
curl -X POST http://localhost:8002/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "Tell me about Steven",
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "use_docs_of_fact": true
  }'
```

**Technology Stack:**
- FastAPI with SSE streaming
- OpenAI API (GPT-4o-mini)
- SentenceTransformers for embeddings
- FAISS for vector similarity search
- GPU Service integration for Qwen/Llama

**Path:** `python/services/stevenai-service/`

---

### GPU Service

Session-based GPU task execution service with real-time event streaming and automatic resource management.

**Main Features:**
- **Difficulty-based GPU routing**: Route tasks to appropriate GPUs (low/high difficulty)
- **Pre-defined task execution**: YAML-based configuration for task definitions and Docker configs
- **SSE streaming**: Real-time event streaming with structured events (CONNECTION, WORKER, TEXT_DELTA, LOGS, TASK_FINISH)
- **Automatic model caching**: Downloads models from file-service on demand
- **Session management**: Long-lived containers with idle timeouts and max lifetime limits
- **Docker-outside-of-Docker (DOOD)**: Service creates sibling containers with GPU passthrough

**Architecture:**
```
Client → GPU Service → GPU Manager (allocate by difficulty)
                    → Docker Manager (create containers with GPU)
                    → Model Downloader (fetch from file-service)
                    → Task Manager (track running tasks)
                    └→ GPU Workers (Docker containers with NVIDIA GPU access)
```

**Technology Stack:**
- FastAPI with async I/O and SSE streaming
- Docker SDK for container orchestration
- PYNVML for GPU metrics monitoring
- NVIDIA CUDA for GPU passthrough

**Path:** `python/services/gpu-server/`
**Workers:** `python/workers/gpu-server/`
**Documentation:** See `python/services/gpu-server/README.md` and `SERVER_DESIGN.md`