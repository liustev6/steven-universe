# Spec: files and mounts (how a sandbox sees and saves your files)

**Status: milestone 2, proposed** (2026-11-09; revised the same day: read-only mounts, a local work
area and explicit publishing). Part of [agent-service](README.md).
Related: [file-management.md](../file-management.md), [auth-service.md](../auth-service.md),
[sandbox-runner.md](sandbox-runner.md), [agent-runtime.md](agent-runtime.md).

## What it is
A session **reads** the files it's allowed to see as an ordinary folder, **works** on local disk, and
**saves** results back to S3 only through an explicit, checked publish step. Nothing in the sandbox
holds a storage credential or a real token, and file-management still checks every access.

```
/workspace/
├── inputs/    read-only: the session's S3 locations, mounted (lazy, fetched on read)
├── work/      read-write local disk: scratch, edits, intermediate files; gone with the sandbox
└── outputs/   read-write local disk: results; saved to S3 only by "publish"
```

**The rule behind it:** reading from storage is lazy and harmless, so mount it. Writing to storage
from code that may be compromised is the risky direction, so make every write explicit, checked and
audited.

**Why not edit S3 in place:** S3 isn't a POSIX filesystem: no in-place edits, no file locking, and a
rename is a copy and a delete. Tools that edit files (editors, `git`, `sed -i`) behave badly on an
S3 mount. Local disk behaves like a disk.

## Reading: the read-only mount
This is the pattern Kubernetes uses to mount S3 into pods (the Mountpoint-for-S3 CSI driver): a mount
process **next to** the workload does the mount, and the workload just sees a directory.

```
sandbox (gVisor, no capabilities)            mounter (sidecar container, same internal network)
  /workspace/inputs/… ◀── bind mount (ro) ── rclone mount ──▶ egress-proxy ──▶ gateway ──▶ file-management
  just sees a folder                           (FUSE)          (exit door)     (credentials)  (checks the grant)
```

| Hop | What it is | Holds | Why it exists |
|---|---|---|---|
| **rclone mount** (in the mounter) | rclone, a widely used open-source storage tool. `rclone mount` uses FUSE (Filesystem in Userspace) to present remote files as a directory; a read becomes an HTTP request. | the session capability only | FUSE needs privileges the sandbox doesn't have (no capabilities, gVisor), so a small trusted sidecar does it |
| **egress-proxy** | the only other member of the sandbox network, which has no route out ([sandbox-runner.md](sandbox-runner.md) R5) | nothing | the single exit: it forwards only to the gateway, and stamps each request with its sandbox |
| **gateway** (part of agent-service, a separate machine) | checks the capability and the session's file rules, swaps in the real token, forwards to file-management | the session's real file-management token | the only place real credentials live; it never runs untrusted code |

**One read, end to end:**
```
agent: cat /workspace/inputs/private/pi-server/notes/todo.md
  → rclone (mounter) → egress-proxy (+ "from sandbox-3") → gateway (capability ok; read allowed on
    private/pi-server/notes/; swaps in token T2) → file-management (T2 grants read) → RustFS
  ← the bytes, back the same way; rclone caches them for repeated reads
```

## Saving: publish
```
agent: publish("outputs/report.pdf", to="private/pi-server/reports/2026-11-report.pdf")
  → runtime → gateway: POST /publish {path, destination}
  → gateway checks: the session may write there? within size limits? type allowed by the profile?
  → gateway reads the file from the sandbox's outputs/ (through the runtime) and uploads it
    with T2 through file-management
  ← client event (protocol B): artifact.created {path, destination, size, sha256}
```
- **Publishing is a tool the agent calls,** available to profiles that may save. It isn't a
  filesystem write. The agent learns the destination's rules from its profile ("reports go to …").
- **The checks happen at the gateway,** not in the sandbox: write grant, destination prefix,
  maximum size, and allowed types per profile. Anything sensitive (overwriting an existing object,
  writing outside the usual folder) can require your confirmation (G12).
- **Every saved object is audited twice:** `artifact.created` in the session's events, and
  file-management's audit line (*agent-service on behalf of the requester*).

## Decisions
| ID | Decision | Why |
|---|---|---|
| F1 | **Three areas:** `inputs/` (read-only S3 mounts), `work/` (local scratch), `outputs/` (local results). | Reading is lazy and natural; editing happens on a real disk; writing to storage is deliberate. |
| F2 | **A mounter sidecar does the mounts,** not the sandbox. The runner starts one per sandbox (a small image with rclone) on the same internal network, through the same egress proxy, and bind-mounts its FUSE mounts into the sandbox, **read-only**. | FUSE needs `/dev/fuse` and admin privileges; the sandbox keeps none (R4). The mounter runs only trusted code (rclone). |
| F3 | **file-management stays the only S3 client** (its D1, D3). Every mounted read and every publish is a file-management request, with a grant check and an audit line. | One gatekeeper for storage; nothing on the VM has S3 or RustFS keys. |
| F4 | **Reads use a WebDAV view in file-management** (RFC 4918: the standard "files over HTTP"). Mounts are read-only, so it needs only `PROPFIND` (list) and `GET` (with ranges), mapped onto the same `authorize()`, buckets and key rules as `/objects`. The mounter is plain `rclone mount` with its WebDAV backend. | An off-the-shelf, mature client (caching, retries, partial reads). Read-only keeps the new surface in file-management small. |
| F5 | **What a session may see and save is decided by agent-service:** the profile's locations **intersected** with the requester's access. file-management only serves; it has no concept of mounts or sessions. The agent can **request**, never **grant** itself, access. | Policy lives in the control plane. |
| F6 | **Writes to S3 happen only through `publish`,** checked at the gateway (grant, destination, size, type, confirmation if sensitive). No read-write mounts. | Writing from code that may be compromised is the risky direction; making it explicit makes it checkable and auditable. |
| F7 | **Inputs are fixed at session start** (milestone 2). **Later:** a `request_files` tool: the agent asks for another location with a reason, agent-service decides (asking you if the profile says so), and the runner adds it under `inputs/`. | Simple and auditable now; requests are the controlled way to grow, later. |

