#!/usr/bin/env bash
set -euo pipefail

mkdir -p /tmp/deep33-e2e

echo "=== ADB DEVICE ==="
for i in $(seq 1 60); do
  state="$(adb get-state 2>/dev/null || true)"
  if [ "$state" = "device" ]; then
    echo "ADB_DEVICE_READY attempt=$i state=$state"
    break
  fi
  echo "ADB_WAIT attempt=$i state=$state"
  adb reconnect offline >/dev/null 2>&1 || true
  adb devices -l || true
  sleep 3
done
test "$(adb get-state 2>/dev/null || true)" = "device"

echo "=== EMULATOR DNS ==="
adb shell getprop net.dns1 || true
adb shell getprop net.dns2 || true

echo "=== ENDPOINT PREFLIGHT ==="
: "${DEEP33_PRIMARY_URL:=https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy}"
: "${DEEP33_SECONDARY_URL:=https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary}"
: "${DEEP33_TERTIARY_URL:=https://deep33-backend.onrender.com}"
urls=("${DEEP33_PRIMARY_URL}")
[ -n "${DEEP33_SECONDARY_URL}" ] && urls+=("${DEEP33_SECONDARY_URL}")
[ -n "${DEEP33_TERTIARY_URL}" ] && urls+=("${DEEP33_TERTIARY_URL}")
for url in "${urls[@]}"; do
  host="${url#https://}"
  host="${host%%/*}"
  echo "DNS_CHECK ${host}"
  getent ahosts "${host}"
done

echo "=== GRADLE BUILD ==="
gradle :app:assembleDebug :app:assembleDebugAndroidTest --no-daemon --stacktrace

echo "=== INSTALL APP ==="
adb install -r app/build/outputs/apk/debug/app-debug.apk

TEST_APK="$(find app/build/outputs/apk/androidTest -type f -name '*androidTest.apk' | head -n 1)"
test -n "$TEST_APK"
adb install -r "$TEST_APK"

echo "=== ANDROID CONNECTIVITY INSTRUMENTATION ==="
INSTRUMENTATION_ARGS=(-w -r)
if [ -n "${DEEP33_INSTRUMENTATION_CLASS:-}" ]; then
  INSTRUMENTATION_ARGS+=(-e class "${DEEP33_INSTRUMENTATION_CLASS}")
  echo "INSTRUMENTATION_CLASS_FILTER=${DEEP33_INSTRUMENTATION_CLASS}"
else
  echo "INSTRUMENTATION_CLASS_FILTER=ALL"
fi

set +e
timeout --foreground 15m adb shell am instrument "${INSTRUMENTATION_ARGS[@]}" \
  cl.caggrometal.deep33.test/androidx.test.runner.AndroidJUnitRunner \
  > /tmp/deep33-e2e/instrumentation.log 2>&1
instrument_status=$?
cat /tmp/deep33-e2e/instrumentation.log
set -e

cp -R app/build/outputs/androidTest-results /tmp/deep33-e2e/connected-results 2>/dev/null || true

# Android's am instrument can return a non-zero process code even after the
# runner reports a clean test suite. Certification is based on the runner
# result itself: all selected tests must end with an OK summary and no failure.
if grep -q "FAILURES!!!" /tmp/deep33-e2e/instrumentation.log ||
   ! grep -Eq "OK \([0-9]+ tests\)" /tmp/deep33-e2e/instrumentation.log; then
  echo "ANDROID_INSTRUMENTATION_FAIL"
  echo "ANDROID_INSTRUMENTATION_PROCESS_STATUS=${instrument_status}"
  exit 1
fi

echo "ANDROID_INSTRUMENTATION_PASS tests_verified"
