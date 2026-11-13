# The sandbox worker (a VM on pve01)

How to create the VM that runs agent sandboxes ([sandbox-runner.md](specs/agent-service/sandbox-runner.md)).
It's a **VM, not an LXC** (R2): model-written code runs here, so it gets its own kernel, and gVisor
(`runsc`) between that code and the kernel. It follows [lxc-setup.md](lxc-setup.md), adapted:
`qm` instead of `pct`, a Debian cloud image with cloud-init instead of a container template, and
gVisor on top of Docker.

**Where each command runs:**
- **pve01's shell** (Proxmox → pve01 → Shell, or SSH to pve01): steps 1, 2, the checks in 4, and 6;
- **the VM's console** (Proxmox → VM 200 → Console, or `qm terminal 200` on pve01; exit with
  `Ctrl-O`): step 5 (log in without a password after step 6);
- **your Mac:** only the SSH alias in step 4.

**VM 200** (the convention: VMs are numbered from 200, containers from 100), **startup order 4**
(after agent-service at 3). The one placeholder is `<IP>`, the VM's address, known after step 2.
Names: host `sandbox-worker`, SSH alias `sandbox-host`, LAN name `sandbox-runner.lan.liustev6.ca`.

## 1. First-boot settings (pve01's shell, once)
The VM is configured by cloud-init. A **snippet** gives it your SSH keys (pve01's
`authorized_keys`, which include your Mac's) and the **QEMU guest agent**, so pve01 can read files
inside the VM: that's how step 4 checks the host key without trusting the network.

Let the `local` storage hold snippets, keeping what it already holds (check the list first; on
pve01 it was `import,iso,backup,vztmpl`):
```sh
pvesh get /storage/local --output-format json-pretty | grep content
pvesm set local --content import,iso,backup,vztmpl,snippets
```
Write the snippet:
```sh
mkdir -p /var/lib/vz/snippets
{ echo "#cloud-config"
  echo "hostname: sandbox-worker"
  echo "disable_root: false"
  echo "ssh_pwauth: false"
  echo "users:"
  echo "  - name: root"
  echo "    ssh_authorized_keys:"
  grep -E '^(ssh-(ed25519|rsa)|ecdsa-sha2-) ' /root/.ssh/authorized_keys | sed 's/.*/      - "&"/'
  echo "packages: [qemu-guest-agent]"
  echo "runcmd: [[systemctl, enable, --now, qemu-guest-agent]]"
} > /var/lib/vz/snippets/sandbox-worker.yaml
cat /var/lib/vz/snippets/sandbox-worker.yaml
```
It holds only public keys, and only real key lines: blank lines and comments in `authorized_keys`
are skipped. Check the output: one `- "ssh-…"` line per key, no empty entry (on pve01: two,
`root@pve01` and your Mac's).

## 2. Create the VM
**The image:** Debian's `generic` cloud image (trixie publishes no smaller `genericcloud` one),
checked against Debian's `SHA512SUMS`. The VM gets **8 vCPU, 16 GB RAM, 256 GB**:
- **memory:** ~1.5 GB for the VM itself (Debian, Docker, runner, proxy), the rest for sandboxes at up
  to 2 GB each (6–7 at their limit; a chat sandbox uses ~140 MB);
- **CPU:** agents mostly wait on the model, but tools (builds, tests) burst, and gVisor slows
  syscall-heavy work;
- **disk:** thin-provisioned on `local-lvm` (only what's written is used): images (×2 during an
  update), image pruning headroom, mounter caches, and room for disk-backed workspaces (2.4);
  256 GB because pve01 has disk to spare (3.7 TB). Growing it later is easy; shrinking isn't.

CP2 (2.2) measures real use; `qm set 200 --cores … --memory …` and a reboot resize it.

### On pve01's shell
```sh
mkdir -p /var/lib/vz/template/qcow && cd /var/lib/vz/template/qcow
wget -q --show-progress -N https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2
wget -q -O SHA512SUMS https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS
grep " debian-13-generic-amd64.qcow2$" SHA512SUMS | sha512sum -c -       # must print: OK

qm create 200 --name sandbox-worker --ostype l26 --machine q35 --cpu host --cores 8 --memory 16384 \
  --scsihw virtio-scsi-single --net0 virtio,bridge=vmbr0 --agent enabled=1 \
  --serial0 socket --vga serial0 --onboot 1 --startup order=4,up=30
qm set 200 --scsi0 local-lvm:0,import-from=/var/lib/vz/template/qcow/debian-13-generic-amd64.qcow2,discard=on,ssd=1,iothread=1
qm disk resize 200 scsi0 256G
qm set 200 --ide2 local-lvm:cloudinit --boot order=scsi0 --ipconfig0 ip=dhcp \
  --cicustom user=local:snippets/sandbox-worker.yaml
qm start 200
qm config 200 | grep -o 'virtio=[^,]*'          # the MAC, for the DHCP reservation
```

### Or in the web UI
1. **Download:** pve01 → local → Import → **Download from URL**: the image URL above; hash
   algorithm SHA-512, checksum from [SHA512SUMS](https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS).
2. **Create VM:**

| Tab | Setting |
|---|---|
| General | VM ID **200**, name `sandbox-worker`; Advanced: start at boot ✓, order **4**, startup delay **30** |
| OS | **Do not use any media**; Linux 6.x – 2.6 kernel |
| System | graphic card **Serial terminal 0**; machine **q35**; SeaBIOS; VirtIO SCSI single; Qemu Agent ✓ |
| Disks | scsi0 on `local-lvm`, **Import from** `local`: `debian-13-generic-amd64.qcow2`; discard, SSD emulation, IO thread ✓ |
| CPU | 1 socket, **8** cores, type **host** |
| Memory | **16384** MiB |
| Network | `vmbr0`, VirtIO, firewall ✓ (its rules come in 2.7) |
| Confirm | start after created: **unticked** |

3. **Before the first start** (VM 200):
   - Hardware → Add → **Serial Port** `0`; Hardware → Add → **CloudInit Drive** (IDE, `local-lvm`);
   - Hardware → Hard Disk → Disk Action → **Resize** by **253** GiB (to ~256 GB);
   - Cloud-Init → IP Config: **DHCP**; leave user and SSH keys empty (the snippet sets them);
   - Options → Boot Order: only **scsi0**;
   - the snippet, which the UI can't attach (pve01's shell):
     `qm set 200 --cicustom user=local:snippets/sandbox-worker.yaml`;
   - **Start**. The MAC: Hardware → Network Device.

### Notes
- **`--cpu host`** (type host): passes the host CPU through. gVisor's default platform, systrap,
  needs nothing more; its faster KVM platform needs nested virtualization, which pve01 has on (2.2
  measures whether it's worth it).
- **First boot takes a minute or two** (cloud-init installs the guest agent). Then, on pve01:
  `qm agent 200 ping && qm guest cmd 200 network-get-interfaces | grep '"ip-address"'`
  (or Summary → IPs in the UI).

## 3. Network (you: router and Cloudflare)
1. **DHCP reservation** for the MAC; if the IP changes, reboot the VM (`qm reboot 200`).
2. **Cloudflare DNS:** `sandbox-runner.lan.liustev6.ca` → the IP, **DNS only** (grey cloud).

The Proxmox firewall that limits what the VM can reach (R5, second layer) is step 8.

## 4. The host key, then SSH from the Mac
**On pve01's shell,** compare the key the guest agent reads from inside the VM with the key the
network offers. They must match:
```sh
qm guest exec 200 -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub | grep -o 'SHA256:[^ ]*'
ssh-keyscan -t ed25519 <IP> 2>/dev/null | ssh-keygen -lf - | grep -o 'SHA256:[^ ]*'
```
**On your Mac,** add the alias to `~/.ssh/config`, and connect once (only after the match):
```
Host sandbox-host
  HostName <IP>
  User root
  ProxyJump pve01
```
```sh
ssh -o StrictHostKeyChecking=accept-new sandbox-host hostname
```

## 5. Packages, Docker and gVisor (the VM's console)
```sh
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get -y -qq full-upgrade
apt-get install -y -qq ca-certificates curl git gnupg openssl python3-yaml unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian trixie stable" > /etc/apt/sources.list.d/docker.list
curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /etc/apt/keyrings/gvisor.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/gvisor.gpg] https://storage.googleapis.com/gvisor/releases release main" > /etc/apt/sources.list.d/gvisor.list
apt-get update -qq && apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin runsc
runsc install                                      # registers "runsc" in /etc/docker/daemon.json
systemctl enable --now docker && systemctl restart docker
set +e
docker run --rm hello-world | grep -m1 Hello
docker run --rm --runtime=runsc hello-world | grep -m1 Hello
docker run --rm --runtime=runsc alpine dmesg | head -2      # gVisor prints its own "kernel" messages
runsc --version | head -1
```
`set -e` stops at the first failing install command; `set +e` turns it off before the checks, so a
failed check doesn't close the console.

| Package | Why |
|---|---|
| Docker CE + Compose plugin | the runner, the egress proxy and the sandboxes are containers |
| `runsc` (gVisor) | the sandboxes' runtime (R4): a user-space kernel between their code and the VM's |
| `git`, `python3-yaml` | deploys check out the commit and read its manifest (as on every host) |
| `unattended-upgrades` | security updates |

No `certbot` yet: the runner's TLS certificate comes with its deploy (2.6).

## 6. Console autologin and root password (optional, the convention here; pve01's shell)
The console asks for a login, and root has no password (SSH is key-only). The guest agent sets up
autologin from pve01, no login needed, with the same override as the containers. It covers both
consoles: `ttyS0` (serial terminal) and `tty1` (the default graphical one):
```sh
qm agent 200 ping && echo "agent OK"
for unit in serial-getty@ttyS0 getty@tty1; do
  qm guest exec 200 -- sh -c "mkdir -p /etc/systemd/system/$unit.service.d && printf '%s\n' '[Service]' 'ExecStart=' 'ExecStart=-/sbin/agetty --autologin root --noreset --noclear --keep-baud 115200,57600,38400,9600 - \$TERM' > /etc/systemd/system/$unit.service.d/autologin.conf"
done
qm guest exec 200 -- sh -c 'systemctl daemon-reload; systemctl restart serial-getty@ttyS0 getty@tty1'
qm guest exec 200 -- cat /etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf   # shows a literal $TERM
qm guest passwd 200 root          # optional: a root password for emergencies
```
If the agent isn't running: still booting (retry), or the snippet isn't attached
(`qm config 200 | grep cicustom` is empty): attach it as in step 2, then `qm reboot 200`.

## 7. The workspace disk (CP2 2.4, R13)
Sandboxes' workspaces live on their own disk: XFS with project quotas, so each sandbox gets a hard
size limit, and a full workspace disk can't fill the system disk. Added while the VM runs.

**On pve01's shell:**
```sh
qm set 200 --scsi1 local-lvm:256,discard=on,ssd=1,iothread=1
```
**On the VM** (the disk is the one Proxmox calls `drive-scsi1`; check it's empty before formatting):
```sh
DEV=/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_drive-scsi1
wipefs -n $DEV                                     # must print nothing: no filesystem on it yet
apt-get install -y -qq xfsprogs
mkfs.xfs -q -L workspaces $DEV
mkdir -p /srv/workspaces
echo "LABEL=workspaces /srv/workspaces xfs defaults,prjquota,nofail 0 2" >> /etc/fstab
systemctl daemon-reload && mount /srv/workspaces
xfs_quota -x -c "state -p" /srv/workspaces | grep -E "Accounting|Enforcement"   # both ON
```
To grow it later: `qm disk resize 200 scsi1 <size>` on pve01, then `xfs_growfs /srv/workspaces` on
the VM (online). VM 200 got 200 GB, then 256 GB.

## 8. The Proxmox firewall (CP2 2.7, R5's second wall)
VM 200 runs the least trusted code in the lab. Inside it, Docker's networks confine the sandboxes;
**this** wall is enforced by pve01 at VM 200's network card, so even root inside the VM can't
change it. VM 200 may reach only DNS, auth-service and the internet; only pve01 (and so the Mac,
over Tailscale) and the deployer may reach it. The rules, with their reasons, are in git:
[`infra/hosts/pve01/firewall/`](../infra/hosts/pve01/firewall/) (`cluster.fw`, `200.fw`, `check.sh`).

**`cluster.fw` is the master switch,** and its policies also apply to pve01 itself: both are ACCEPT,
so pve01 and every guest without rules behave as with the firewall off. Only a guest with its own
`<vmid>.fw` **and** `firewall=1` on its NIC is filtered.

**Apply or change it** (pve01's shell; `/etc/pve` refuses `chmod`, so `cp`, not `install -m`):
```sh
cp cluster.fw 200.fw /etc/pve/firewall/                      # from infra/hosts/pve01/firewall/
pve-firewall compile | grep -iE "error|warn" || echo ok       # validate before it takes effect
qm set 200 --net0 virtio=BC:24:11:C5:23:E8,bridge=vmbr0,firewall=1   # once; the link blips ~1 s
pve-firewall restart && pve-firewall status
```
**Check it** from the Mac: `infra/hosts/pve01/firewall/check.sh` (21 probes; ALL PASSED). Drops are
logged in `/var/log/pve-firewall.log` on pve01. **Undo:** `pve-firewall stop` (all rules off at
once); VM 200's Proxmox console works regardless of the network.

**When something new needs a path** (CP3: agent-service calling the runner), add the rule to
`200.fw` in git first, then apply and re-run `check.sh` with a row for it.

## 9. Done when (sub-checkpoint 2.1)
- [x] `docker run --rm --runtime=runsc hello-world` works on the VM (2026-11-12: runsc
  release-20261109.0, Docker 29.9.3; `/dev/kvm` present, so gVisor's KVM platform can be measured)
- [x] the host key was checked through pve01 before it was trusted
  (`SHA256:SxpsCwJnpZ9DRPXmdVwXNAamgWPPHZI6M8kXyznYeUE`)
- [ ] DHCP reservation (`BC:24:11:C5:23:E8` → `192.168.50.95`) and `sandbox-runner.lan.liustev6.ca` in place
- [x] the VM is in [boot.md](specs/boot.md)

The deploy wrapper and the deployer's allowlist (lxc-setup.md steps 6 and 13) come in 2.6, when the
runner is deployed.
