#!/bin/bash
set -xe

THIS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$THIS_DIR"/vars.sh

cd "$PROJECT_DIR"

DATE_UTC=$(date -u)
TIMESTAMP_UTC=$(date +%s)
COMMIT_HASH=$(git rev-parse --short HEAD || echo -n "local")

echo "Building Backend: ${IMAGE_NAME}"

./setup-prod.sh

# A cached apt layer would keep the ca-certificates of an earlier build.
DOCKER_BUILDKIT=1 docker build \
  -f app/Dockerfile \
  --pull \
  --no-cache \
  --progress plain \
  --platform linux/amd64 \
  -t "${IMAGE_NAME}" \
  --label build_date_utc="$DATE_UTC" \
  --label build_timestamp_utc="$TIMESTAMP_UTC" \
  --label git_commit_hash="$COMMIT_HASH" \
  .
docker tag "${IMAGE_NAME}":latest "${IMAGE_NAME}":"${COMMIT_HASH}"

docker push "${IMAGE_NAME}":"${COMMIT_HASH}"
