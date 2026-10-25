#!/usr/bin/env bash
set -Eeuo pipefail

fail() {
  echo "DEEP33_GATE_FAIL=$*" >&2
  exit 1
}

: "${VERCEL_PROTECTION_BYPASS:?Missing GitHub Actions secret VERCEL_PROTECTION_BYPASS}"
: "${VERCEL_URL:?VERCEL_URL must point to the branch Preview alias}"
: "${CURRENT_SHA:?CURRENT_SHA must be the PR head SHA}"
: "${SUPABASE_URL:?SUPABASE_URL is required to create a CI session}"
: "${SUPABASE_PUBLISHABLE_KEY:?SUPABASE_PUBLISHABLE_KEY is required}"
VERCEL_URL="${VERCEL_URL%/}"

# Prevent sending either the Vercel bypass value or user-session bearer token
# to arbitrary hosts. curl is intentionally not configured to follow redirects.
case "$VERCEL_URL" in
  https://deep33-backend-git-fix-vercel-android-route-221026-iac33.vercel.app|https://deep33-backend-is5935p3w-iac33.vercel.app|https://deep33-backend.vercel.app) ;;
  *) fail "VERCEL_URL is not an approved DEEP33 Vercel URL" ;;
esac

TMP_DIR="${RUNNER_TEMP:-/tmp}/deep33-master-gate"
mkdir -p "$TMP_DIR"
SESSION_ID="deep33-master-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
TOKEN="${TEST_SESSION_TOKEN:-}"

# Create a short-lived session if no dedicated CI session token was supplied.
# The Vercel protection secret is never sent to Supabase or any non-Vercel host.
if [ -z "$TOKEN" ]; then
  auth_ok=0
  for attempt in 1 2 3 4 5; do
    profile_id="ci-master-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-${RANDOM}-${attempt}"
    auth_code="$(curl -sS --connect-timeout 8 --max-time 25 \
      -o "$TMP_DIR/auth.json" -w '%{http_code}' \
      -X POST "$SUPABASE_URL/functions/v1/deep33-auth" \
      -H "apikey: $SUPABASE_PUBLISHABLE_KEY" \
      -H 'Content-Type: application/json' \
      --data "{\"action\":\"session\",\"memory_profile_id\":\"$profile_id\"}" || true)"
    if [ "$auth_code" = "200" ]; then
      TOKEN="$(jq -r '.access_token // empty' "$TMP_DIR/auth.json" 2>/dev/null || true)"
      if [ -n "$TOKEN" ]; then
        auth_ok=1
        break
      fi
    fi
    echo "CI_SESSION_RETRY attempt=$attempt http=$auth_code"
    [ "$attempt" -lt 5 ] && sleep 2
  done
  [ "$auth_ok" = "1" ] || {
    jq -c '{error:(.error // .message // "session creation failed")}' "$TMP_DIR/auth.json" 2>/dev/null || true
    fail "CI_SESSION_CREATE_FAILED"
  }
fi
echo "::add-mask::$TOKEN"

HTTP_CODE="000"
request_file() {
  local name="$1"
  local url="$2"
  local timeout="$3"
  shift 3
  local args=(
    -sS --connect-timeout 8 --max-time "$timeout"
    -D "$TMP_DIR/$name.headers"
    -o "$TMP_DIR/$name.body"
    -w '%{http_code}'
    -H "x-vercel-protection-bypass: $VERCEL_PROTECTION_BYPASS"
    -H 'Cache-Control: no-cache'
    -H "Authorization: Bearer $TOKEN"
    -H "apikey: $SUPABASE_PUBLISHABLE_KEY"
    -H "X-DEEP33-Session-Id: $SESSION_ID"
    -H "X-Request-ID: deep33-master-${GITHUB_RUN_ID:-local}-$name"
  )
  HTTP_CODE="$(curl "${args[@]}" "$@" "$url" || true)"
  echo "HTTP_CHECK name=$name status=$HTTP_CODE"
}

show_redirect() {
  local location
  location="$(awk 'tolower($0) ~ /^location:/ {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); print}' "$TMP_DIR/$1.headers" 2>/dev/null | tail -n 1 | sed 's/[?#].*$//')"
  [ -n "$location" ] && echo "REDIRECT_LOCATION=$location"
}

