#!/bin/bash
# OSRM One-click Deployment Script
# Usage: bash deploy_osrm.sh
# 
# This script automates the entire OSRM deployment process:
# 1. Configure Docker registry mirrors (for China network)
# 2. Pull OSRM backend image
# 3. Process map data (extract/partition/customize)
# 4. Start OSRM routing service

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}OSRM One-click Deployment Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAP_DATA_DIR="${SCRIPT_DIR}/map_data"
CONTAINER_NAME="osrm-guangdong"
SERVICE_PORT=5000

# Check if running with sudo
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Note: Some operations may require sudo privileges${NC}"
fi

# Step 1: Check Docker installation
echo -e "${GREEN}[Step 1] Checking Docker installation...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed!${NC}"
    echo "Please install Docker first:"
    echo "  CentOS: sudo yum install -y docker-ce"
    echo "  Ubuntu: sudo apt install -y docker-ce"
    exit 1
fi

echo -e "${GREEN}Docker version: $(docker --version)${NC}"
echo ""

# Step 2: Configure Docker registry mirrors
echo -e "${GREEN}[Step 2] Configuring Docker registry mirrors...${NC}"

if [ -f /etc/docker/daemon.json ]; then
    echo -e "${YELLOW}Docker daemon.json already exists${NC}"
    echo "Current configuration:"
    cat /etc/docker/daemon.json
    echo ""
    read -p "Overwrite configuration? (y/N): " overwrite
    if [[ "$overwrite" != "y" && "$overwrite" != "Y" ]]; then
        echo "Skipping Docker configuration..."
    else
        sudo mkdir -p /etc/docker
        sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://docker.m.daocloud.io",
    "https://dockerpull.org",
    "https://dockerhub.icu",
    "https://docker.udayun.com",
    "https://docker.211678.top",
    "https://registry.docker-cn.com",
    "https://hub-mirror.c.163.com"
  ],
  "max-concurrent-downloads": 10,
  "debug": false,
  "experimental": false
}
EOF
        sudo systemctl daemon-reload
        sudo systemctl restart docker
        sleep 5
        echo -e "${GREEN}Docker configuration updated${NC}"
    fi
else
    sudo mkdir -p /etc/docker
    sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://docker.m.daocloud.io",
    "https://dockerpull.org",
    "https://dockerhub.icu",
    "https://docker.udayun.com",
    "https://docker.211678.top",
    "https://registry.docker-cn.com",
    "https://hub-mirror.c.163.com"
  ],
  "max-concurrent-downloads": 10,
  "debug": false,
  "experimental": false
}
EOF
    sudo systemctl daemon-reload
    sudo systemctl restart docker
    sleep 5
    echo -e "${GREEN}Docker configuration created${NC}"
fi

# Verify Docker registry mirrors
echo "Verifying Docker configuration..."
if sudo docker info | grep -q "Registry Mirrors"; then
    echo -e "${GREEN}Registry mirrors configured successfully${NC}"
else
    echo -e "${YELLOW}Warning: Registry mirrors may not be active${NC}"
fi
echo ""

# Step 3: Pull OSRM image
echo -e "${GREEN}[Step 3] Pulling OSRM backend image...${NC}"

MAX_RETRIES=5
RETRY_COUNT=0
SUCCESS=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ] && [ "$SUCCESS" = false ]; do
    echo "Attempt $((RETRY_COUNT + 1))/$MAX_RETRIES..."
    
    if timeout 180 docker pull osrm/osrm-backend:latest; then
        echo -e "${GREEN}OSRM image pulled successfully!${NC}"
        SUCCESS=true
    else
        echo -e "${YELLOW}Pull failed, waiting 30 seconds...${NC}"
        sleep 30
        RETRY_COUNT=$((RETRY_COUNT + 1))
    fi
done

if [ "$SUCCESS" = false ]; then
    echo -e "${RED}Failed to pull OSRM image after $MAX_RETRIES attempts${NC}"
    echo ""
    echo "Alternative solutions:"
    echo "1. Download image tar file and import: docker load -i osrm-backend.tar"
    echo "2. Use a VPN or proxy"
    echo "3. Try again later when network is stable"
    exit 1
fi
echo ""

# Step 4: Check map data
echo -e "${GREEN}[Step 4] Checking map data files...${NC}"

mkdir -p "${MAP_DATA_DIR}"

# Look for PBF files
PBF_FILE=$(find "${MAP_DATA_DIR}" -name "*.osm.pbf" -type f | head -n 1)

if [ -z "$PBF_FILE" ]; then
    echo -e "${YELLOW}No map data found in ${MAP_DATA_DIR}${NC}"
    echo ""
    echo "Please download map data from:"
    echo "  https://download.geofabrik.de/asia/china.html"
    echo ""
    echo "For Guangdong province:"
    echo "  wget https://download.geofabrik.de/asia/china/guangdong-latest.osm.pbf"
    echo "  mv guangdong-latest.osm.pbf ${MAP_DATA_DIR}/"
    echo ""
    read -p "Enter path to your PBF file (or press Enter to exit): " pbf_path
    
    if [ -z "$pbf_path" ]; then
        exit 1
    fi
    
    if [ -f "$pbf_path" ]; then
        cp "$pbf_path" "${MAP_DATA_DIR}/"
        PBF_FILE="${MAP_DATA_DIR}/$(basename "$pbf_path")"
        echo -e "${GREEN}Map data copied to ${MAP_DATA_DIR}${NC}"
    else
        echo -e "${RED}File not found: $pbf_path${NC}"
        exit 1
    fi
