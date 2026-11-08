# RustFS (object storage)

**Status:** running since 2026-11-06 · CT 101 `rustfs` on pve01 · `http://192.168.50.123:9000` (S3 API),
`:9001/rustfs/console/` (web console) · RustFS 1.0.4 · Related: [file-management.md](file-management.md)
(the only service that uses it), [secrets-manager.md](secrets-manager.md) (where its keys will be managed)

## What it is

RustFS is the home lab's **S3-compatible object storage**: buckets and objects behind the standard
S3 API, on a single container with a 2 TB disk. It replaced MinIO, whose community edition was
archived. SeaweedFS was the other candidate; RustFS was chosen because it is a drop-in S3 server
with a MinIO-style web console and a single binary, which fits a one-node home lab.

**Only file-management talks to it** (with a limited key). Other services never get storage
credentials; they go through file-management, which decides access from auth-service tokens.
RustFS itself has no idea who the end caller is.

### At a glance
| | |
|---|---|
| Container | CT 101 `rustfs`: Debian 13, unprivileged, 2 cores, 4 GB RAM, 512 MB swap, 2000 GB disk on `local-lvm` (thin), `onboot=1`, `startup order=1,up=10` |
| Address | `192.168.50.123`, DHCP reservation for MAC `BC:24:11:EF:41:59`; IPv6 auto (unused) |
| Ports | `9000` S3 API, `9001` web console; both plain HTTP on all interfaces, LAN only |
| Binary / service | `/opt/rustfs/rustfs` (1.0.4), systemd `rustfs.service`, runs as **root** |
| Data | `/opt/rustfs_data/data` (one volume, no redundancy); logs `/opt/rustfs_data/logs/` |
| Config | `/etc/default/rustfs` (root, mode 600): root access key + secret, volume path, addresses, console on |
| Buckets | `public`, `private` (versioning on, lifecycle rules) |
| Keys | root key (admin, console only); one limited key for file-management |
| Backups | **none** (see Known issues) |

## How it was created

With the community-scripts Proxmox helper, run as root on pve01:
```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/rustfs.sh)"
```
In the interactive (advanced) setup the disk was set to **2000 GB on `local-lvm`**. That is the
container's root disk, which also holds the data; `local-lvm` is an LVM thin pool (3.49 TB), so
only the space actually used is allocated. Script defaults: 2 cores, 2 GB RAM, 20 GB disk, Debian
13, unprivileged.

What the scripts do (read 2026-11-08):

1. **`ct/rustfs.sh`** (on pve01) loads the shared `build.func` from the community-scripts **core**
   repository at run time, asks the setup questions, and creates the container.
2. **`install/rustfs-install.sh`** (inside the container):
   - updates the OS; creates `/opt/rustfs_data/{data,logs}`;
   - downloads the **latest** RustFS release from GitHub (`rustfs-linux-x86_64-gnu-*.zip`) into
     `/opt/rustfs`;
   - generates the **root key**: access key `openssl rand -hex 8` (16 hex chars), secret a random
     32-character password, written to `/etc/default/rustfs` (mode 600) with
     `RUSTFS_VOLUMES=/opt/rustfs_data/data`, `RUSTFS_ADDRESS=:9000`, `RUSTFS_CONSOLE_ADDRESS=:9001`,
     `RUSTFS_CONSOLE_ENABLE=true`, log level `error`;
   - creates `rustfs.service` (runs as **root**, `EnvironmentFile=/etc/default/rustfs`,
     `Restart=on-failure`, `LimitNOFILE=65536`), enables and starts it;
   - adds the community-scripts login banner and its `update` helper; installs `mc` (Midnight
     Commander, a file manager, not the MinIO client).
3. **Updating:** running the same script again **inside** the container runs its `update_script`:
   stop the service, download the latest release, start it again. The data is kept.

The script was run as published (not pinned to a commit) and executes code fetched at run time as
root on pve01, so the exact steps that ran are only as reviewable as that day's `main` branch.

## Changes made after the script
| When | Change |
|---|---|
| 2026-11-06 | RAM raised to 4 GB |
| 2026-11-07 | `startup order=1,up=10` (after auth-service, before file-management; before, it had no order and started after them) |
| 2026-11-07 | DHCP reservation `.123` on the router |
| 2026-11-07 | Debian's `awscli` installed (bucket setup; the MinIO admin client was not installed) |
| 2026-11-07 | Buckets `public` and `private`, versioning on, lifecycle rules (below) |
| 2026-11-08 | A limited access key for file-management, with its own policy (below) |

## Buckets
| Bucket | Used by | Settings |
|---|---|---|
| `public` | file-management: files anyone may read through api-service | versioning on; no bucket policy (private in storage) |
| `private` | file-management: `private/<client>/…`, each service's own files | versioning on; no bucket policy |

