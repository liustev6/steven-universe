#!/bin/bash

##############################################################################
# GPU Service Remote Build Deployment Script
#
# This script builds the Docker image directly on the GPU server instead of
# building locally and transferring. This is much faster (30s vs 3min).
#
# Prerequisites:
# - SSH access to VM_HOST configured in .env
# - Docker and nvidia-docker installed on remote VM
# - NVIDIA drivers and CUDA installed on remote VM
#
# Usage:
#   infra/deployment/gpu-server.sh   (from the repo root, or any directory)
##############################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory (infra/deployment/) and the service it deploys
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../python/services/gpu-server" && pwd)"

# Load environment variables
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${BLUE}Loading environment from .env${NC}"
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
else
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please create .env file from .env.example"
    exit 1
fi

# Validate required variables
if [ -z "$VM_HOST" ]; then
    echo -e "${RED}Error: VM_HOST not set in .env${NC}"
    exit 1
fi

if [ -z "$DEPLOY_PATH" ]; then
    echo -e "${RED}Error: DEPLOY_PATH not set in .env${NC}"
    exit 1
fi

# Docker image details
IMAGE_NAME="gpu-service"
IMAGE_TAG="${APP_VERSION:-latest}"
FULL_IMAGE_NAME="$IMAGE_NAME:$IMAGE_TAG"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}GPU Service Remote Build Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Target: ${BLUE}$VM_HOST${NC}"
echo -e "Deploy Path: ${BLUE}$DEPLOY_PATH${NC}"
echo -e "Image: ${BLUE}$FULL_IMAGE_NAME${NC}"
echo ""

##############################################################################
# Step 1: Transfer Source Code to VM
##############################################################################

echo -e "${YELLOW}Step 1: Transferring source code to VM...${NC}"

# Get python root directory
PYTHON_ROOT="$(dirname "$(dirname "$PROJECT_ROOT")")"

# Create build directory structure on VM
ssh "$VM_HOST" "mkdir -p $DEPLOY_PATH/build/libs $DEPLOY_PATH/build/services"

# Transfer only shared-schemas and gpu-server
echo "Syncing shared-schemas..."
rsync -av --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'venv' \
    "$PYTHON_ROOT/libs/shared-schemas/" \
    "$VM_HOST:$DEPLOY_PATH/build/libs/shared-schemas/"

echo "Syncing gpu-server..."
rsync -av --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'venv' \
    "$PYTHON_ROOT/services/gpu-server/" \
    "$VM_HOST:$DEPLOY_PATH/build/services/gpu-server/"

echo -e "${GREEN}✓ Source code transferred (shared-schemas + gpu-server only)${NC}"
echo ""

##############################################################################
# Step 2: Build Docker Image on VM
##############################################################################

echo -e "${YELLOW}Step 2: Building Docker image on remote VM...${NC}"

ssh "$VM_HOST" << EOF
    cd $DEPLOY_PATH/build

    echo "Building Docker image..."
    docker build \
        --build-arg APP_VERSION="$APP_VERSION" \
        -t "$FULL_IMAGE_NAME" \
        -f services/gpu-server/Dockerfile \
        .

    echo "✓ Docker image built successfully"
EOF

echo -e "${GREEN}✓ Image built on VM${NC}"
echo ""

##############################################################################
# Step 3: Transfer Configuration Files
##############################################################################

echo -e "${YELLOW}Step 3: Transferring configuration files...${NC}"

# Transfer .env file
if [ -f "$PROJECT_ROOT/.env" ]; then
    scp "$PROJECT_ROOT/.env" "$VM_HOST:$DEPLOY_PATH/.env"
    echo -e "${GREEN}✓ Transferred .env${NC}"
else
    echo -e "${RED}Warning: .env file not found${NC}"
fi

# Config files (task_definitions.yaml, task_actions.yaml, model_paths.yaml) are baked into Docker image
echo -e "${GREEN}✓ Configuration files will be included in Docker image${NC}"

echo ""

##############################################################################
# Step 3.5: Transfer GPU Workers
##############################################################################

echo -e "${YELLOW}Step 3.5: Transferring GPU workers to VM...${NC}"

# Get workers directory (python/workers/gpu-server/)
WORKERS_DIR="$(dirname "$(dirname "$PROJECT_ROOT")")/workers/gpu-server"

