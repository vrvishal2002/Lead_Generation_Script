#!/bin/bash
set -e

# Install Docker
apt-get update -y
apt-get install -y docker.io docker-compose-plugin git
systemctl start docker
systemctl enable docker
usermod -aG docker ubuntu

# Ready signal
touch /home/ubuntu/setup-done
