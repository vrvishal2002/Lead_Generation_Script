#!/bin/bash
# Selenium Grid 4 — Azure VM startup script
#
# Run once after SSH-ing into your Azure VM:
#   chmod +x start-selenium-grid-azure.sh
#   ./start-selenium-grid-azure.sh [chrome_nodes]     # default: 3 nodes
#
# Scale nodes later without downtime:
#   docker compose -f selenium-grid.yml up --scale chrome=6 -d
#
# Verify grid is healthy:
#   curl http://localhost:4444/status | python3 -m json.tool
#   Open http://<AZURE_VM_PUBLIC_IP>:4444/ui  in a browser

set -e

CHROME_NODES=${1:-3}

# Azure Instance Metadata Service (IMDS) — requires the Metadata:true header
_get_public_ip() {
    curl -sf \
        -H "Metadata:true" \
        "http://169.254.169.254/metadata/instance/network/interface/0/ipv4/ipAddress/0/publicIpAddress?api-version=2021-02-01&format=text" \
        2>/dev/null || hostname -I | awk '{print $1}'
}

echo "==> Stopping any existing Selenium Grid containers..."
docker compose -f selenium-grid.yml down 2>/dev/null || true

echo "==> Generating selenium-grid.yml..."
cat > selenium-grid.yml <<EOF
version: "3.8"

services:
  selenium-hub:
    image: selenium/hub:4.27.0
    container_name: selenium-hub
    ports:
      - "4444:4444"
      - "4442:4442"
      - "4443:4443"
    environment:
      - SE_SESSION_RETRY_INTERVAL=5
      - SE_SESSION_REQUEST_TIMEOUT=300
      - SE_SESSION_QUEUE_TIMEOUT=300
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4444/status"]
      interval: 15s
      timeout: 10s
      retries: 5

  chrome:
    image: selenium/node-chrome:4.27.0
    shm_size: "2gb"
    depends_on:
      selenium-hub:
        condition: service_healthy
    environment:
      - SE_EVENT_BUS_HOST=selenium-hub
      - SE_EVENT_BUS_PUBLISH_PORT=4442
      - SE_EVENT_BUS_SUBSCRIBE_PORT=4443
      - SE_NODE_MAX_SESSIONS=2
      - SE_NODE_SESSION_TIMEOUT=300
      - SE_NODE_OVERRIDE_MAX_SESSIONS=true
    restart: unless-stopped
EOF

echo "==> Starting Selenium Hub..."
docker compose -f selenium-grid.yml up -d selenium-hub

echo "==> Waiting for Hub to become healthy..."
until curl -sf http://localhost:4444/status > /dev/null; do
  sleep 3
done

echo "==> Starting ${CHROME_NODES} Chrome node(s)..."
docker compose -f selenium-grid.yml up -d --scale chrome="${CHROME_NODES}" chrome

PUBLIC_IP=$(_get_public_ip)

echo ""
echo "Selenium Grid 4 is running."
echo "  Hub UI  : http://${PUBLIC_IP}:4444/ui"
echo "  Status  : curl http://localhost:4444/status"
echo ""
echo "  Set this env var in your app:"
echo "  SELENIUM_HUB_URL=http://${PUBLIC_IP}:4444"
echo ""
echo "  NOTE: Open port 4444 in your Azure VM Network Security Group (NSG)"
echo "        if you need to access the Hub UI from outside the VM."
echo ""
echo "  To scale nodes:  docker compose -f selenium-grid.yml up --scale chrome=6 -d"
echo "  To stop all   :  docker compose -f selenium-grid.yml down"