# Wait for Vercel to activate the immutable deployment of this exact PR SHA.
health_pass=0
for attempt in $(seq 1 30); do
  request_file health "$VERCEL_URL/health" 25
  if [ "$HTTP_CODE" = "302" ] || [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
    show_redirect health
    fail "VERCEL_PREVIEW_PROTECTION_REJECTED secret_or_protection_configuration"
  fi
  if [ "$HTTP_CODE" = "200" ] && jq -e --arg sha "$CURRENT_SHA" '
    .status == "PASS" and
    (.git_sha | type == "string") and
    ((.git_sha | ascii_downcase) == ($sha | ascii_downcase)) and
    (.build_id | type == "string" and length > 0)
  ' "$TMP_DIR/health.body" >/dev/null 2>&1; then
    jq -c '{status,service,git_sha,build_id,git_branch}' "$TMP_DIR/health.body"
    echo "HEALTH_SHA_PASS expected=$CURRENT_SHA"
    health_pass=1
    break
  fi
  jq -c '{status,service,git_sha,build_id,git_branch,detail}' "$TMP_DIR/health.body" 2>/dev/null || true
  echo "WAITING_FOR_PREVIEW_SHA attempt=$attempt expected=$CURRENT_SHA"
  [ "$attempt" -lt 30 ] && sleep 10
done
[ "$health_pass" = "1" ] || fail "HEALTH_SHA_MISMATCH_OR_DEPLOYMENT_NOT_READY expected=$CURRENT_SHA"

request_file ready "$VERCEL_URL/ready" 30
[ "$HTTP_CODE" = "200" ] || { show_redirect ready; cat "$TMP_DIR/ready.body"; fail "READY_HTTP_$HTTP_CODE"; }
jq -e --arg sha "$CURRENT_SHA" '
  .status == "PASS" and .ready == true and
  ((.git_sha | ascii_downcase) == ($sha | ascii_downcase))
' "$TMP_DIR/ready.body" >/dev/null || fail "READY_CONTRACT_OR_SHA_MISMATCH"
jq -c '{status,ready,git_sha,network,gateway,memory_ready}' "$TMP_DIR/ready.body"
echo "READY_PASS"

request_file web_status "$VERCEL_URL/v1/web/status" 25
[ "$HTTP_CODE" = "200" ] || { show_redirect web_status; cat "$TMP_DIR/web_status.body"; fail "WEB_STATUS_HTTP_$HTTP_CODE"; }
jq -e '.engine == "DEEP33 Search Engine" and .tool_loop_enabled == true' "$TMP_DIR/web_status.body" >/dev/null ||
  fail "WEB_STATUS_CONTRACT_FAIL"
jq -c '{enabled,engine,engine_version,configured_provider,active_provider,parallel_providers,tool_loop_enabled,realtime_policy_enabled}' "$TMP_DIR/web_status.body"
echo "WEB_STATUS_PASS"

request_file web_search "$VERCEL_URL/v1/web/search" 75 \
  --get --data-urlencode 'q=latest stable Python release official documentation'
[ "$HTTP_CODE" = "200" ] || { show_redirect web_search; cat "$TMP_DIR/web_search.body"; fail "WEB_SEARCH_HTTP_$HTTP_CODE"; }
jq -e '
  .ok == true and
  ((.results // []) | length) >= 1 and
  ((.fetched_pages // []) | length) >= 1 and
  ((.sources // .results // []) | length) >= 1 and
  ([.results[]? | select((.url // "") | startswith("https://"))] | length) >= 1 and
  ([.fetched_pages[]? | select((.url // .final_url // "") | startswith("https://"))] | length) >= 1 and
  (.domains_used // 0) >= 1 and
  (.extracted_text_chars // 0) >= 80
' "$TMP_DIR/web_search.body" >/dev/null || {
  jq -c '{ok,status,code,errors,providers,domains_used,extracted_text_chars,results_count:((.results // [])|length),fetched_pages_count:((.fetched_pages // [])|length),sources_count:((.sources // .results // [])|length)}' "$TMP_DIR/web_search.body" 2>/dev/null || true
  fail "SEARCH=FAIL web_navigation_or_extraction_not_verified"
}
jq -c '{ok,realtime,providers,domains_used,extracted_text_chars,results_count:((.results // [])|length),fetched_pages_count:((.fetched_pages // [])|length),sources_count:((.sources // .results // [])|length),first_source:((.results // [])[0] // {} | {title,url,source_domain})}' "$TMP_DIR/web_search.body"
echo "SEARCH=PASS web_navigation=true results>=1 fetched_pages>=1 sources>=1"

request_file chat_stream "$VERCEL_URL/v1/chat/stream" 55 \
  --no-buffer -X POST \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  -H "X-Idempotency-Key: deep33-master-stream-${GITHUB_RUN_ID:-local}" \
  -H 'X-DEEP33-Personality: NEUTRO' \
  -H 'x-deep33-skip-web-tools: true' \
  --data '{"messages":[{"role":"user","content":"Responde únicamente con la palabra STREAM_PASS. No busques en Internet."}],"personality":"NEUTRO"}'
[ "$HTTP_CODE" = "200" ] || { show_redirect chat_stream; tail -c 3000 "$TMP_DIR/chat_stream.body" 2>/dev/null || true; fail "CHAT_STREAM_HTTP_$HTTP_CODE"; }
grep -qi '^content-type:.*text/event-stream' "$TMP_DIR/chat_stream.headers" ||
  fail "CHAT_STREAM_CONTENT_TYPE_NOT_SSE"
grep -Fq 'STREAM_PASS' "$TMP_DIR/chat_stream.body" || {
  tail -c 3000 "$TMP_DIR/chat_stream.body" 2>/dev/null || true
  fail "CHAT_STREAM_EMPTY_OR_EXPECTED_TEXT_MISSING"
}
echo "CHAT_STREAM_PASS first_visible_text=true"

echo "DEEP33_MASTER_PIPELINE_PASS sha=$CURRENT_SHA"
