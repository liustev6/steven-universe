# RustFS (object storage)

**Status: live** (since 2026-11-06; checked 2026-11-08). CT 101 `rustfs` on pve01: S3 API
`http://192.168.50.123:9000` (file-management's `FILES_STORAGE_ENDPOINT`), console
`:9001/rustfs/console/`, RustFS 1.0.4. Related: [file-management.md](file-management.md) (its only
user), [secrets-manager.md](secrets-manager.md) (where its key lives), [boot.md](boot.md).

## What it is
RustFS is the home lab's **S3-compatible object storage**: buckets and objects behind the standard
S3 API, in one container with a 2 TB disk. It replaced MinIO, whose community edition was archived.
SeaweedFS was the other candidate; RustFS won because it is a drop-in S3 server, a single binary,
and has a MinIO-style web console, which fits a one-node home lab.

**The patterns:**
- **Only file-management talks to it.** Other services never hold storage credentials: they call
  file-management with an auth-service token, and file-management decides access per prefix.
  RustFS never knows who the end caller is.
- **Standard S3 over the LAN** (SigV4-signed, `aioboto3` in file-management), so any S3 server
  could replace it. Nothing is exposed outside the LAN.
- **A limited key per service, never the root key.** file-management's key may only do object
  operations in its two buckets. The root key stays on CT 101 for the console and admin work.
- **The key is a managed secret:** its values live in OpenBao and every file-management deploy
  writes them to the host, so rotating is "put a new value, redeploy".
- **A plain systemd service on an LXC,** not Docker: one binary, started at boot.

## Setup
| | |
|---|---|
| Container | CT 101 `rustfs`: Debian 13, unprivileged, 2 cores, 4 GB RAM, 512 MB swap, 2000 GB disk on `local-lvm` (LVM thin pool, 3.49 TB: only used space is allocated) |
| Boot | `onboot=1`, `startup order=1,up=10`: after auth-service (0), before file-management (2) |
| Address | `192.168.50.123`, DHCP reservation for MAC `BC:24:11:EF:41:59`; IPv6 auto (unused) |
| Ports | `9000` S3 API, `9001` console; both plain HTTP on all interfaces, LAN only |
| Service | `/opt/rustfs/rustfs`, systemd `rustfs.service`: runs as **root**, `EnvironmentFile=/etc/default/rustfs`, `Restart=on-failure`, `LimitNOFILE=65536` |
| Config | `/etc/default/rustfs` (root, mode 600): root access key and secret, `RUSTFS_VOLUMES=/opt/rustfs_data/data`, `RUSTFS_ADDRESS=:9000`, `RUSTFS_CONSOLE_ADDRESS=:9001`, console on, log level `error` |
| Data | `/opt/rustfs_data/data` (one volume, no redundancy); logs in `/opt/rustfs_data/logs/` |
| Tools | Debian's `awscli` for admin S3 commands (the MinIO client is not installed) |
| Backups | **none** (see Known issues) |

## Buckets
| Bucket | Holds | Settings |
|---|---|---|
| `public` | files anyone may read through api-service | versioning on; no bucket policy (private in storage) |
| `private` | each service's own files, under `private/<client>/…` | versioning on; no bucket policy |

These are file-management's `FILES_BUCKETS`. Both buckets have the same lifecycle rules: old
versions expire after 30 days, and abandoned multipart uploads after 1 day. RustFS accepted the
rules; check after 30 days that expiry really runs.
```json
{"Rules": [
  {"ID": "expire-old-versions", "Status": "Enabled", "Filter": {"Prefix": ""},
   "NoncurrentVersionExpiration": {"NoncurrentDays": 30}},
  {"ID": "abort-stale-multipart", "Status": "Enabled", "Filter": {"Prefix": ""},
   "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}
]}
```

## Keys and access
| Key | Can do | Where it lives | Used for |
|---|---|---|---|
| **Root key** | everything: buckets, users, policies | CT 101 `/etc/default/rustfs` only (host-only) | the console, bucket setup |
| **file-management key** | object read, write, delete, list in `public` and `private` only | OpenBao `kv/services/file-management/rustfs_access_key` and `rustfs_secret_key`; every deploy writes them to CT 103 `/etc/file-management/credentials/` (owner 10001, mode 600) | file-management's storage calls |

The file-management key is an access key for a RustFS user, bound to the policy `file-management`:
```json
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"],
   "Resource": ["arn:aws:s3:::public", "arn:aws:s3:::private"]},
  {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                                 "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
   "Resource": ["arn:aws:s3:::public/*", "arn:aws:s3:::private/*"]}]}
```
Checked 2026-11-08 with the key itself: read, write, list and delete work in both buckets;
creating or deleting buckets, changing versioning, setting bucket policies and reading any other
bucket are refused (`AccessDenied`). Listing buckets is allowed but only shows these two.

In RustFS a **user** (console login) and an **access key** (S3 credentials) are separate: S3
clients need an access key created for the user; the user's name and password fail as S3
credentials (`InvalidAccessKeyId`).

## Operations
| Task | How |
|---|---|
| Console from outside the LAN | `ssh -N -L 9001:192.168.50.123:9001 -L 9000:192.168.50.123:9000 pve01`, then `http://localhost:9001/rustfs/console/`; log in with the root key (`ssh pve01 "pct exec 101 -- grep RUSTFS_SECRET_KEY /etc/default/rustfs"` shows it in your own terminal) |
| S3 commands as admin | `pct enter 101`; `. /etc/default/rustfs`; `export AWS_ACCESS_KEY_ID="$RUSTFS_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$RUSTFS_SECRET_KEY" AWS_DEFAULT_REGION=us-east-1`; then `aws --endpoint-url http://127.0.0.1:9000 s3api …` (root key in that shell only) |
| Add a bucket | `create-bucket`, `put-bucket-versioning`, `put-bucket-lifecycle-configuration` (the rules above); extend the key's policy; then file-management's side (its spec, "Buckets") |
| New key for a service | console: a policy limited to its buckets or prefixes, a user, an access key for that user with only that policy; `bao kv put kv/services/<svc>/<name> value=-` for both values; declare them in its `deploy.yaml` `secrets:`, `tofu apply`, deploy; test allowed and refused operations |
| Rotate file-management's key | new access key for the same user; `bao kv put` both values; redeploy file-management (`gh workflow run build.yml -f redeploy=file-management`); delete the old key |
| Recover an overwritten or deleted object | versions are kept 30 days: `list-object-versions --bucket <bucket> --prefix <key>`, then `get-object --version-id …` (root key) |
| Update RustFS | note the version (`/opt/rustfs/rustfs --version`); run the community script again **inside** CT 101 (stops the service, installs the latest release, starts it; data kept); then redeploy file-management for its health and smoke checks |
| Is it up | `curl -s -o /dev/null -w "%{http_code}\n" http://192.168.50.123:9000/` → `403` (up, no credentials) |
| Logs | `journalctl -u rustfs` and `/opt/rustfs_data/logs/` |

## Known issues and risks
| Issue | Risk | Proposed fix |
|---|---|---|
| **No backups** (pve01 has no backup jobs at all) | a disk failure loses every object; this NVMe had I/O errors before the Proxmox reinstall | `rclone sync` of all versions to a second disk (the Kingston 1 TB) or off-site; Proxmox backup jobs for the small containers (several hold keys: encrypt and restrict them) |
| **One volume, no redundancy** | no protection against disk errors inside RustFS | acceptable for one node; rely on backups |
| **Runs as root, no systemd hardening** | a RustFS bug or an exposed console gives root in the container | a dedicated `rustfs` user owning `/opt/rustfs_data`, plus `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp` in a systemd override |
| **Plain HTTP on 9000/9001, on all interfaces** | requests are signed (SigV4: the secret never travels), but object data and console logins cross the LAN unencrypted | Proxmox firewall on CT 101: 9000 only from file-management (`.66`), 9001 only from pve01 (for the tunnel); later TLS with a Let's Encrypt certificate like `files` |
| **The console logs in with the root key** | a leaked console password is full control | a separate console admin user; keep the root key for emergencies |
| **`latest` releases, script not pinned** | an update can change behaviour without review | note the version before updating; pin in a future own install |
| **No automatic security updates** | Debian packages go stale | `unattended-upgrades`, as on CT 102–106 |
| **A 2 TB thin disk on a shared pool** | if the thin pool fills, every guest gets I/O errors | quotas in file-management (planned); watch `pvesm status` |
| **Leftovers in the console** | earlier attempts may have left users without working keys (`file-management` / `file_management`) | delete unused users and keys |

## History
Installed 2026-11-06 with the community-scripts Proxmox helper `ct/rustfs.sh` (run unpinned as root
on pve01: latest release, generated root key, systemd unit); disk set to 2000 GB, RAM raised to 4 GB the same day.
2026-11-07: boot order, DHCP reservation, `awscli`, the buckets. 2026-11-08: the limited key for
file-management, moved into OpenBao the same day (deployer phase 0b).

## Non-goals
- Multiple nodes or erasure coding.
- Public access: RustFS stays LAN-only; all public reads go through api-service → file-management.
- Per-caller access logic in storage: file-management does that.
