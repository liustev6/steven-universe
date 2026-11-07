# LXC Container Setup for Docker Deployment

This guide walks you through setting up a Proxmox LXC container to run Docker containers for the steven-universe microservices.

## Prerequisites

- Proxmox VE host with root access
- LXC container created (Debian 13 recommended)
- Container ID (e.g., 104)
- SSH access to the Proxmox host

## Table of Contents

1. [Docker Installation in LXC](#1-docker-installation-in-lxc)
2. [Proxmox Host Configuration](#2-proxmox-host-configuration)
3. [SSH Key Setup](#3-ssh-key-setup)
4. [Optional: Auto-Login Configuration](#4-optional-auto-login-configuration)

---

## 1. Docker Installation in LXC

Run these commands **inside the LXC container** to install Docker on Debian 13.

### Step 1.1: Install Prerequisites

```bash
apt update
apt install -y ca-certificates curl gnupg lsb-release
```

### Step 1.2: Add Docker GPG Key

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  | tee /etc/apt/keyrings/docker.asc > /dev/null
chmod a+r /etc/apt/keyrings/docker.asc
```

### Step 1.3: Add Docker Repository

```bash
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/debian trixie stable" \
  > /etc/apt/sources.list.d/docker.list
```

### Step 1.4: Install Docker

```bash
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### Step 1.5: Verify Installation

```bash
docker --version
docker compose version
```

### Step 1.5: Install rsync
```bash
apt install -y rsync
```

---

## 2. Proxmox Host Configuration

These settings are required to allow Docker to run inside the LXC container. Docker needs privileged access to manage containers, networks, and devices.

### Step 2.1: Edit LXC Configuration

On the **Proxmox host**, edit the LXC container configuration file. Replace `104` with your container ID:

```bash
nano /etc/pve/lxc/104.conf
```

### Step 2.2: Add Docker Support Settings

Add these lines at the end of the file:

```conf
# Allow Docker to run in LXC
lxc.apparmor.profile: unconfined
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: proc:rw sys:rw
```

**What these settings do:**
- `lxc.apparmor.profile: unconfined` - Disables AppArmor restrictions
- `lxc.cgroup2.devices.allow: a` - Allows access to all devices
- `lxc.cap.drop:` - Prevents dropping capabilities (Docker needs them)
- `lxc.mount.auto: proc:rw sys:rw` - Mounts /proc and /sys with read-write access

### Step 2.3: Restart Container

```bash
pct stop 104
pct start 104
```

---

## 3. SSH Key Setup

Set up SSH key authentication for passwordless deployment from your local machine to the LXC container.

### Step 3.1: Generate SSH Key (Local Machine)

If you don't already have an SSH key, generate one on your **local development machine**:

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
```

Press Enter to accept the default location (`~/.ssh/id_ed25519`).

### Step 3.2: Copy Public Key

Display your public key:

```bash
cat ~/.ssh/id_ed25519.pub
```

Copy the entire output (starts with `ssh-ed25519 ...`).

### Step 3.3: Add Key to LXC Container

Run these commands **inside the LXC container**:

```bash
# Create .ssh directory with correct permissions
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# Add your public key
nano ~/.ssh/authorized_keys
# Paste the public key you copied, save and exit (Ctrl+O, Enter, Ctrl+X)

# Set correct permissions
chmod 600 ~/.ssh/authorized_keys
```

### Step 3.4: Test SSH Connection

From your **local machine**, test the connection (replace with your LXC IP):

```bash
ssh root@<LXC_IP_ADDRESS>
```

You should connect without being prompted for a password.

### Step 3.5: Configure Deployment Script

Update your `.env` file in `python/services/web-server/` with the LXC host:

```bash
LXC_HOST=root@<LXC_IP_ADDRESS>
LXC_DEPLOY_PATH=~/web-server
```

---

## 4. Optional: Auto-Login Configuration

Enable automatic root login on the LXC container console (useful for debugging, but not required for deployment).

### Step 4.1: Edit Getty Service

Run this command **inside the LXC container**:

```bash
systemctl edit container-getty@.service
```

### Step 4.2: Add Auto-Login Configuration

Add these lines in the editor that opens:

```ini
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear --keep-baud tty%I 115200,38400,9600 $TERM
```

Save and exit (Ctrl+O, Enter, Ctrl+X).

### Step 4.3: Reboot Container

```bash
reboot
```

After reboot, the console will automatically log in as root.

**Note:** This is tested and working on Debian-based containers.

---

## Verification Checklist

After completing all steps, verify your setup:

- [ ] Docker is installed and running: `docker ps`
- [ ] Docker Compose is available: `docker compose version`
- [ ] SSH key authentication works from local machine
- [ ] Container has proper LXC configuration for Docker
- [ ] Deployment script can connect: `infra/deployment/<service>.sh`

---

## Troubleshooting

### Docker fails to start containers

**Problem:** Error like "failed to create shim task"

**Solution:** Check that you added all LXC configuration settings in Step 2 and restarted the container.

### SSH connection refused

**Problem:** Cannot connect via SSH

**Solution:**
1. Check if SSH service is running: `systemctl status ssh`
2. Install if missing: `apt install -y openssh-server`
3. Ensure container has network connectivity: `ping google.com`

### Permission denied on deployment

**Problem:** rsync or ssh fails during deployment

**Solution:**
1. Verify SSH key is properly added to `~/.ssh/authorized_keys`
2. Check file permissions: `ls -la ~/.ssh/`
3. Should be: `drwx------ .ssh/` and `-rw------- authorized_keys`

---

## Next Steps

After completing this setup:

1. Configure your `.env` file with all required environment variables (see `.env.example`)
2. Run the deployment script from the repo root: `infra/deployment/web-gateway.sh`
3. Access your service at `http://<LXC_IP>:8000`
4. Check health endpoint: `curl http://<LXC_IP>:8000/health`

For more information, see `python/services/web-server/README.md`.
