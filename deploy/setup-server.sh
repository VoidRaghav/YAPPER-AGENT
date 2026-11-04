#!/usr/bin/env bash
set -euo pipefail

REPO="${YAPPER_REPO:-https://github.com/VoidRaghav/YAPPER-AGENT.git}"
DIR="$HOME/YAPPER-AGENT"

if ! command -v git >/dev/null || ! command -v curl >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq git curl
fi

if ! command -v docker >/dev/null; then
  echo "installing docker"
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo systemctl enable --now docker >/dev/null
sudo usermod -aG docker "$USER"

ram_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
if [ "$ram_mb" -lt 4000 ] && [ -z "$(swapon --show --noheadings)" ]; then
  echo "adding 2 GB swap"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only -q
else
  git clone -q "$REPO" "$DIR"
fi
mkdir -p "$DIR/data" "$DIR/gateway/auth"

echo "server ready: $DIR"
