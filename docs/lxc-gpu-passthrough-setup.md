# The GPU for LXC containers on pve01

How pve01's NVIDIA card (an RTX 5060, 8 GB) is made usable by unprivileged LXC containers, and how a
container gets it. Done by hand, one step at a time, each step's result recorded here.
Why an LXC and not a VM: [tts-service.md](specs/tts-service.md) T1. The first container to use it:
CT 108, tts-service (CP4, [tracker.md](specs/agent-service/tracker.md)).

**The shape:** the **kernel module** (the driver's part inside pve01's kernel) runs on pve01 only;
containers share pve01's kernel, so they get the card's **device files** (`/dev/nvidia*`) and the
driver's **user-space libraries**, which must be the very same version as the module. Agent sandboxes
never get the GPU.

**Versions (2026-11-17):** Proxmox VE 9.2.2, kernel `7.0.14-29-pve` (following Proxmox's updates,
step 5); NVIDIA open kernel modules **620.70.06** (held); NVIDIA Container Toolkit 1.20.x. Secure Boot is off on pve01
(no module signing needed). pve01's console uses the AMD integrated graphics (`79:00.0`), not this card.

## Part 1: pve01 (the host)
Run in pve01's shell, as root.

### 1. The kernel headers
The driver's kernel module is compiled on pve01 (by DKMS) for the exact running kernel; the headers
are that kernel's interface description, the input to the build. Files on disk: nothing runs.
```sh
uname -r                                          # 7.0.2-6-pve
apt update
apt install -y proxmox-headers-$(uname -r)
ls -d /lib/modules/$(uname -r)/build && echo "headers in place"
```
Don't `apt upgrade` here: the kernel must stay as it is.

**Result (2026-11-17):** `proxmox-headers-7.0.2-6-pve` 7.0.2-6 installed; `/lib/modules/7.0.2-6-pve/build`
present; the kernel unchanged. (215 package updates were pending: left alone until the kernel is
pinned in step 5.)

### 2. NVIDIA's apt repository
`cuda-keyring` (NVIDIA's standard way, per their install guide) installs their signing key and a source
file that trusts **only** that key for that repository (`signed-by=`). Nothing is installed from it yet.
```sh
cd /tmp
wget -q https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb && rm cuda-keyring_1.1-1_all.deb
cat /etc/apt/sources.list.d/cuda-debian13-x86_64.list
gpg --show-keys /usr/share/keyrings/cuda-archive-keyring.gpg
apt update 2>&1 | grep -i nvidia
apt-cache policy nvidia-kernel-open-dkms | head -4
```
**Result (2026-11-17):** `cuda-keyring` 1.1-1; the source:
`deb [signed-by=/usr/share/keyrings/cuda-archive-keyring.gpg] https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/ /`.
The key: rsa4096, 2025-08-15, **`02182E60104FCDC26EAE1B8597A5D4CB8793F200`**, "Kitmaker (Debian 13
Trixie) <kitmaker@nvidia.com>" (a different fingerprint later means NVIDIA rotated it: check before
trusting). `apt update` read the signed `InRelease`; candidate `nvidia-kernel-open-dkms` 620.70.06-1.

