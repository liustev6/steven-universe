# StevenAI Service - RAG-Powered AI Chatbot

AI chatbot service with RAG (Retrieval-Augmented Generation) that answers questions about Steven Liu using semantic search over personal documents and Q&A pairs.

**Path**: `python/services/stevenai-service/`

## Overview

This service provides an intelligent chatbot that can answer questions about Steven's background, projects, and experience. It uses:
- **RAG** with FAISS vector search and BGE embeddings
- **Multiple models**: OpenAI GPT-4o-mini, Qwen, or Llama (via GPU service)
- **Streaming responses** with SSE (Server-Sent Events)
- **Follow-up rewriting** for contextual questions

### Architecture

```
┌──────────────────────────────┐
│      StevenAI Service        │
│       (This Service)         │
└───────┬──────────────────────┘
        │
    ┌───┴────┬──────────────┐
    ▼        ▼              ▼
┌────────┐ ┌─────────┐ ┌──────────┐
│ OpenAI │ │   RAG   │ │   GPU    │
│GPT-4o  │ │ FAISS   │ │ Service  │
│  mini  │ │  BGE    │ │Qwen/Llama│
└────────┘ └─────────┘ └──────────┘
```

## Features

✅ **RAG with FAISS**
- Semantic search over documents and Q&A pairs
- BGE-large-en-v1.5 embeddings
- Top-3 context retrieval

✅ **Multiple Models**
- OpenAI GPT-4o-mini (default)
- Qwen/Qwen2.5-7B-Instruct (via GPU service)
- meta-llama/Llama-3.2-3B-Instruct (via GPU service)

✅ **Smart Follow-ups**
- Automatically rewrites follow-up questions as standalone queries
- Improves RAG retrieval for contextual questions

✅ **Streaming Responses**
- Server-Sent Events (SSE) for real-time streaming
- Character-level buffering (5+ chars) for smooth UX

## API Endpoints

### Chat Stream

```bash
# Basic query with GPT-4o-mini
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "query": "What projects has Steven worked on?",
    "use_docs_of_fact": true,
    "use_qa_pairs": true
  }'

# Use Qwen model
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "query": "Tell me about Steven'\''s experience",
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "use_docs_of_fact": true
  }'

# Follow-up question (with context)
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "query": "What about his education?",
    "last_q": "Tell me about Steven",
    "last_a": "Steven is a software engineer...",
    "use_qa_pairs": true
  }'
```

### Request Parameters

```json
{
  "query": "User question (required)",
  "last_q": "Previous question (optional, for follow-ups)",
  "last_a": "Previous answer (optional, for follow-ups)",
  "model": "chatGPT | Qwen/Qwen2.5-7B-Instruct | meta-llama/Llama-3.2-3B-Instruct",
  "use_docs_of_fact": true,  // Include document corpus
  "use_qa_pairs": true,       // Include Q&A pairs
  "temperature": 0.7,
  "max_tokens": 512
}
```

### SSE Stream Format

The response streams JSON events:

```javascript
// Context retrieval
data: {"type":"context","contexts":[{"content":"...","score":0.85,"metadata":{...}}]}

// Streaming text deltas
data: {"type":"delta","delta":"Steven"}
data: {"type":"delta","delta":" is a"}
data: {"type":"delta","delta":" software"}

// Stream complete
data: {"type":"done","full_text":"Steven is a software engineer..."}

// Error (if any)
data: {"type":"error","error":"Error message"}
```

### Health Check

```bash
# Check service health
curl http://localhost:8000/health

# Response
{
  "status": "healthy",
  "service": "StevenAI Service",
  "version": "1.0.0"
}
```

## RAG Data

### Documents Dataset (`rag_document.json`)
- Personal background, education, work experience
- Projects, skills, achievements
- Manually curated for accuracy

### Q&A Pairs Dataset (`rag_qa.json`)
- Generated from documents at build time
- Question-answer pairs for common queries
- Improves retrieval for direct questions

### Search Strategy
```python
# Top-3 from each enabled dataset
results = []
if use_docs_of_fact:
    results += search_documents(query, top_k=3)
if use_qa_pairs:
    results += search_qa_pairs(query, top_k=3)
```

## Development Setup

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Edit configuration
nano .env

