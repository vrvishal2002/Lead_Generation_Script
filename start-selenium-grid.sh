#!/bin/bash
# nano start-selenium-grid.sh
# chmod +x start-selenium-grid.sh
# docker ps

# Stop and remove old containers if they exist
docker stop selenium-hub chrome-node-1 chrome-node-2 chrome-node-3 2>/dev/null
docker rm selenium-hub chrome-node-1 chrome-node-2 chrome-node-3 2>/dev/null

# Create Docker network if it doesn't exist
docker network inspect selenium-grid >/dev/null 2>&1 || docker network create selenium-grid

# Start Selenium Hub
docker run -d --name selenium-hub --network selenium-grid -p 4444:4444 selenium/hub:latest

# Wait a few seconds for Hub to initialize
sleep 5

# Start Chrome nodes
for i in 1 2 3 4; do
  docker run -d --name chrome-node-$i --network selenium-grid \
    -e SE_EVENT_BUS_HOST=selenium-hub \
    -e SE_EVENT_BUS_PUBLISH_PORT=4442 \
    -e SE_EVENT_BUS_SUBSCRIBE_PORT=4443 \
    selenium/node-chrome:latest
done

echo "Selenium Grid started: Hub + 3 Chrome nodes"
echo "Open http://<EC2_PUBLIC_IP>:4444/grid/console to verify"