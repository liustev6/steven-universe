> **Archived (2026-11-17).** This service is not deployed and not maintained. pve01's one GPU now
> serves only the voice services ([speech-service](../../../docs/specs/speech-service.md)). A general GPU
> service will be redesigned when the lab has several GPUs; this document is kept as a reference.

# GPU Service Architecture

A session-based GPU execution system with bounded queues, automatic model reuse, SSE streaming, and dynamic GPU allocation.  
This architecture is designed for high-throughput inference workloads where models are expensive to load and long-lived GPU workers significantly reduce latency.

---

## 1. High-Level Architecture

The GPU Service acts as a central coordinator, scheduler, and router for all GPU tasks.
It manages session workers (long-lived GPU containers), model caching, and request routing.

```
┌─────────────────────────────────────────────────────────┐
│                   Client (Web Server)                   │
└────────────────────┬────────────────────────────────────┘
                     │ POST /api/tasks/predefined (SSE)
                     ↓
┌────────────────────────────────────────────────────────┐
│                   API Layer (FastAPI)                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ tasks.py    │  │ sessions.py  │  │ health.py      │ │
│  └──────┬──────┘  └──────┬───────┘  └────────┬───────┘ │
└─────────┼─────────────────┼──────────────────┼─────────┘
          │                 │                  │
          ↓                 ↓                  ↓
┌─────────────────────────────────────────────────────────┐
│            Singleton Managers (app/core/manager/)       │
│            Global instances, one per service            │
│                                                         │
│  ┌────────────────────────────────────────────────┐     │
│  │          Task Manager (State Tracker)          │     │
│  │  • Track running tasks (task_id → metadata)    │     │
│  │  • Provides monitoring and status queries      │     │
│  │  • No orchestration logic (that's in handlers) │     │
│  └────────────────┬───────────────────────────────┘     │
│                   │                                     │
│  ┌────────────────┼───────────────────────────────┐     │
│  │  GPU Manager   │                               │     │
│  │  • Allocate GPU by difficulty (low/high)       │     │
│  │  • Track availability (is_available flag)      │     │
│  │  • Monitor metrics (memory, temp, utilization) │     │
│  └────────────────┼───────────────────────────────┘     │
│                   │                                     │
│  ┌────────────────┼───────────────────────────────┐     │
│  │ Session Mgr    │                               │     │
│  │  • Track sessions (id → Session mapping)       │     │
│  │  • Find idle sessions (model + task matching)  │     │
│  │  • Manage FIFO queues (per session)            │     │
│  │  • SessionDispatcher: Background task dequeue  │     │
│  └────────────────┼───────────────────────────────┘     │
│                   │                                     │
│  ┌────────────────┼───────────────────────────────┐     │
│  │ Docker Mgr     │                               │     │
│  │  • Create session containers                   │     │
│  │  • Monitor health checks                       │     │
│  └────────────────┼───────────────────────────────┘     │
│                   │                                     │
│  ┌────────────────┼───────────────────────────────┐     │
│  │ Model Download │                               │     │
│  │  • Check local cache (/data/models/{id})       │     │
│  │  • Fetch from file-service if missing          │     │
│  │  • Extract tar.gz archives                     │     │
│  │  • Return host path for volume mount           │     │
│  └────────────────┼───────────────────────────────┘     │
└───────────────────┼─────────────────────────────────────┘
                    │
                    ↓
┌─────────────────────────────────────────────────────────┐
│       Per-Request Instances (app/core/instance/)        │
│       One instance per task request                     │
│                                                         │
│  ┌────────────────────────────────────────────────┐     │
│  │     Session Task Handler (session tasks)       │     │
│  │  1. Load config → ConfigLoader                 │     │
│  │  2. Find/create session → SessionManager       │     │
│  │  3. Download model → ModelDownloader (if new)  │     │
│  │  4. Allocate GPU → GPUManager (if new)         │     │
│  │  5. Create container → DockerManager (if new)  │     │
│  │  6. Wait for health → WorkerHealthClient       │     │
│  │  7. Enqueue task → SessionManager              │     │
│  │  8. Wait for dispatcher to dequeue             │     │
│  │  9. Send task → SessionWorkerClient (HTTP)     │     │
│  │  10. Stream SSE events back to client          │     │
│  │  11. Mark session WAITING when done            │     │
│  └────────────────────────────────────────────────┘     │
│                                                         │
│  ┌────────────────────────────────────────────────┐     │
│  │ Config Loader (used by both handlers)          │     │
│  │  • Load YAML files (definitions/actions/paths) │     │
│  │  • Merge task definition + action + model path │     │
│  │  • Apply request overrides                     │     │
│  └────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────┘
                    │
                    ↓
┌────────────────────────────────────────────────────────┐
│              HTTP Clients (app/clients/)               │
│              Used by session task handler only         │
│                                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │  WorkerHealthClient                            │    │
│  │  • Poll /health endpoint until 200             │    │
│  │  • DNS: http://gpu-session-{id}:8000/health    │    │
│  └────────────────────────────────────────────────┘    │
│                                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │  SessionWorkerClient                           │    │
│  │  • POST /task with task payload                │    │
│  │  • Stream SSE events from worker response      │    │
│  │  • DNS: http://gpu-session-{id}:8000/task      │    │
│  └────────────────────────────────────────────────┘    │
└───────────────────┼────────────────────────────────────┘
                    │
                    ↓
┌────────────────────────────────────────────────────────┐
│          Worker Containers (Docker)                    │
│          Session-based workers only                    │
│                                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │  Session Workers (Long-lived, HTTP servers)    │    │
│  │  • FastAPI HTTP server on port 8000            │    │
│  │  • Endpoints: GET /health, POST /task, POST /stop │
│  │  • Named: gpu-session-{container_id[:12]}      │    │
│  │  • Network: gpu-network (DNS resolution)       │    │
│  │  • GPU: --gpus device=N                        │    │
│  │  • Model: /data/models/{id} → /models (ro)     │    │
│  │  • Env: MODEL_PATH=/models                     │    │
│  │  • Outputs: SSE events over HTTP response      │    │
│  │  • Lifecycle: Reused across multiple requests  │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────┘
```