## Authentication chain
For "pi-server asks the agent to summarize its notes and save the summary":

| Step | Token | Held by |
|---|---|---|
| pi-server → agent-service | T1: `aud=agent-service`, `sub=service:pi-server` | pi-server |
| agent-service → auth-service: **exchange** T1 (grant 3, relay), asking for **less**: `read` on `private/pi-server/notes/`, `write` on `private/pi-server/reports/` | T2: `aud=file-management`, `sub=service:pi-server`, `act=agent-service`, the narrowed grants | **the gateway only** |
| mounter and runtime → gateway | the session capability | the sandbox and its mounter |
| gateway → file-management (mounted reads, publishes) | T2 | the gateway |

- **The sandbox never holds a real token.** A leaked capability works only from its own sandbox,
  through the gateway, for that session's locations, until the session ends.
- **file-management sees the truth:** *agent-service acting for pi-server*, with grants that can't be
  widened (auth-service A7: tokens only narrow).
- **Expiry is hidden from the mount.** T2 lives 10 minutes; the gateway re-exchanges behind the
  stable capability. Long background tasks use a **downscoped** token instead (grant 4, up to 1 hour),
  which needs auth-service's downscope gap fixed first ([auth-service.md](../auth-service.md), Known
  issues).
- **Changes needed in auth-service:** agent-service becomes a client with `can_relay_clients`, plus the
  audiences `agent-service` and `sandbox-runner`. Requesters' grants are unchanged.

## Not chosen
| Option | Why not |
|---|---|
| Read-write S3 mounts (this spec's first version) | S3 isn't a POSIX filesystem (no in-place edits, locks, or real renames), and writes from untrusted code would go straight to storage, unchecked |
| Copying all inputs in at start | slow for large folders, and a snapshot that's stale at once; kept in mind for small inputs |
| rclone or `mountpoint-s3` straight to RustFS | S3 credentials on the VM, and a bypass of file-management's grants and audit |
| Temporary S3 credentials scoped by policy (STS-style) | depends on RustFS supporting it, and still bypasses file-management's model; code holding temporary credentials can still use them |
| A custom FUSE driver over `/objects` | no change to file-management, but a filesystem of our own to maintain. **The fallback** if a WebDAV view turns out wrong. |
| FUSE inside the sandbox | needs privileges inside the sandbox; gVisor's FUSE support is limited |

## Milestone 2 plan
| Piece | Where | Testable alone? |
|---|---|---|
| WebDAV read view (`/dav/{bucket}/…`: `PROPFIND`, `GET` with ranges; the same grant checks) | file-management | **yes**: unit tests, then `rclone` against it with a test client |
| Mounter image (rclone; a mount script for the session's read locations) | `python/services/sandbox-runner/mounter/` or its own folder | yes: on the test VM with a test token |
| The gateway's files routes: WebDAV reads → file-management with T2; `POST /publish` with the checks; the `artifact.created` client event | the dev gateway first, then agent-service | with the dev gateway |
| `publish` as a tool in the runtime (an MCP tool, served by the gateway or the runtime, that Codex calls) | agent-runtime | yes |
| Token exchange for a session (relay, narrowed) | the dev gateway first (a dev client `agent-dev` with `can_relay_clients`), then agent-service | yes, against the live auth-service |
| Runner support (mounter per sandbox, the read-only bind mount, local `work/` and `outputs/`) | sandbox-runner, or a script on the test VM | on the test VM |

**What milestone 2 must answer:**

| # | Question |
|---|---|
| M1 | Does a FUSE mount from the sidecar show up inside a **gVisor** sandbox reliably (bind-mount propagation through gVisor's file proxy)? Fallback: inputs copied in at start for small sets. |
| M2 | Speed: listing a folder of 1,000 files; reading a 100 MB file. |
| M3 | Read-only is enforced: writes to `inputs/` fail in the sandbox, and file-management would refuse them anyway. |
| M4 | Publish checks: a publish outside the write grant, over the size limit, or of a refused type is refused at the gateway, with a clear error to the agent. |
| M5 | Token renewal: a session longer than 10 minutes keeps reading with no error visible to rclone. |

**Done when:** a sandbox on the test VM reads `inputs/private/agent-dev/…`, works in `work/`, and
publishes `outputs/report.md` to `private/agent-dev/reports/`; a publish to a forbidden location is
refused; file-management's audit shows *agent-service (dev) on behalf of the requester* for every
access; and nothing on the VM ever held a real token.
