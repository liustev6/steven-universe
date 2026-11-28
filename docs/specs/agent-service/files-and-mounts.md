# Spec: files and mounts (how a sandbox sees and saves your files)

**Status: CP6, in progress.** Designed 2026-11-09 (milestone 2); **revised 2026-11-27** (6.2) for what
CP2–CP5 built (pods with their own proxy and workspace, the token exchange, the gateway's tools) and
the 6.1 spike's answers. Part of [agent-service](README.md); its build: [tracker.md](tracker.md) CP6.
Related: [file-management.md](../file-management.md), [auth-service.md](../auth-service.md),
[sandbox-runner.md](sandbox-runner.md), [agent-runtime.md](agent-runtime.md), [tools.md](tools.md).

## What it is
A session **reads** the files it's allowed to see as an ordinary folder, **works** on local disk, and
**saves** results back to storage only through an explicit, checked `publish` command. Nothing in the
sandbox holds a storage credential or a real token, and file-management still checks every access.

```
/workspace/                      the pod's own folder on VM 200's workspace disk (R13)
├── inputs/    read-only: the session's read locations, mounted (lazy, fetched on read)
│   └── private/pi-server/notes/…     each location at its own path (<bucket>/<prefix>)
├── work/      read-write local disk: scratch, edits, intermediate files; gone with the pod
└── outputs/   read-write local disk: results; saved to storage only by `publish`
```

**The rule behind it:** reading from storage is lazy and harmless, so mount it. Writing to storage
from code that may be compromised is the risky direction, so make every write explicit, checked and
audited.

**Why not edit storage in place:** S3 isn't a POSIX filesystem: no in-place edits, no file locking,
and a rename is a copy and a delete. Tools that edit files (editors, `git`, `sed -i`) behave badly on
an S3 mount. Local disk behaves like a disk.

## Reading: the read-only mount
The pattern Kubernetes uses to mount storage into pods (its CSI drivers, e.g. Mountpoint for S3): a
trusted helper **next to** the workload does the mount; the workload just sees a directory.

```
VM 200, one pod
├── mounter (Docker, /dev/fuse + SYS_ADMIN; runs only rclone; started at warm-up)
│     rclone mount (WebDAV) ──▶ the pod's proxy ──▶ gateway /dav ──▶ file-management /dav
│     the token: the session capability, read per request      (T2)          (checks T2)
│     mounted at a folder on the VM, bound `rshared`
└── sandbox (gVisor, no privileges; runs the agent)
      /workspace/inputs ◀── that folder, bound read-only
```

| Hop | What it is | Holds | Why it exists |
|---|---|---|---|
| **rclone** (in the mounter) | a widely used open-source storage tool; `rclone mount` uses FUSE (Filesystem in Userspace) to present remote files as a folder: a listing becomes a `PROPFIND`, a read a `GET` (with ranges) | the session capability, once claimed | FUSE needs `/dev/fuse` and the mount privilege, which the sandbox doesn't have (gVisor, no capabilities), so a small trusted sidecar does it |
| **the pod's proxy** | the pod's only way out ([sandbox-runner.md](sandbox-runner.md) R12): it forwards only to the gateway and stamps each request with its pod | nothing | the single exit, for the mounter as for the sandbox |
| **gateway** (agent-service, CT 107) | checks the capability and the pod's stamp (C7), shows the session its read locations, swaps in the real token, forwards to file-management | T2, the session's file-management token | the only place a real credential is used; it never runs untrusted code |
| **file-management** | its WebDAV view: `PROPFIND` and `GET`, on the same `authorize()` as `/objects` | its storage key | the only storage client (its D1, D3) |

**One read, end to end:**
```
agent: cat /workspace/inputs/private/pi-server/notes/todo.md
  → rclone (mounter) → proxy (+ "from pod 3") → gateway (capability ok, pod ok; the session may read
    private/pi-server/notes/ by T2's details; swaps in T2) → file-management (T2 grants read) → RustFS
  ← the bytes, back the same way; rclone caches listings for repeated reads
```

## Saving: `publish`, a command the agent runs
The sandbox image carries a small CLI. The agent runs it from its shell, like any other command:
```
publish put    outputs/report.md  private/agent-cli/reports/2026-11-report.md
publish delete private/agent-cli/reports/old-draft.md
```
```
publish (in the sandbox) → the pod's proxy → gateway: PUT/DELETE /files/…, the capability, the bytes
  → gateway checks: T2's details allow `write` (or `delete`) there? the size and type limits?
    overwriting allowed? (later, for sensitive writes: asked first, G12)
  → gateway → file-management with T2           → its audit: agent-service for the caller
  ← protocol B: artifact.created {path, destination, size, sha256} (artifact.deleted for delete)
```
- **The CLI holds no credential:** it reads the session capability already in the sandbox
  (`/run/session`) and talks only to the gateway, through the proxy. It sends the file's bytes; the
  gateway decides.
- **Off by default.** Only a profile with `write` (or `delete`) locations gets anything to publish to;
  its instructions (always, from what it was granted) tell the agent where and how. Running the CLI needs
  a shell (the profile's `tools: [shell]`); `pi` has none and stays read-only.
- **Every change is audited twice:** `artifact.created` / `artifact.deleted` in the session's events,
  and file-management's audit line (*agent-service on behalf of the caller*).

## Permissions: the profile's request, the caller's grant, the overlap
The same model as tools (tools.md TL4, TL5), in the same words as `policy.yaml`: RFC 9396
`authorization_details` of type `file_access`, `actions` and `locations`.

```yaml
# policy.yaml, the caller's grant (the ceiling, reviewed once):
pi-server:
  grants:
    file-management:
      scopes: [files:read, files:write, files:delete, files:list]
      authorization_details:
        - {type: file_access, actions: [read, write, delete, list], locations: ["private/${client}/"]}

# profiles.yaml, what a session of this profile asks for (none: no files at all):
files:
  - {actions: [read, list], locations: ["private/${client}/notes/"]}     # mounted at inputs/
  - {actions: [write],      locations: ["private/${client}/reports/"]}   # publish put
```

1. **agent-service** fills in `${client}` (the session's caller, as auth-service does in the policy)
   and asks the token exchange for exactly the profile's details; the scopes follow from the actions.
2. **auth-service** intersects them with the caller's grant (`grant_overlap`, RFC 6749 §3.3): the
   prefix rule (`private/pi-server/` covers `private/pi-server/notes/`), per action. Tokens only narrow.
   T2: `sub` = the caller, `act` = agent-service, `aud` = file-management, the overlap as its details.
3. **The gateway goes by T2's details, not the profile:** what the mount shows (`read`/`list`
   locations), where `publish put` and `publish delete` may go. The intersection lives in one place.
4. **file-management** checks T2 on every request.

A profile asking for more than the caller has gets the overlap, logged when it's smaller than asked;
an empty overlap is a session with no files.

## Decisions
| ID | Decision | Why |
|---|---|---|
| F1 | **Three areas:** `inputs/` (read-only mounts), `work/` (local scratch), `outputs/` (local results). `work/` and `outputs/` are the pod's own workspace (R13: its folder on VM 200's XFS disk, under a quota). | Reading is lazy and natural; editing happens on a real disk; writing to storage is deliberate. |
| F2 | **A mounter sidecar per pod does the mount,** started **at warm-up** with the pod's proxy (R10, R12): the stock rclone image (`rclone/rclone:1.69.1`), `/dev/fuse` and `SYS_ADMIN` only (and AppArmor unconfined: Docker's profile refuses `mount(2)`), in the pod's network, through its proxy. Its FUSE mount, at `/run/sandbox-runner/mounts/<pod>/inputs` on the VM (bound `rshared`), exists before the sandbox starts, which binds it read-only at `/workspace/inputs`. rclone reads its Bearer token per request (`--webdav-bearer-token-command`) from a `capability` file in the session directory: **empty from the warm-up** (rclone also reads it once at start, and a failing token command is fatal: the first deploy's pods never warmed), so nothing is read; **at the claim** the runner replaces it, then writes the session file, as before. **No cache refresh is needed:** nobody lists the mount before the claim (agent-runtime waits for its session file), so rclone has nothing stale (checked on VM 200, 2026-11-27: the files show 0 s after the token appears). | FUSE needs privileges the sandbox keeps none of (R4). Started at warm-up, the mount exists before the sandbox does, so no mount has to appear under a running sandbox, and the claim stays instant (the 6.1 spike and 6.6's check: on VM 200). |
| F3 | **file-management stays the only storage client** (its D1, D3). Every mounted read and every publish is a file-management request, with a grant check and an audit line. | One gatekeeper for storage; nothing on VM 200 has storage keys. |
| F4 | **Reads use WebDAV** (RFC 4918, the standard "files over HTTP"): only `PROPFIND` (list) and `GET` (with ranges), at file-management (`/dav/{bucket}/…`, mapped onto the same `authorize()`, buckets and key rules as `/objects`) and at the gateway (`/dav`). The mounter is plain `rclone mount` with its WebDAV backend. | An off-the-shelf, mature client (caching, retries, partial reads); read-only keeps the new surface small. |
| F5 | **Every mounter is identical;** the gateway's `/dav` shows each session only its own read locations, as folders at their own paths (`private/pi-server/notes/`). The runner never learns a session's files. | Policy lives in the control plane (agent-service); the runner carries out (R1). |
| F6 | **Permissions as for tools:** the profile's `files` (RFC 9396 `file_access` details, `${client}` = the caller) asked for at the token exchange; auth-service gives the overlap with the caller's grant (`grant_overlap` at the `file-management` audience); the gateway acts on T2's details only; file-management checks T2. agent-service relays to `file-management` too (its `relay_audiences`). The agent can **request**, never **grant** itself, access. | One model for everything a session reaches; the ceiling is the caller's grant, reviewed in the policy. |
| F7 | **Writes happen only through `publish`,** a CLI in the sandbox image (`put`, `delete`) that calls the gateway with the capability; the gateway checks T2's `write`/`delete` locations, size, type and overwrite, then writes with T2. Off unless the profile has `write`/`delete` locations; needs a shell. No read-write mounts. | Writing from code that may be compromised is the risky direction: explicit makes it checkable and auditable. A command fits how agents already work (a shell), and the bytes are already where the CLI runs. |
| F8 | **Inputs are fixed at session start.** | Simple and auditable; requests to grow them are "Later". |
| F9 | **The mounter is torn down carefully:** stopped gracefully (rclone unmounts on SIGTERM), its folder unmounted at teardown anyway (`umount -l`), and leftover `fuse.rclone` mounts swept when the runner starts (R9). | The 6.1 spike: a hard-killed mounter leaves its mount behind, attached to a dead rclone, and `mountpoint` doesn't even report it. |

## Authentication chain
For "the `agent` CLI asks a session to summarize its notes and save the summary":

| Step | Token | Held by |
|---|---|---|
| caller → agent-service, each turn | T1: `aud=agent-service`, `sub=agent-cli` | the caller; agent-service keeps it **in memory for that turn only** (TL4) |
| agent-service → auth-service: **token exchange** (RFC 8693, grant 3), T1 as the subject, agent-service's own credentials, asking for the profile's `file_access` details | T2: `aud=file-management`, `sub=agent-cli`, `act=agent-service`, the overlap, at most 10 minutes | **the gateway only** |
| mounter and `publish` → gateway | the session capability (C7: valid only from its own pod) | the sandbox and its mounter |
| gateway → file-management (mounted reads, publishes) | T2 | the gateway |

- **The sandbox never holds a real token.** A leaked capability works only from its own pod, through
  the gateway, for that session's locations, until the session ends.
- **Between turns there's no T1, so no new T2:** a stolen capability can't read your files while
  you aren't using the session (CP5 checked the same for tools).
- **file-management sees the truth:** *agent-service acting for the caller*, with grants that can't
  be widened.
- **A turn longer than T2's 10 minutes** re-exchanges with the turn's T1; if T1 itself expires
  mid-turn, long tasks need a downscoped token (grant 4, up to 1 hour), which needs auth-service's
  downscope gap fixed first ([auth-service.md](../auth-service.md), Known issues). Voice and short
  tasks never reach it (M5).
- **Callers are clients:** `${client}` is the calling client (`pi-server`, `agent-cli`), so the CLI's
  files are `private/agent-cli/…` until CP9 brings people's own identities through api-service.

## Not chosen
| Option | Why not |
|---|---|
| Read-write mounts (this spec's first version) | S3 isn't a POSIX filesystem (no in-place edits, locks, or real renames), and writes from untrusted code would go straight to storage, unchecked |
| FUSE inside the sandbox | needs `/dev/fuse` and the mount privilege inside the one container that runs untrusted code; gVisor's FUSE support is limited |
| The mounter started at the claim, its mount appearing under a running sandbox | works only with the parent bound `rslave` (the 6.1 spike), and makes the claim wait for rclone; the warm-up mount (F2) needs neither |
| The runner fetching `outputs/` files for publish (the 6.2 draft) | the bytes are already in the sandbox, where the CLI runs; the gateway's checks are the same either way |
| Copying all inputs in at start | slow for large folders, and a stale snapshot at once; kept in mind for small inputs |
| rclone or `mountpoint-s3` straight to RustFS | storage keys on VM 200, and a bypass of file-management's grants and audit |
| Temporary S3 credentials scoped by policy (STS-style) | depends on RustFS, bypasses file-management's model, and code holding them can still use them |
| A custom FUSE driver over `/objects` | a filesystem of our own to maintain; the fallback if WebDAV turns out wrong |

## Later
- **`request_files`:** the agent asks for another location with a reason; agent-service decides
  (asking you if the profile says so), and the gateway's `/dav` shows it.
- **`publish` as an MCP tool** at the gateway's `/mcp`, with the same checks, for a profile without a
  shell.
- **Confirmation for sensitive writes** (overwrites, outside the usual folder): G12, with Home
  Assistant's.
- **`mcp_tools` in the same shape as `files`:** today a profile's `mcp_tools` lists only locations
  (`[weather/]`, the action always `call`); it could take the full RFC 9396 entry
  (`{actions: [call], locations: [...]}`) so tools and files read the same in `profiles.yaml` and
  `policy.yaml`. Nothing needs it yet.

## The build (CP6, [tracker.md](tracker.md))
| Sub-CP | Piece | Where | Testable alone? |
|---|---|---|---|
| 6.1 ✅ | the spike: a sidecar's mount inside gVisor | VM 200 | done |
| 6.2 ✅ | this revision | here | — |
| 6.3 ✅ | the WebDAV read view (`/dav/{bucket}/…`: `PROPFIND`, `GET` with ranges; the same grant checks) | file-management | **yes**: unit tests, then `rclone` against it with a test token |
| 6.4 ✅ | the specs in `public/steven-universe/specs/`, synced by the pipeline (deployer D21) | deployer, api-service, GitHub | yes |
| 6.5 | the profile's `files`; the token exchange to file-management (relay, overlap); the gateway's `/dav` (below) | agent-service, auth-service's policy | yes, against the live auth-service |
| 6.6 | the mounter per pod (F2, F9) | sandbox-runner | on VM 200 |
| 6.7 | `publish` (the CLI in the sandbox image; the gateway's `/files` routes and checks; `artifact.created`/`deleted` in protocol B, shared-schemas) | agent-runtime, agent-service, shared-schemas | yes |
| 6.8 | the live checks: "Done when", M2–M5, the security invariants | all | — |

**How 6.5 works (agent-service):**
- **The profile** asks in the policy's own words: `files:` RFC 9396 `file_access` entries,
  `${client}` filled in with the caller's client (`service:<client>`; an entry naming it is left out
  for any other kind of caller). The session's readable folders are always listed in its instructions,
  after the tools, from what the exchange *granted* (after the intersection), never from the profile's
  request; nothing is listed for a session granted nothing.
- **At the start** (`Sessions._session_files`), the caller's token is exchanged for the profile's
  request; what auth-service granted (the overlap, `grant_overlap`) is read from the new token's own
  `authorization_details` and stored on the session (`sessions.files`, migration 0004), like its
  tools. A narrower grant than asked is logged; none (or file-management or auth-service down) is a
  session without files, never a failed start.
- **The gateway's `/dav`** (`app/gateway/dav.py`), paired like every gateway route (C7):
  - inside a granted read folder → file-management's `/dav`, the path exactly as it came (still
    encoded), with a token exchanged for the **running turn's** caller, asking for exactly the stored
    grant (cached by the token client until it nears expiry); between turns, 403;
  - above the granted folders → a listing made in the gateway of just the folders leading down to
    them (a grant on `public/steven-universe/` shows `/dav/public/` holding only `steven-universe/`);
    the root always answers, empty for a session without files;
  - anything else → 404; every write method → 405; `Depth: infinity` → 403.
- **The policy:** file-management's audience overlaps; agent-service relays to it; `agent-cli`
  reads and lists `public/` (for the `steven-ai` profile).

**What CP6 must answer:**

| # | Question |
|---|---|
| M1 | Does a FUSE mount from the sidecar show up inside a **gVisor** sandbox reliably? **Answered: yes** (below). |
| M2 | Speed: listing a folder of 1,000 files; reading a 100 MB file. |
| M3 | Read-only is enforced: writes to `inputs/` fail in the sandbox, and file-management would refuse them anyway. |
| M4 | Publish checks: a publish outside the write locations, over the size limit, or of a refused type is refused at the gateway, with a clear error to the agent. |
| M5 | Token renewal: a session longer than 10 minutes keeps reading with no error visible to rclone. |

**Answered by the 6.1 spike (2026-11-27, VM 200; the real sandbox image and settings: gVisor, user
10001, read-only root, all capabilities dropped, `no-new-privileges`; the mounter the stock
`rclone/rclone:1.69.1` with `/dev/fuse` and `SYS_ADMIN`; a test WebDAV server with a Bearer check):**
- **M1, yes.** The mounter's FUSE mount (bound `rshared`, so it appears on the VM) is read inside the
  gVisor sandbox through an ordinary `:ro` bind: `ls`, `cat`, subfolders. **Writes and deletes fail**
  ("Permission denied": the mount is read-only and bound `:ro`), part of M3.
- **A mount made after the sandbox started** shows up only if the parent folder is bound `ro,rslave`;
  with the runner's default (`rprivate`) it stays empty.
- **A mount made before the session exists works:** rclone's WebDAV backend reads its Bearer token per
  request from a command (`--webdav-bearer-token-command`). With no token yet, rclone stays up and
  reads fail ("Input/output error"); once the token appears and its directory cache is refreshed
  (`rclone rc vfs/refresh`), a sandbox already running reads everything. (6.6: not even that is
  needed when nothing lists the mount before the token, the case in a pod: F2.) So the mounter can start at
  warm-up, like the pod's proxy, and the claim stays instant (F2).
- **M2, an early signal** (the test server on the VM, so the mount's own overhead, not the network):
  listing 1,000 files 0.017 s; reading 100 MB 0.29 s (checksum identical); 4 KB at the 80 MB mark (a
  range request) 0.19 s. Measured again end to end in 6.7.
- **A hard-killed mounter leaves its mount behind** on the VM (attached to a dead rclone;
  `mountpoint` doesn't even report it): F9.

**Answered live by 6.8 (2026-11-27, a `steven-ai` session from the `agent` CLI; checks run in its
sandbox on VM 200 during a turn):**
- **M2, the real path** (sandbox → its proxy → the gateway → file-management → RustFS): one request
  5–17 ms; the session's **first access** 541 ms (`ls -R` of the specs; it was 7.5 s: see below);
  1,000 files listed 159 ms cold, 17 ms cached; 100 MB read in 490 ms (~200 MB/s, checksum
  identical); one small file 18 ms; 4 KB at the 80 MB mark 66 ms. (Test data put in by hand, then
  removed.)
- **The slow first access, found and fixed:** before the claim, rclone's own `Statfs` got 401s (an
  empty token), and each of its 10 default retries raised its pacer's sleep to 2 s, which then decays
  only a quarter per request: a first `ls -R` took 5.5–7.5 s. `--low-level-retries 3` (5.5 s → 377 ms)
  and `--disable About` (rclone never asks the disk size: no request before the claim; → 128 ms), both
  measured on VM 200 after a 45 s pre-claim wait. One 401 remains right after the claim: rclone holds
  its token in memory and re-reads the file only when the server rejects it (one fast retry).
- **M3, yes:** in the sandbox, writes and deletes in `inputs/` fail ("Permission denied"; `work/`
  writes); at the gateway, `PUT`, `DELETE`, `MKCOL` with the session's own capability → 405.
- **M5, yes:** one session read a spec in four turns 4 minutes apart (12 minutes), each through a
  different exchanged token (each turn's caller token exchanged afresh); a single turn longer than its
  caller token's life is still the grant-4 case above.
- **The security invariants:** a capability used from another pod, with a forged `X-Sandbox-Id` →
  403 (that pod's proxy stamped its own); no real token in the sandbox (files, environment) or the
  mounter (its configuration): only the capability; between turns, reads → 403 (only the folders
  leading down to the grant answer); a session without files (`pi`): "0 file grant(s)", its `/dav/`
  empty, `/dav/public/` 404, `inputs/` empty.
- **M4** comes with `publish` (6.7).

**Done when:** a sandbox reads `inputs/private/<caller>/…`, works in `work/`, and publishes
`outputs/report.md` to `private/<caller>/reports/`; a publish to a forbidden location is
refused; file-management's audit shows *agent-service on behalf of the caller* for every
access; and nothing on VM 200 ever held a real token.