Lifecycle rules on both (RustFS accepted them; check after 30 days that expiry really runs):
```json
{"Rules": [
  {"ID": "expire-old-versions", "Status": "Enabled", "Filter": {"Prefix": ""},
   "NoncurrentVersionExpiration": {"NoncurrentDays": 30}},
  {"ID": "abort-stale-multipart", "Status": "Enabled", "Filter": {"Prefix": ""},
   "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}
]}
```
Commands used (on CT 101, root key loaded from `/etc/default/rustfs` into the command's
environment only):
```sh
. /etc/default/rustfs
export AWS_ACCESS_KEY_ID="$RUSTFS_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$RUSTFS_SECRET_KEY" AWS_DEFAULT_REGION=us-east-1
S3="aws --endpoint-url http://127.0.0.1:9000 s3api"
$S3 create-bucket --bucket private
$S3 put-bucket-versioning --bucket private --versioning-configuration Status=Enabled
$S3 put-bucket-lifecycle-configuration --bucket private --lifecycle-configuration file://lifecycle.json
```

## Keys and access
| Key | Can do | Where it lives | Used for |
|---|---|---|---|
| **Root key** (generated by the script) | everything: buckets, users, policies | `/etc/default/rustfs` on CT 101 only | logging into the console, bucket setup |
| **file-management key** | object read/write/delete/list in `public` and `private` only | CT 103 `/etc/file-management/credentials/` (owner 10001, mode 600) | file-management's storage calls |

The file-management key is an **access key** created in the console for a user, bound to the
policy `file-management`:
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
bucket are refused (`AccessDenied`). Listing buckets is allowed but filtered: it only shows the
two buckets the key may use.

In RustFS a **user** (console login) and an **access key** (S3 credentials) are separate: S3
clients need an access key created for the user; the user name and password don't work as S3
credentials (`InvalidAccessKeyId`).

## Operations
| Task | How |
|---|---|
| Open the console from outside the LAN | `ssh -N -L 9001:192.168.50.123:9001 -L 9000:192.168.50.123:9000 pve01`, then `http://localhost:9001/rustfs/console/`; log in with the root key (`ssh pve01 "pct exec 101 -- grep RUSTFS_SECRET_KEY /etc/default/rustfs"` shows the secret in your own terminal) |
| S3 commands as admin | `pct enter 101`, then the `aws` commands above (root key in the command's environment only) |
| New key for a service | console: a policy limited to its buckets/prefixes, a user, an access key for that user with only that policy; place the secret on the service host with `read -rs` (never in chat); test it with allowed and refused operations |
| Rotate file-management's key | create a new access key for the same user, replace the two files on CT 103, redeploy file-management, delete the old key |
| Recover an overwritten or deleted object | versions are kept 30 days: `aws s3api list-object-versions --bucket private --prefix <key>`, then `get-object --version-id …` (root key) |
| Update RustFS | run the community script again **inside** CT 101 (it stops the service, installs the latest release, starts it); afterwards run file-management's deploy for its health and smoke checks |
| Logs | `journalctl -u rustfs` and `/opt/rustfs_data/logs/` |

## Known issues and risks
| Issue | Risk | Proposed fix |
|---|---|---|
| **No backups** (pve01 has no backup jobs at all) | a disk failure loses every object; the NVMe this lives on had I/O errors before the Proxmox reinstall | back up the buckets to a second disk (the Kingston 1 TB) or off-site with `rclone sync` of all versions; Proxmox backup jobs for the small containers (CT 102/103 contain keys: encrypt and restrict them) |
| **One volume, no redundancy** | no protection against disk errors inside RustFS | acceptable for one node; rely on backups |
| **Runs as root, no systemd hardening** | a RustFS bug or exposed console gives root in the container | a dedicated `rustfs` user owning `/opt/rustfs_data`, plus `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp` in a systemd override |
| **Plain HTTP on 9000/9001, on all interfaces** | requests are signed (SigV4: the secret never travels), but object data and console logins cross the LAN unencrypted | Proxmox firewall on CT 101: port 9000 only from file-management (`.66`), 9001 only from pve01 (for the tunnel); later RustFS TLS with a `files`-style Let's Encrypt certificate |
| **The console logs in with the root key** | a leaked console password is full control | a separate console admin user; keep the root key for emergencies |
| **`latest` releases, script not pinned** | an update can change behaviour without review | note the version before updating (`/opt/rustfs/rustfs --version`); pin in a future own install |
| **No automatic security updates** | Debian packages go stale | `unattended-upgrades` like CT 102/103 |
| **A 2 TB thin disk on a shared pool** | if the thin pool ever fills, every guest gets I/O errors | quotas in file-management (planned); watch `pvesm status` |
| **Leftovers in the console** | earlier attempts may have left users without working keys (`file-management` / `file_management`) | delete unused users and keys |

## Non-goals
Multiple nodes or erasure coding; public access (RustFS stays LAN-only, all public reads go
through api-service → file-management); per-caller access logic in storage (file-management does
that).