# Run service
uvicorn app.main:app --reload --port 8000
```

### Environment Variables

```bash
# Application
APP_NAME=StevenAI Service
APP_VERSION=1.0.0
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:3000,http://localhost:3001

# OpenAI Configuration
OPENAI_API_KEY=sk-your-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# GPU Service (for Qwen/Llama models)
GPU_SERVICE_URL=http://192.168.50.49:8001
GPU_SERVICE_API_KEY=your-key
GPU_MODELS=Qwen/Qwen2.5-7B-Instruct,meta-llama/Llama-3.2-3B-Instruct

# RAG Configuration
RAG_DOCS_PATH=app/data/rag_document.json
RAG_QA_PATH=app/data/rag_qa.json
RAG_INDEX_DIR=app/data/faiss_indexes
RAG_EMBEDDING_MODEL=BAAI/bge-large-en-v1.5
RAG_TOP_K=3

# Streaming
STREAM_BUFFER_SIZE=5
STREAM_FLUSH_TIMEOUT=0.5

# Security
INTERNAL_API_KEY=your-secure-key
```

## Deployment

### Prerequisites
- LXC container with Docker installed
- SSH key authentication configured
- rsync installed locally
- Local `.env` file configured

### Deploy to LXC

```bash
# Run deployment script (loads this directory's .env)
infra/deployment/stevenai-service.sh   # from the repo root
```

**What happens:**
1. 📦 Syncs code and shared-schemas to LXC
2. 📄 Syncs RAG document
3. 🔨 Builds Docker image (includes RAG QA generation)
4. 🛑 Stops old container
5. 🚀 Starts new container on port 8000
6. 🏥 Verifies health endpoint

**Useful commands:**
```bash
# View logs
ssh your-lxc-host 'docker logs -f stevenai-service'

# Restart service
ssh your-lxc-host 'docker restart stevenai-service'
```

## Project Structure

```
python/services/stevenai-service/
├── app/
│   ├── main.py                     # FastAPI app with RAG initialization
│   ├── core/
│   │   ├── config.py              # Pydantic Settings
│   │   └── dependencies.py        # Auth, HTTP client
│   ├── api/
│   │   ├── health.py              # Health check
│   │   └── chat.py                # Chat streaming endpoint
│   ├── services/
│   │   ├── rag_service.py         # FAISS + SentenceTransformer
│   │   ├── model_router.py       # Route to OpenAI/GPU
│   │   └── stream_buffer.py      # Character buffering
│   ├── clients/
│   │   └── gpu_client.py          # GPU service client
│   └── data/
│       ├── rag_document.json      # Document corpus
│       ├── rag_qa.json            # Generated Q&A pairs
│       └── faiss_indexes/         # FAISS indexes (runtime)
├── scripts/
│   └── generate_rag_qa.py         # Build-time QA generation
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

## Shared Schemas

This service uses type-safe Pydantic schemas from `python/libs/shared-schemas/`.

### Defined Schemas (`shared_schemas/stevenai_service.py`)

**Request/Response:**
- `ChatRequest` - Streaming chat request
- `ChatResponseChunk` - SSE event chunks
- `RAGContext` - Retrieved context document
- `ChatChunkType` - Event types enum

### Usage

```python
from shared_schemas.stevenai_service import (
    ChatRequest,
    ChatResponseChunk,
    ChatChunkType,
    RAGContext
)

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    # Request validated automatically
    yield ChatResponseChunk(
        type=ChatChunkType.DELTA,
        delta="Hello"
    ).model_dump_json()
```

## Tech Stack

- **FastAPI** - Async web framework
- **Uvicorn** - ASGI server
- **OpenAI** - GPT-4o-mini API
- **SentenceTransformers** - BGE embeddings
- **FAISS** - Vector similarity search
- **HTTPX + httpx-sse** - Async HTTP with SSE support
- **sse-starlette** - Server-Sent Events streaming
- **shared-schemas** - Type-safe API contracts

## Performance

- **RAG Search**: ~50ms (3 documents from 2 datasets)
- **First Token**: ~200ms (OpenAI) | ~500ms (Qwen/Llama)
- **Streaming**: 5+ character buffering for smooth UX
- **GPU Models**: Session-based (reuses loaded models)

## License

Part of the `steven-universe` monorepo.
