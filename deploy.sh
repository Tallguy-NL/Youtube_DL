#!/bin/bash
set -e

APP_DIR="/opt/stacks/youtube_dl"
APP_NAME="youtube_dl"

echo "Deploying $APP_NAME..."

cd "$APP_DIR"

git pull origin master

docker build --network host --no-cache -t youtube_dl_youtube_dl:latest .
docker compose down
docker compose up -d --no-build
docker image prune -f

echo "$APP_NAME deployed!"
