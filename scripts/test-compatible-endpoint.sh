#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

BASE_URL="${NEMOCLAW_ENDPOINT_URL:-${OPENAI_BASE_URL:-}}"
MODEL="${NEMOCLAW_MODEL:-}"
API_KEY="${COMPATIBLE_API_KEY:-${NEMOCLAW_PROVIDER_KEY:-${NVIDIA_API_KEY:-${OPENAI_API_KEY:-}}}}"

if [[ -z "$BASE_URL" ]]; then
  echo "Missing base URL. Expected NEMOCLAW_ENDPOINT_URL or OPENAI_BASE_URL in $ENV_FILE." >&2
  exit 1
fi

if [[ -z "$MODEL" ]]; then
  echo "Missing model. Expected NEMOCLAW_MODEL in $ENV_FILE." >&2
  exit 1
fi

if [[ -z "$API_KEY" ]]; then
  echo "Missing API key. Expected COMPATIBLE_API_KEY, NEMOCLAW_PROVIDER_KEY, NVIDIA_API_KEY, or OPENAI_API_KEY in $ENV_FILE." >&2
  exit 1
fi

BASE_URL="${BASE_URL%/}"
MODELS_URL="$BASE_URL/models"
CHAT_URL="$BASE_URL/chat/completions"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
MODELS_BODY="$TMP_DIR/models.json"
CHAT_BODY="$TMP_DIR/chat.json"
CHAT_REQUEST="$TMP_DIR/request.json"

cat >"$CHAT_REQUEST" <<EOF
{
  "model": "$MODEL",
  "messages": [
    {
      "role": "user",
      "content": "Reply with exactly: ok"
    }
  ],
  "max_tokens": 8,
  "temperature": 0
}
EOF

echo "Testing compatible endpoint configuration"
echo "Env file: $ENV_FILE"
echo "Base URL: $BASE_URL"
echo "Model: $MODEL"

MODELS_STATUS="$(
  curl -sS -o "$MODELS_BODY" -w '%{http_code}' \
    -H "Authorization: Bearer $API_KEY" \
    "$MODELS_URL"
)"

echo
echo "GET /models -> HTTP $MODELS_STATUS"

if [[ "$MODELS_STATUS" != "200" ]]; then
  echo "Model listing failed:" >&2
  cat "$MODELS_BODY" >&2
  exit 1
fi

node - "$MODELS_BODY" "$MODEL" <<'EOF'
const fs = require("fs");

const bodyPath = process.argv[2];
const targetModel = process.argv[3];
const payload = JSON.parse(fs.readFileSync(bodyPath, "utf8"));
const models = Array.isArray(payload.data) ? payload.data : [];
const found = models.some((entry) => entry && entry.id === targetModel);

console.log(`Models returned: ${models.length}`);
console.log(`Configured model present: ${found ? "yes" : "no"}`);
if (!found) {
  const sample = models.slice(0, 10).map((entry) => entry && entry.id).filter(Boolean);
  if (sample.length) {
    console.log(`Sample models: ${sample.join(", ")}`);
  }
}
EOF

CHAT_STATUS="$(
  curl -sS -o "$CHAT_BODY" -w '%{http_code}' \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d @"$CHAT_REQUEST" \
    "$CHAT_URL"
)"

echo
echo "POST /chat/completions -> HTTP $CHAT_STATUS"

if [[ "$CHAT_STATUS" != "200" ]]; then
  echo "Chat completion failed:" >&2
  cat "$CHAT_BODY" >&2
  exit 1
fi

node - "$CHAT_BODY" <<'EOF'
const fs = require("fs");

const bodyPath = process.argv[2];
const payload = JSON.parse(fs.readFileSync(bodyPath, "utf8"));
const choice = Array.isArray(payload.choices) ? payload.choices[0] : null;
const message = choice && choice.message && typeof choice.message.content === "string"
  ? choice.message.content.trim()
  : "";

console.log("Chat completion succeeded.");
console.log(`Reply preview: ${message || "<empty>"}`);
EOF