fi

# Get base filename (without .osm.pbf extension)
BASE_NAME=$(basename "$PBF_FILE" .osm.pbf)
echo -e "${GREEN}Using map data: $PBF_FILE ($(du -h "$PBF_FILE" | cut -f1))${NC}"
echo ""

# Step 5: Process map data
echo -e "${GREEN}[Step 5] Processing map data...${NC}"

OSRM_FILE="${MAP_DATA_DIR}/${BASE_NAME}.osrm"

if [ -f "$OSRM_FILE" ]; then
    echo -e "${YELLOW}Processed data already exists${NC}"
    read -p "Reprocess map data? (y/N): " reprocess
    if [[ "$reprocess" != "y" && "$reprocess" != "Y" ]]; then
        echo "Using existing processed data..."
        NEED_PROCESS=false
    else
        NEED_PROCESS=true
    fi
else
    NEED_PROCESS=true
fi

if [ "$NEED_PROCESS" = true ]; then
    echo -e "${BLUE}This process takes approximately 2-5 minutes${NC}"
    echo ""
    
    # 5.1 Extract
    echo "5.1 Extracting road network..."
    docker run --rm -t \
        -v "${MAP_DATA_DIR}:/data" \
        osrm/osrm-backend:latest \
        osrm-extract -p /opt/car.lua /data/${BASE_NAME}.osm.pbf
    echo -e "${GREEN}Extract completed${NC}"
    echo ""
    
    # 5.2 Partition
    echo "5.2 Partitioning..."
    docker run --rm -t \
        -v "${MAP_DATA_DIR}:/data" \
        osrm/osrm-backend:latest \
        osrm-partition /data/${BASE_NAME}.osrm
    echo -e "${GREEN}Partition completed${NC}"
    echo ""
    
    # 5.3 Customize
    echo "5.3 Customizing..."
    docker run --rm -t \
        -v "${MAP_DATA_DIR}:/data" \
        osrm/osrm-backend:latest \
        osrm-customize /data/${BASE_NAME}.osrm
    echo -e "${GREEN}Customize completed${NC}"
fi
echo ""

# Step 6: Start OSRM service
echo -e "${GREEN}[Step 6] Starting OSRM service...${NC}"

# Check if container already exists
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${YELLOW}Container ${CONTAINER_NAME} already exists${NC}"
    docker stop ${CONTAINER_NAME} 2>/dev/null || true
    docker rm ${CONTAINER_NAME} 2>/dev/null || true
fi

# Start new container
docker run -d \
    --name ${CONTAINER_NAME} \
    -p ${SERVICE_PORT}:5000 \
    --memory=4g \
    --restart unless-stopped \
    -v "${MAP_DATA_DIR}:/data" \
    osrm/osrm-backend:latest \
    osrm-routed --algorithm mld /data/${BASE_NAME}.osrm

echo -e "${GREEN}OSRM service started${NC}"
echo ""

# Step 7: Test service
echo -e "${GREEN}[Step 7] Testing service...${NC}"

sleep 5

# Test with a sample route (Shenzhen coordinates)
TEST_URL="http://localhost:${SERVICE_PORT}/route/v1/driving/114.057868,22.543099;114.085947,22.547?overview=false"

if curl -s "$TEST_URL" | grep -q "Ok"; then
    echo -e "${GREEN}Service test successful!${NC}"
    
    # Display route info
    RESPONSE=$(curl -s "$TEST_URL")
    DISTANCE=$(echo "$RESPONSE" | grep -o '"distance":[0-9.]*' | cut -d':' -f2)
    DURATION=$(echo "$RESPONSE" | grep -o '"duration":[0-9.]*' | cut -d':' -f2)
    
    echo ""
    echo "Test route: Futian Station -> Luohu Station"
    echo "  Distance: $(echo "scale=2; $DISTANCE / 1000" | bc) km"
    echo "  Duration: $(echo "scale=1; $DURATION / 60" | bc) min"
else
    echo -e "${YELLOW}Service may not be fully ready, please wait and test manually${NC}"
fi
echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Deployment Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "Service Information:"
echo "  Address: http://localhost:${SERVICE_PORT}"
echo "  Container: ${CONTAINER_NAME}"
echo "  Map data: ${MAP_DATA_DIR}/${BASE_NAME}.osm.pbf"
echo ""
echo "Usage:"
echo "  python3 taxi_trip_distance.py taxi_trip_clean.csv --workers 10"
echo ""
echo "Management Commands:"
echo "  docker ps                    # Check status"
echo "  docker logs ${CONTAINER_NAME}   # View logs"
echo "  docker stop ${CONTAINER_NAME}   # Stop service"
echo "  docker start ${CONTAINER_NAME}  # Start service"
echo ""
echo -e "${GREEN}Ready to calculate taxi trip distances!${NC}"