## 2. Key Features

### Session-Based Execution
Workers are long-lived GPU containers with loaded models.  
Tasks for the same `model_id` reuse the session to avoid repeated loading costs.

### Bounded Queue per Session
Each session maintains a FIFO queue with a configurable maximum:

```
max_queue: configurable
```

### One Active Task per Session
A session never executes more than one task simultaneously, ensuring GPU memory stability.

### Automatic Model Fetching
Models are downloaded from file-service on demand and cached locally.

### Server-Sent Events (SSE)
The GPU service relays all worker output through a unified SSE channel.

### Task Difficulty Routing
Tasks can target specific GPUs (e.g., low difficulty vs. high difficulty) using `GPU_DEVICE_DIFFICULTY`.

---

## 3. Session State Machine

```
INITIALIZING
      ↓
    READY
      │
      │ new task arrives
      ▼
 ENQUEUE (queue_len ≤ max_queue)
      │
      │ dequeue next
      ▼
  WORKING  ─────────────► ENQUEUE
      │                     ▲
      │                     │ queue_len ≤ max_queue
      ▼                     │
    READY ◄─────────────────┘
      ↓
 TERMINATED
```

### Guarantees

✔ Never more than one active task  
✔ Queue never exceeds `max_queue`  
✔ If queue is full:  
- GPU free → new session  
- GPU busy → reject with `503`

---

## 4. Dispatch Logic

```
task arrives
  │
  ▼
find existing session for model
  │
  ├── if queue_len < max_queue → enqueue
  │
  └── if queue_full:
  │         ├─ if gpu free → create new session
  │         └─ else → 503 reject
  │
  ▼
Session Dispatcher executes tasks sequentially
```

### Behavior Summary

- Reuse a session if possible  
- Only one active execution per session  
- Sessions scale horizontally when needed  
- System rejects overload instead of creating unlimited queues  

---

## 5. Concurrency Rules

| Behavior | Allowed | Not Allowed |
|---------|---------|-------------|
| Queue tasks inside a session | ✔ | — |
| Parallel execution inside a single session | ❌ | GPU memory unsafe |
| Create new session when queue full & GPU free | ✔ | — |
| Create new session when queue full & GPU busy | ❌ | 503 |

---

## 6. SSE Routing

Workers never stream directly to clients.

```
Worker → GPU Service → Client
```

This ensures:
- stable SSE protocol  
- unified logging  
- secure routing  
- consistent event formatting  

---

## 7. DNS & Addressing

Session workers are addressed by container name, not IP:

```
gpu-session-{uuid}.internal:8000
```

DNS is provided by a custom Docker network.

---

## 8. Health Readiness

Workers must return:

```
{ "status": "READY" }
```

HTTP 200 alone is not sufficient; the service waits for a READY status before scheduling tasks.

---

## 9. Configuration

```
session:
  max_queue: configurable
  allow_model_reuse: true
  enforce_single_exec: true
  health_ready_required: true
  idle_timeout_seconds: 300
  max_lifetime_seconds: 3600
```

---

## 10. Session Task Flow

### Cold Start
1. No existing session found  
2. Model downloaded if missing  
3. GPU allocated  
4. Worker container started  
5. Wait for /health to return READY  
6. Session enters READY state  
7. First task enqueued  

### Subsequent Requests
- If queue < max_queue → enqueue  
- If queue = max_queue:  
  - GPU free → create new session  
  - GPU busy → reject  

### Execution
- Worker processes tasks one-by-one  
- GPU Service relays SSE to client  

### Termination
- Idle timeout  
- Max lifetime  
- Manual deletion  

---

## 11. Request Flow Diagram

```
Client  
  │ task
  ▼
GPU Service  
  │
  ├─ Find matching session
  │     ├─ queue < max_queue → enqueue
  │     └─ queue full:
  │            ├─ GPU free → new session
  │            └─ reject (503)
  │
  ▼
Session Dispatcher  
  │
  └→ POST /task to session worker  
        │
        └→ stream SSE back to client
```

---

## 12. Worker Types

### Session Workers (long-lived)
- Expose /health, /task, and /stop endpoints
- Handle multiple sequential tasks
- Use bounded queues
- Persistent containers with model reuse

---

## 13. Summary

This GPU service architecture provides:
- Efficient model reuse  
- Deterministic and safe execution (1 active task per session)  
- Horizontal scaling controlled by GPU availability  
- Bounded queues for fairness and overload protection  
- Clean SSE streaming via a central coordinator  
- Automatic model fetching and caching  
- Unified DNS-based addressing for session workers  

This combined system forms a robust, scalable GPU inference backend suitable for LLMs, multimodal models, fine-tuning pipelines, and high-throughput GPU workloads.
