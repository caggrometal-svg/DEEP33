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
: "${DEEP33_PRIMARY_URL:=https://deep33-backend.onrender.com}"
: "${DEEP33_SECONDARY_URL:=https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-proxy}"
: "${DEEP33_TERTIARY_URL:=https://opocgzydeknuchtrqzfa.supabase.co/functions/v1/deep33-tertiary}"
for url in "${DEEP33_PRIMARY_URL}" "${DEEP33_SECONDARY_URL}" "${DEEP33_TERTIARY_URL}"; do
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
set +e
timeout --foreground 15m adb shell am instrument -w -r \
  cl.caggrometal.deep33.test/androidx.test.runner.AndroidJUnitRunner \
  > /tmp/deep33-e2e/instrumentation.log 2>&1
instrument_status=$?
cat /tmp/deep33-e2e/instrumentation.log
set -e

cp -R app/build/outputs/androidTest-results /tmp/deep33-e2e/connected-results 2>/dev/null || true

# Android's am instrument can return a non-zero process code even after the
# runner reports a clean test suite (e.g. INSTRUMENTATION_CODE: -1).
# Certification is based on the test runner result itself: all tests must end
# with an OK summary and no explicit failure marker.
if grep -q "FAILURES!!!" /tmp/deep33-e2e/instrumentation.log ||
   ! grep -Eq "OK \([0-9]+ tests\)" /tmp/deep33-e2e/instrumentation.log; then
  echo "ANDROID_INSTRUMENTATION_FAIL"
  echo "ANDROID_INSTRUMENTATION_PROCESS_STATUS=${instrument_status}"
  exit 1
fi

echo "ANDROID_INSTRUMENTATION_PASS tests_verified"