### 3. The driver (compute only), held at one version
`nvidia-kernel-open-dkms` (the module's source, built by DKMS for the running kernel) and
`nvidia-driver-cuda` (CUDA's libraries, `nvidia-smi`, `nvidia-persistenced`, `nvidia-modprobe`; no
X server, no OpenGL or Vulkan display libraries). If the DKMS build fails, stop: nothing else changes
(`nouveau` keeps the card).

**3a, a dry run first** (`apt -s` changes nothing; read the list before installing):
```sh
apt -s install nvidia-kernel-open-dkms=620.70.06-1 nvidia-driver-cuda=620.70.06-1 | grep -E "^Inst" | awk '{print $2, $3}'
```
Result: the build toolchain (`dkms` 3.4.4, `gcc-14`, `make`, `build-essential`), every NVIDIA package
at 620.70.06 (incl. `firmware-nvidia-gsp`, which the open modules need, `libcuda1`,
`nvidia-persistenced`, `nvidia-modprobe`), no kernel package, no display libraries. Four upgrades as
dependencies: glibc (`libc6`, `libc-bin`, `libc-l10n`, `locales`) 2.41-12+deb13u3 → u4, a Debian point
release that `libc6-dev` needs. (`nvidia-powerd`, a laptop power service, comes along: disabled in
step 6.)

**3b, the install** (DKMS compiles the module: a minute or two):
```sh
apt install -y nvidia-kernel-open-dkms=620.70.06-1 nvidia-driver-cuda=620.70.06-1 2>&1 | tee /root/nvidia-install.log | grep -i -E "dkms|building|error|fail" | tail -20
dkms status
modinfo -F version nvidia
```
**Result (2026-11-17): it builds on kernel 7.0.2.** `nvidia/620.70.06, 7.0.2-6-pve, x86_64: installed`;
modules `nvidia`, `nvidia-uvm`, `nvidia-modeset`, `nvidia-drm`, `nvidia-peermem` in
`/lib/modules/7.0.2-6-pve/updates/dkms/`. DKMS made its own signing key (`/var/lib/dkms/mok.key`)
and signed them: unused while Secure Boot is off. The module isn't loaded yet: `nouveau` holds the
card until step 4 and the reboot. The full log: `/root/nvidia-install.log`.

**3c, held at this version:** the container's user-space libraries must match the host's module
exactly, so `apt upgrade` must never move them; changing the driver is a deliberate step ("Updating
later").
```sh
apt-mark hold $(dpkg-query -W -f='${binary:Package} ${Version}\n' | awk '$2 ~ /^620\.70\.06/ {print $1}')
apt-mark showhold
apt -s upgrade 2>/dev/null | grep -c -E "^Inst (nvidia|libnvidia|libcuda|firmware-nvidia)" || true
```
**Result:** 27 packages held (`firmware-nvidia-gsp`, `libcuda1`, `libnvidia-*`, `nvidia-*`, …); a
simulated upgrade would move 0 of them.

### 4. `nouveau` and `nova_core` blacklisted
The kernel's own drivers for NVIDIA cards (`nouveau`, and `nova_core` for this generation) must not
claim the card before NVIDIA's driver. NVIDIA's package already blacklists both
(`/etc/modprobe.d/nvidia.conf`: `blacklist nouveau`, `blacklist nova-core`; `-` and `_` are the same
to modprobe); Proxmox blacklists `nvidiafb`. Our own file states it once more, then the boot image is
rebuilt so the blacklist applies from early boot:
```sh
cat > /etc/modprobe.d/blacklist-nova-core.conf <<'EOF'
# pve01: the RTX 5060 belongs to NVIDIA's driver (docs/lxc-gpu-passthrough-setup.md, step 4).
# nouveau is blacklisted by NVIDIA's package (/etc/modprobe.d/nvidia.conf), nvidiafb by Proxmox.
blacklist nova_core
EOF
update-initramfs -u -k all
lsinitramfs /boot/initrd.img-$(uname -r) | grep -E "modprobe.d/(nvidia|blacklist-nova)"
```
**Result (2026-11-17):** both files in `initrd.img-7.0.2-6-pve`. pve01 boots with GRUB from `/boot`
("No /etc/kernel/proxmox-boot-uuids found, skipping ESP sync": no Proxmox-managed EFI partitions).
`nouveau` and `nova_core` stay loaded until the reboot (step 7). NVIDIA's file also sets its
power-management options (`NVreg_PreserveVideoMemoryAllocations=1` and others): left as they are.

### 5. Following kernel updates (not pinning)
Decided 2026-11-17: pve01 keeps taking Proxmox's kernel updates, and the driver follows them. DKMS
hooks into kernel installs (`/etc/kernel/postinst.d/dkms`) and builds the module for every new kernel,
**if that kernel's headers are installed too**: `proxmox-default-headers` pulls in the headers of
Proxmox's current kernel series, so they come with each kernel update.
```sh
apt install -y proxmox-default-headers
dkms status                                     # the module, built for every installed kernel
```
**Result (2026-11-17):** `proxmox-default-headers` 2.1.0 brought `proxmox-headers-7.0`, and with it
Proxmox's newest kernel, **7.0.14-29** (installed, and the next to boot), with its headers; DKMS built
the module for it right away (`nvidia/620.70.06, 7.0.14-29-pve, x86_64: installed`). Its boot image
holds the blacklists of step 4.

**The safety net:** a DKMS build can fail on a brand-new kernel. `./setup.sh shutdown` checks that the
module is built for the kernel pve01 boots next (the newest in `/boot`) and stops if not; after a
boot, `./setup.sh` checks `nvidia-smi`. If a build fails: "Updating later".

### 6. `nvidia-persistenced` on, `nvidia-powerd` off
NVIDIA's packages already do most of it through udev (`/usr/lib/udev/rules.d/60-nvidia*.rules`): when
the card appears at boot they **create its device files, `/dev/nvidia-uvm` included** ("containers
lack the privileges to do that, so we preload it here"), **start `nvidia-persistenced`** (keeps the
driver state loaded: no slow first CUDA call after idle) and turn on runtime power management. Here
it's made explicit, and the laptop power-boost service is masked:
```sh
systemctl enable nvidia-persistenced
systemctl mask nvidia-powerd
```
**Result (2026-11-17):** `nvidia-persistenced` enabled, `nvidia-powerd` masked (→ `/dev/null`). Neither
runs before the reboot (the module isn't loaded yet).

### 7. Reboot and check
```sh
./setup.sh reboot        # checks the driver is built for the next kernel, pauses agents, reboots
# … about 3 minutes …
./setup.sh               # a GPU line first, then the usual (OpenBao's unseal, agents, every service)
nvidia-smi
lsmod | grep -E "^(nvidia|nouveau|nova)"
ls -la /dev/nvidia*
```
**Result (2026-11-17, 22:39):** booted **7.0.14-29-pve**. `nvidia-smi`: NVIDIA GeForce RTX 5060, driver
620.70.06, CUDA 13.5 (the newest CUDA this driver runs: an image built for CUDA 12.8 or newer works), 8151 MiB, persistence mode on. Loaded: `nvidia`, `nvidia_uvm`, `nvidia_modeset`, `nvidia_drm`;
no `nouveau`, no `nova_core`. `nvidia-persistenced` active, `nvidia-powerd` off.

**The device files** (what a container is given, Part 2), all created at boot by NVIDIA's udev rule:

| File | Major, minor |
|---|---|
| `/dev/nvidia0` | 195, 0 |
| `/dev/nvidiactl` | 195, 255 |
| `/dev/nvidia-modeset` | 195, 254 |
| `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools` | 507, 0 and 1 (the major is assigned at boot: don't hard-code it) |
| `/dev/nvidia-caps/nvidia-cap1`, `nvidia-cap2` | 510, 1 and 2 |

## Part 2: a container (CT 108, tts-service)
Run in pve01's shell. The container is an ordinary one from [lxc-setup.md](lxc-setup.md); the GPU is
added with Proxmox's `devN` entries.

### 1. The container
```sh
pct create 108 local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst \
  --hostname tts-service --arch amd64 --ostype debian --unprivileged 1 \
  --features nesting=1,keyctl=1 --cores 4 --memory 8192 --swap 1024 \
  --rootfs local-lvm:64 --net0 name=eth0,bridge=vmbr0,ip=dhcp,type=veth \
  --onboot 1 --startup order=2,up=10 --timezone host \
  --ssh-public-keys /root/.ssh/authorized_keys
pct start 108
```
**Result (2026-11-17):** CT 108 `tts-service`, MAC `BC:24:11:DD:1C:95`, `192.168.50.34`; unprivileged,
4 cores, 8 GB, 64 GB, boot order 2. DNS: `tts.lan.liustev6.ca` (DNS only), a DHCP reservation.

### 2. The GPU's device files
Proxmox's `devN` entries pass a host device into the container, by path (so `nvidia-uvm`'s number,
assigned at boot, is looked up at each start), with no raw `lxc.*` lines: the container stays
unprivileged. The four CUDA needs: the card (`nvidia0`), the driver's control channel (`nvidiactl`),
CUDA's unified memory (`nvidia-uvm`, `nvidia-uvm-tools`). Not given: `nvidia-modeset` (displays) and
`nvidia-caps/*` (splitting data-center cards, MIG). **`mode=0666`**, as on pve01 itself: Proxmox's
default (`0660`, root only) would stop a service running as uid 10001 from opening the GPU.
```sh
pct stop 108
pct set 108 --dev0 /dev/nvidia0,mode=0666 --dev1 /dev/nvidiactl,mode=0666 \
            --dev2 /dev/nvidia-uvm,mode=0666 --dev3 /dev/nvidia-uvm-tools,mode=0666
pct start 108
pct exec 108 -- ls -la /dev/nvidia0 /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools
```
**Result (2026-11-17):** the four device files inside CT 108, `crw-rw-rw-`, numbers 195 and 507.

### 3. The usual container setup
[lxc-setup.md](lxc-setup.md) steps 2–7, unchanged: nothing about the GPU differs.

**Result (2026-11-17):** DHCP reservation and `tts.lan.liustev6.ca` (DNS only) → `192.168.50.34`; console
autologin and a root password; packages and Docker (Compose v5.7.1; `hello-world` runs inside the
unprivileged container); SSH alias `tts-host` (host key `SHA256:QoMm0yhs…`, checked through pve01);
the deploy wrapper (`/etc/deploy-target/services`: `tts-service`); CT 108 in [boot.md](specs/boot.md).

### 4. NVIDIA's user-space libraries, at the host's exact version
The container shares pve01's kernel: no kernel module here, only the driver's user-space half
(`libcuda1`, `libnvidia-ml1`, `nvidia-smi`, …), at **exactly** the module's version. Same repository
and key as pve01; a dry run first (no `dkms`, no compiler, no kernel module). In CT 108's shell:
```sh
cd /tmp && curl -fsSLO https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb && rm cuda-keyring_1.1-1_all.deb && apt-get update -qq
sha256sum /usr/share/keyrings/cuda-archive-keyring.gpg      # the same as pve01's (no gpg in the minimal image)
apt-get -s install --no-install-recommends nvidia-driver-cuda=620.70.06-1 | grep -E "^Inst"
apt-get install -y --no-install-recommends nvidia-driver-cuda=620.70.06-1
apt-mark hold $(dpkg-query -W -f='${binary:Package} ${Version}\n' | awk '$2 ~ /^620\.70\.06/ {print $1}')
systemctl mask nvidia-persistenced nvidia-powerd               # pve01's daemon serves the card
nvidia-smi
```
**Result (2026-11-17):** keyring `77f0992e…34bbe`, the same as pve01's; 25 packages at 620.70.06,
held (no kernel-module packages; a few small X libraries as dependencies); `nvidia-persistenced` and
`nvidia-powerd` masked. **`nvidia-smi` inside CT 108: NVIDIA GeForce RTX 5060, 620.70.06, 8151 MiB.**
(Pasting a long block into the Proxmox console can garble it: paste one line at a time, or use
`ssh tts-host`.)

### 5. The NVIDIA Container Toolkit, and a CUDA container
The toolkit lets `docker run --gpus all` (and compose's `gpus:`) hand a container the device files and
the libraries of step 4. In an unprivileged LXC the container can't manage device cgroups, so the
toolkit must not try: **`no-cgroups = true`** (the known fix for Proxmox containers; the device files
are already open, step 2). In CT 108's shell:
```sh
apt-get install -y nvidia-container-toolkit
nvidia-ctk config --set nvidia-container-cli.no-cgroups --in-place
nvidia-ctk runtime configure --runtime=docker && systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.8.2-base-ubuntu24.04 nvidia-smi
docker run --rm --gpus all --user 10001:10001 nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0
```
**Result (2026-11-17):** toolkit 1.20.2 (`libnvidia-container1`, tools, base); `no-cgroups = true`;
Docker's `nvidia` runtime registered. In a CUDA 12.8 container: `NVIDIA GeForce RTX 5060, 620.70.06,
8151 MiB`, as root and as uid 10001. **NVIDIA's vector-add sample, as uid 10001: Test PASSED** (data to
the card, a kernel, the result back: CUDA's unified memory end to end; built for CUDA 12.5, the driver
compiled it for this card on the fly). The test images removed. The toolkit isn't held: it doesn't
have to match the driver.

## The chain, end to end
```
pve01: kernel 7.0.14 + NVIDIA's module 620.70.06 (DKMS) ── /dev/nvidia0, nvidiactl, nvidia-uvm(-tools)
  └─ CT 108 (unprivileged): dev0–dev3 (mode 0666) + NVIDIA's libraries 620.70.06 (held)
       └─ Docker + NVIDIA Container Toolkit (no-cgroups) ── `gpus: all` ──▶ a container, as uid 10001
```

## Updating later
**A Proxmox (kernel) update:** nothing special. `apt full-upgrade` on pve01 installs the new kernel and
its headers; DKMS builds the module for it (`dkms status` lists it: `installed`). Then
`./setup.sh reboot`: it refuses if the module isn't built for the kernel it would boot. After the boot,
`./setup.sh` shows the GPU line. If DKMS failed for the new kernel, keep running the current one (don't
reboot) and update the driver (below); NVIDIA usually supports a new kernel within weeks.

**A driver update** (a fix, or support for a new kernel): the host's module and every GPU container's
libraries must move **together**, to the same version.
1. pve01: `apt-mark unhold` the NVIDIA packages; `apt install nvidia-kernel-open-dkms=<v> nvidia-driver-cuda=<v>`;
   `dkms status` (built for every installed kernel); hold again (step 3c).
2. Each GPU container (CT 108): the same `<v>` of `nvidia-driver-cuda` (step 4), held again.
3. `./setup.sh reboot` (the new module loads at boot); `nvidia-smi` on pve01, in CT 108, and in a CUDA
   container (step 5).
Until the reboot, the old module keeps running and the containers' new libraries don't match it: do
both sides and the reboot in one sitting.