if [ -d "$WORKERS_DIR" ]; then
    echo "Syncing GPU workers..."

    # Create workers directory on VM
    ssh "$VM_HOST" "mkdir -p ~/gpu-workers"

    # Sync workers directory
    rsync -av --delete \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.git' \
        --exclude 'venv' \
        "$WORKERS_DIR/" \
        "$VM_HOST:~/gpu-workers/"

    echo -e "${GREEN}✓ GPU workers transferred to ~/gpu-workers${NC}"

    # Build workers on VM
    echo "Building workers on VM..."
    ssh "$VM_HOST" << 'WORKER_EOF'
        cd ~/gpu-workers

        # Find all build.sh scripts and execute them
        for build_script in $(find . -name "build.sh" -type f); do
            worker_dir=$(dirname "$build_script")
            echo "Building worker in $worker_dir..."
            cd ~/gpu-workers/$worker_dir
            chmod +x build.sh
            ./build.sh
            echo "✓ Built worker: $worker_dir"
        done

        echo ""
        echo "Worker images:"
        docker images | grep -E "worker|loading"
WORKER_EOF

    echo -e "${GREEN}✓ GPU workers built${NC}"
else
    echo -e "${YELLOW}Warning: Workers directory not found at $WORKERS_DIR${NC}"
    echo -e "${YELLOW}Skipping worker deployment${NC}"
fi

echo ""

##############################################################################
# Step 4: Create Directories on VM
##############################################################################

echo -e "${YELLOW}Step 4: Setting up directories on VM...${NC}"

ssh "$VM_HOST" << EOF
    # Model cache directory
    if [ -n "$MODEL_CACHE_DIR" ]; then
        mkdir -p "$MODEL_CACHE_DIR"
        echo "Created model cache directory: $MODEL_CACHE_DIR"
    fi
EOF

echo -e "${GREEN}✓ Directories configured${NC}"
echo ""

##############################################################################
# Step 5: Stop Existing Container
##############################################################################

echo -e "${YELLOW}Step 5: Stopping existing container...${NC}"

ssh "$VM_HOST" << EOF
    if docker ps -a --format '{{.Names}}' | grep -q "^gpu-service$"; then
        echo "Stopping and removing existing container..."
        docker stop gpu-service || true
        docker rm gpu-service || true
        echo "✓ Existing container removed"
    else
        echo "No existing container found"
    fi
EOF

echo ""

##############################################################################
# Step 6: Start New Container
##############################################################################

echo -e "${YELLOW}Step 6: Starting new container...${NC}"

ssh "$VM_HOST" << EOF
    cd $DEPLOY_PATH

    # Load environment variables
    export \$(grep -v '^#' .env | xargs)

    # Create custom Docker network for container DNS resolution
    if ! docker network inspect gpu-network >/dev/null 2>&1; then
        echo "Creating gpu-network..."
        docker network create gpu-network
        echo "✓ Network created"
    else
        echo "✓ Network gpu-network already exists"
    fi

    # Run container with GPU support on custom network
    docker run -d \
        --name gpu-service \
        --restart unless-stopped \
        --network gpu-network \
        --gpus all \
        -p 8001:8000 \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v \${MODEL_CACHE_DIR}:\${MODEL_CACHE_DIR} \
        --env-file .env \
        "$FULL_IMAGE_NAME"

    echo "✓ Container started"
    echo ""
    echo "Container logs:"
    docker logs gpu-service --tail 20
EOF

echo ""
echo -e "${GREEN}✓ Deployment complete${NC}"
echo ""

##############################################################################
# Step 7: Health Check
##############################################################################

echo -e "${YELLOW}Step 7: Running health check...${NC}"
echo "Waiting 5 seconds for service to start..."
sleep 5

ssh "$VM_HOST" << 'EOF'
    HEALTH_CHECK=$(curl -s http://localhost:8001/health || echo "failed")

    if echo "$HEALTH_CHECK" | grep -q "healthy"; then
        echo "✓ Service is healthy"
    else
        echo "⚠ Service health check failed"
        echo "Response: $HEALTH_CHECK"
    fi
EOF

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Service URL: http://$(echo $VM_HOST | cut -d'@' -f2):8001"
echo ""
echo "Useful commands:"
echo "  View logs:    ssh $VM_HOST 'docker logs -f gpu-service'"
echo "  Stop service: ssh $VM_HOST 'docker stop gpu-service'"
echo "  Restart:      ssh $VM_HOST 'docker restart gpu-service'"
echo "  Shell access: ssh $VM_HOST 'docker exec -it gpu-service /bin/bash'"
echo ""
