# Spec: files and mounts (how a sandbox sees and saves your files)

**Status: CP6, in progress.** Designed 2026-11-09 (milestone 2); **revised 2026-11-27** (6.2) for what
CP2–CP5 built (pods with their own proxy and workspace, the token exchange, the gateway's tools) and
the 6.1 spike's answers; **revised 2026-11-28** for 6.7: one local folder (`work/`), saving through
`sfsync` (`put`, `rm`) into the session's own folder, what the agent is told, a 2 s listing cache. Part of [agent-service](README.md); its build: [tracker.md](tracker.md) CP6.
Related: [file-management.md](../file-management.md), [auth-service.md](../auth-service.md),
[sandbox-runner.md](sandbox-runner.md), [agent-runtime.md](agent-runtime.md), [tools.md](tools.md).

## What it is
A session **reads** the files it's allowed to see as an ordinary folder, **works** on local disk, and
**saves** files to storage only through an explicit, checked command, `sfsync` (session file sync).
Nothing in the sandbox holds a storage credential or a real token, and file-management still checks
every access.

```
/workspace/            the pod's own folder on VM 200's workspace disk (R13); read-only itself
├── inputs/   read-only: the session's read locations, mounted (lazy, fetched on read)
│   ├── public/…                               each location at its own path (<bucket>/<prefix>)
│   └── private/session-files/<session id>/…   the session's own folder: what `sfsync put` saved
└── work/     read-write local disk: the agent's working folder (Codex starts here); gone with the pod
/tmp          read-write, in memory, small (the template's tmp_mb, 512 MB): short-lived files
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
  ← the bytes, back the same way; rclone caches each folder's listing for 2 s (F11)
```
**Live, not a copy:** nothing is synced into the pod; every `ls` or `cat` is a request at that moment.
A file saved with `sfsync put` (or removed with `sfsync rm`) shows there once the folder's cached
listing expires: within 2 s, usually at once (a model call between two commands takes longer).

## Saving: `sfsync`, a command the agent runs
`sfsync` (session file sync) is a small CLI in the sandbox image. The agent runs it from its shell,
like any other command; any local file can be the source:
```
sfsync put /workspace/work/report.md  private/session-files/<session id>/reports/report.md
sfsync put /tmp/chart.png             private/session-files/<session id>/chart.png
sfsync rm  private/session-files/<session id>/chart.png
```
Reading needs no command: what the session may read is mounted (`ls`, `cat`, `cp` from
`/workspace/inputs`), its own folder included.
```
sfsync (in the sandbox) → the pod's proxy → gateway: PUT/DELETE /files/<bucket>/<key>, the capability
  → gateway checks (F7): a turn running? T2's details allow `write` (put) or `delete` (rm) there?
    within the profile's size limit (put)?
  → gateway → file-management /objects with T2          → its audit: agent-service for the caller
  ← protocol B: artifact.created {source, location, size, sha256} (artifact.deleted {location} for rm)
```
- **CRUD, decided at the gateway:** `put` creates or replaces (`write`), `rm` deletes (`delete`);
  reading and listing are the mount (`read`, `list`). The CLI checks nothing itself: it sends the
  request and reports the gateway's answer.
- **The CLI holds no credential:** it reads the session capability already in the sandbox
  (`/run/session`) and talks only to the gateway, through the proxy.
- **Off by default.** Only a session granted `write` or `delete` somewhere has anything to save to,
  and only then is `sfsync` in its instructions (below). Running it needs a shell (the profile's
  `tools: [shell]`); `pi` has none and stays read-only.
- **Every change is audited twice:** `artifact.created` / `artifact.deleted` in the session's events,
  and file-management's audit line (*agent-service on behalf of the caller*).

**Failures the agent can act on (F12).** Codex reads a command's output and exit code, so every
failure says what went wrong and what is allowed instead. The gateway answers errors as RFC 9457
problem details (`application/problem+json`: `type`, `title`, `detail`, and the allowed locations);
`sfsync` prints one line to stderr and exits with a `sysexits.h` code, so "not allowed" and "try
again" differ:
```
sfsync: put refused: private/other/x.md is outside the folders you may write to
        (private/session-files/0898e38a-…/)                                          exit 77 (NOPERM)
sfsync: put refused: 40.2 MB is over this session's limit of 25 MB per file          exit 77 (NOPERM)
sfsync: no such local file: /workspace/work/missing.md                                exit 66 (NOINPUT)
sfsync: rm: not found: private/session-files/0898e38a-…/old.md                        exit 66 (NOINPUT)
sfsync: storage did not answer (503); nothing was changed, try again                  exit 75 (TEMPFAIL)
sfsync: no turn is running                                                            exit 75 (TEMPFAIL)
sfsync: usage: sfsync put LOCAL_FILE DESTINATION | sfsync rm DESTINATION               exit 64 (USAGE)
```
A success prints one line: `put /workspace/work/report.md -> private/session-files/0898e38a-…/reports/report.md (12.3 KB)`.

## What the agent is told
Two blocks, both generated, never written by hand in a profile:

**The workspace note** (agent-runtime, every session with a shell, the same for every session of a
template: first in its instructions; the sizes measured in the sandbox itself, F13):
```
Your workspace (this sandbox's file system):
- /workspace/work: your working folder, read-write, on disk (1.0 GB). Clone, build, edit and keep
  intermediate files here. Everything in it is deleted when the session ends.
- /tmp: small, in memory (512 MB), for short-lived temporary files. If it runs out of space,
  use /workspace/work.
- /workspace/inputs: read-only, files from storage (when this session has any; listed below).
Everything else is read-only.
```
**The files block** (agent-service, after the tools; from what the session was **granted**, after
the intersection, never from the profile's request). The read part when anything is readable; the
`sfsync` part only when some location allows `write` or `delete`, each line only for a granted
action; the paths filled in. For `steven-ai`, session `0898e38a-…`:
```
Files you can read in this session (read-only; use your shell: ls, cat, grep):
- /workspace/inputs/public/
- /workspace/inputs/private/session-files/0898e38a-…/

To save files to storage, use the `sfsync` command in your shell; it is the only way to write
(the folders above are read-only, and files left in /workspace/work or /tmp are lost when the
session ends).
You may write to: private/session-files/0898e38a-…/
You may delete in: private/session-files/0898e38a-…/
  sfsync put /tmp/chart.png private/session-files/0898e38a-…/chart.png
  sfsync put /workspace/work/report.md private/session-files/0898e38a-…/reports/report.md
  sfsync rm  private/session-files/0898e38a-…/chart.png
`put` replaces a file of the same name. Saved files appear under
/workspace/inputs/private/session-files/0898e38a-…/ within about 2 seconds.
```

## Permissions: the profile's request, the caller's grant, the overlap
The same model as tools (tools.md TL4, TL5), in the same words as `policy.yaml`: RFC 9396
`authorization_details` of type `file_access`, `actions` and `locations`.

```yaml
# policy.yaml, the caller's grant (the ceiling, reviewed once):
agent-cli:
  grants:
    file-management:
      scopes: [files:read, files:write, files:delete, files:list]
      authorization_details:
        - {type: file_access, actions: [read, list], locations: ["public/"]}
        - {type: file_access, actions: [read, list, write, delete], locations: ["private/session-files/"]}

# profiles.yaml, what a session of this profile asks for (none: no files at all):
files:
  - {actions: [read, list], locations: ["public/"]}                                      # mounted at inputs/
  - {actions: [read, list, write, delete], locations: ["private/session-files/${session}/"]}  # its own folder: mounted, and sfsync
```
**The session's own folder** (F10): `private/session-files/<session id>/` is where sessions save,
for now the only write location granted, in the tests and the real profiles alike. Nothing in the
code is specific to it: any bucket and prefix a caller is granted works the same.

1. **agent-service** fills in `${client}` (the session's caller, as auth-service does in the policy)
   and `${session}` (the session's id), and asks the token exchange for exactly the profile's
   details; the scopes follow from the actions.
2. **auth-service** intersects them with the caller's grant (`grant_overlap`, RFC 6749 §3.3): the
   prefix rule (`private/pi-server/` covers `private/pi-server/notes/`), per action. Tokens only narrow.
   T2: `sub` = the caller, `act` = agent-service, `aud` = file-management, the overlap as its details.
3. **The gateway goes by T2's details, not the profile:** what the mount shows (`read`/`list`
   locations), where `sfsync put` and `sfsync rm` may go. The intersection lives in one place.
4. **file-management** checks T2 on every request.

A profile asking for more than the caller has gets the overlap, logged when it's smaller than asked;
an empty overlap is a session with no files.

## Decisions
| ID | Decision | Why |
|---|---|---|
| F1 | **Two areas** (revised 2026-11-28; `outputs/` dropped): `inputs/` (read-only mounts) and `work/` (local, read-write: the agent's working folder, where Codex starts), plus `/tmp` (in memory, small). `/workspace` itself is read-only to the agent. `work/` is the pod's own workspace (R13: its folder on VM 200's XFS disk, under a quota). | Reading is lazy and natural; editing happens on a real disk; writing to storage is deliberate. A separate `outputs/` duplicated `work/`: same disk, same quota, and `sfsync` takes any file, so it enforced nothing. |
| F2 | **A mounter sidecar per pod does the mount,** started **at warm-up** with the pod's proxy (R10, R12): the stock rclone image (`rclone/rclone:1.69.1`), `/dev/fuse` and `SYS_ADMIN` only (and AppArmor unconfined: Docker's profile refuses `mount(2)`), in the pod's network, through its proxy. Its FUSE mount, at `/run/sandbox-runner/mounts/<pod>/inputs` on the VM (bound `rshared`), exists before the sandbox starts, which binds it read-only at `/workspace/inputs`. rclone reads its Bearer token per request (`--webdav-bearer-token-command`) from a `capability` file in the session directory: **empty from the warm-up** (rclone also reads it once at start, and a failing token command is fatal: the first deploy's pods never warmed), so nothing is read; **at the claim** the runner replaces it, then writes the session file, as before. **No cache refresh is needed:** nobody lists the mount before the claim (agent-runtime waits for its session file), so rclone has nothing stale (checked on VM 200, 2026-11-27: the files show 0 s after the token appears). | FUSE needs privileges the sandbox keeps none of (R4). Started at warm-up, the mount exists before the sandbox does, so no mount has to appear under a running sandbox, and the claim stays instant (the 6.1 spike and 6.6's check: on VM 200). |
| F3 | **file-management stays the only storage client** (its D1, D3). Every mounted read and every `sfsync` is a file-management request, with a grant check and an audit line. | One gatekeeper for storage; nothing on VM 200 has storage keys. |
| F4 | **Reads use WebDAV** (RFC 4918, the standard "files over HTTP"): only `PROPFIND` (list) and `GET` (with ranges), at file-management (`/dav/{bucket}/…`, mapped onto the same `authorize()`, buckets and key rules as `/objects`) and at the gateway (`/dav`). The mounter is plain `rclone mount` with its WebDAV backend. | An off-the-shelf, mature client (caching, retries, partial reads); read-only keeps the new surface small. |
| F5 | **Every mounter is identical;** the gateway's `/dav` shows each session only its own read locations, as folders at their own paths (`private/pi-server/notes/`). The runner never learns a session's files. | Policy lives in the control plane (agent-service); the runner carries out (R1). |
| F6 | **Permissions as for tools:** the profile's `files` (RFC 9396 `file_access` details, `${client}` = the caller, `${session}` = the session's id) asked for at the token exchange; auth-service gives the overlap with the caller's grant (`grant_overlap` at the `file-management` audience); the gateway acts on T2's details only; file-management checks T2. agent-service relays to `file-management` too (its `relay_audiences`). The agent can **request**, never **grant** itself, access. | One model for everything a session reaches; the ceiling is the caller's grant, reviewed in the policy. |
| F7 | **Writes happen only through `sfsync`** (session file sync; was `publish`), a CLI in the sandbox image: `put LOCAL_FILE DESTINATION` (create or replace) and `rm DESTINATION`. No `ls` or `get`: the mount already reads and lists. It calls the gateway (`PUT`/`DELETE /files/<bucket>/<key>`) with the capability; **the gateway decides every action:** a turn running (T1 to exchange), T2's `write` (put) or `delete` (rm) covering the destination, and the profile's per-file size limit (`limits.max_file_mb`, default 25); then it writes with T2 to file-management's `/objects`. The CLI enforces nothing. Off unless the session is granted `write`/`delete` somewhere; needs a shell. No read-write mounts. | Writing from code that may be compromised is the risky direction: explicit makes it checkable and auditable. A command fits how agents already work (a shell), and the bytes are already where the CLI runs. The gateway only sees bytes, so a source rule in the CLI would be a convention, not a check. |
| F8 | **Inputs are fixed at session start.** | Simple and auditable; requests to grow them are "Later". |
| F9 | **The mounter is torn down carefully:** stopped gracefully (rclone unmounts on SIGTERM), its folder unmounted at teardown anyway (`umount -l`), and leftover `fuse.rclone` mounts swept when the runner starts (R9). | The 6.1 spike: a hard-killed mounter leaves its mount behind, attached to a dead rclone, and `mountpoint` doesn't even report it. |
| F10 | **Sessions save into their own folder, `private/session-files/<session id>/`** (2026-11-28): a profile asks for it as `private/session-files/${session}/` with `read, list, write, delete`; a caller's grant covers `private/session-files/`, and the intersection narrows each session to its own folder. For now the only write location granted (`agent-cli` in the policy; `steven-ai` and the tests' profiles). The folder is also mounted, so what a session saved is readable at `inputs/private/session-files/<id>/`. | One predictable place per session, found again by its id; sessions can't touch each other's folders (the exchange grants only their own). Callers aren't separated from each other: any caller granted `private/session-files/` reaches every session's folder; fine while `agent-cli` is the only one (Later: per caller). |
| F11 | **The mount caches listings for 2 s** (`--dir-cache-time 2s`, was 10 s), and nothing invalidates it. | A model call between two commands takes 1.6–4.5 s, so the agent's next `ls` after `sfsync` practically always sees the change; a listing costs 5–17 ms (6.8). Invalidating from the sandbox would need rclone's remote control open to it (Not chosen). |
| F12 | **Errors the agent can act on:** the gateway's `/files` answers errors as RFC 9457 problem details (`application/problem+json`, `detail` and the allowed locations); `sfsync` prints one line to stderr and exits with a `sysexits.h` code (64 usage, 66 no such file, 75 try again, 77 not allowed). | The agent loop reads output and exit codes: a clear sentence and a code tell it whether to fix the command, ask, or retry. Both are standards, nothing of our own. |
| F13 | **The agent is told its file system by generated text only:** agent-runtime's workspace note (every session with a shell; sizes measured in the sandbox) and agent-service's files block (from what was granted, after the intersection; `sfsync` only with `write`/`delete`). | Nothing to keep in sync by hand in profiles; the agent is never told it may do what it may not. agent-runtime sees the real disk sizes (the templates differ: `chat` 1 GB, `dev` 20 GB); agent-service sees the grant. |

## Authentication chain
For "the `agent` CLI asks a session to summarize the specs and save the summary":

| Step | Token | Held by |
|---|---|---|
| caller → agent-service, each turn | T1: `aud=agent-service`, `sub=agent-cli` | the caller; agent-service keeps it **in memory for that turn only** (TL4) |
| agent-service → auth-service: **token exchange** (RFC 8693, grant 3), T1 as the subject, agent-service's own credentials, asking for the profile's `file_access` details | T2: `aud=file-management`, `sub=agent-cli`, `act=agent-service`, the overlap, at most 10 minutes | **the gateway only** |
| mounter and `sfsync` → gateway | the session capability (C7: valid only from its own pod) | the sandbox and its mounter |
| gateway → file-management (mounted reads, `sfsync`) | T2 | the gateway |

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
- **Callers are clients:** `${client}` is the calling client (`pi-server`, `agent-cli`) until CP9
  brings people's own identities through api-service.

## Not chosen
| Option | Why not |
|---|---|
| Read-write mounts (this spec's first version) | S3 isn't a POSIX filesystem (no in-place edits, locks, or real renames), and writes from untrusted code would go straight to storage, unchecked |
| FUSE inside the sandbox | needs `/dev/fuse` and the mount privilege inside the one container that runs untrusted code; gVisor's FUSE support is limited |
| The mounter started at the claim, its mount appearing under a running sandbox | works only with the parent bound `rslave` (the 6.1 spike), and makes the claim wait for rclone; the warm-up mount (F2) needs neither |
| The runner fetching `outputs/` files for publish (the 6.2 draft) | the bytes are already in the sandbox, where the CLI runs; the gateway's checks are the same either way |
| A separate `outputs/` folder, and `sfsync` taking only files from it | duplicated `work/` (same disk, same quota); the agent could copy anything into it, so it checked nothing (F1, F7) |
| `sfsync ls` and `sfsync get` | the mount already lists and reads, the session's own folder included (F10) |
| `sfsync` invalidating the mount's cache (rclone's remote control, `vfs/forget`) | the control API would listen on the pod network for the sandbox, on the most privileged container (`SYS_ADMIN`); in rclone 1.69.1 `vfs/forget`, `core/quit`, `options/set` and `debug/*` need no auth, and a password would sit in the sandbox and unlock the rest (`core/command`). A 2 s cache does the job (F11) |
| Copying all inputs in at start | slow for large folders, and a stale snapshot at once; kept in mind for small inputs |
| rclone or `mountpoint-s3` straight to RustFS | storage keys on VM 200, and a bypass of file-management's grants and audit |
| Temporary S3 credentials scoped by policy (STS-style) | depends on RustFS, bypasses file-management's model, and code holding them can still use them |
| A custom FUSE driver over `/objects` | a filesystem of our own to maintain; the fallback if WebDAV turns out wrong |

## Later
- **`request_files`:** the agent asks for another location with a reason; agent-service decides
  (asking you if the profile says so), and the gateway's `/dav` shows it.
- **`sfsync` as an MCP tool** at the gateway's `/mcp`, with the same checks, for a profile without a
  shell.
- **Session folders per caller:** `private/session-files/${client}/${session}/`, so a caller granted
  the folder sees only its own sessions' files (F10); needed once a second caller saves.
- **Exact cache invalidation, if 2 s ever isn't enough:** after a write, the gateway asks the runner,
  and the runner calls rclone's `vfs/forget` over a socket only the VM sees, never the sandbox.
- **A retention rule for `private/session-files/`** (S3 lifecycle, or the session's end): today the
  folders stay until deleted.
- **More checks on `put`** (file types, refusing overwrites) if a profile needs them.
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
| 6.5 ✅ | the profile's `files`; the token exchange to file-management (relay, overlap); the gateway's `/dav` (below) | agent-service, auth-service's policy | yes, against the live auth-service |
| 6.6 ✅ | the mounter per pod (F2, F9) | sandbox-runner | on VM 200 |
| 6.7 | `sfsync` (below) | sandbox-runner, agent-runtime, agent-service, shared-schemas, the policy | yes |
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

**What 6.7 builds:**
- **sandbox-runner:** only `work/` in a pod's workspace (owned by the sandbox's user; `/workspace`
  itself root's); the mounter's `--dir-cache-time 2s`.
- **agent-runtime:** Codex starts in `/workspace/work`; the workspace note (F13); the `sfsync` CLI
  (`core/sfsync.py`, a console script in the image).
- **agent-service:** `${session}` in profiles; `limits.max_file_mb`; the gateway's `PUT`/`DELETE
  /files/<bucket>/<key>` (paired, the checks of F7, problem details); `artifact.created` /
  `artifact.deleted` in the session's events; the files block's `sfsync` part (F13); `steven-ai` and
  a test profile get their own folder.
- **shared-schemas:** the two events in protocol B.
- **auth-service's policy:** `agent-cli` granted `private/session-files/` (read, list, write,
  delete); deployed by hand.

**What CP6 must answer:**

| # | Question |
|---|---|
| M1 | Does a FUSE mount from the sidecar show up inside a **gVisor** sandbox reliably? **Answered: yes** (below). |
| M2 | Speed: listing a folder of 1,000 files; reading a 100 MB file. |
| M3 | Read-only is enforced: writes to `inputs/` fail in the sandbox, and file-management would refuse them anyway. |
| M4 | `sfsync` checks: a `put` outside the write locations or over the size limit, and an `rm` outside the delete locations, are refused at the gateway, with an error the agent understands (F12). |
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
- **M4** comes with `sfsync` (6.7).

**Done when:** a session reads `inputs/public/…`, works in `work/`, saves
`/workspace/work/report.md` with `sfsync put` to `private/session-files/<its id>/`, and sees it under
`inputs/private/session-files/<its id>/` within 2 s; an `sfsync` to a forbidden location is refused
with a clear error; file-management's audit shows *agent-service on behalf of the caller* for every
access; and nothing on VM 200 ever held a real token.
