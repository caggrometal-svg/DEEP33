#!/usr/bin/env bash
set -euo pipefail

run_gradle_with_transient_retry() {
  local attempt=1
  local max_attempts=3
  local log_file
  while [ "$attempt" -le "$max_attempts" ]; do
    log_file="$(mktemp)"
    echo "GRADLE_ATTEMPT=$attempt/$max_attempts TASKS=$*"
    if gradle "$@" --no-daemon --max-workers=2 2>&1 | tee "$log_file"; then
      rm -f "$log_file"
      return 0
    fi
    if ! grep -Eqi 'HTTP.*429|status code 429|Too Many Requests|Could not GET.*(maven|gradle)' "$log_file"; then
      echo "GRADLE_NON_TRANSIENT_FAILURE"
      rm -f "$log_file"
      return 1
    fi
    echo "GRADLE_REPOSITORY_RATE_LIMIT attempt=$attempt"
    rm -f "$log_file"
    if [ "$attempt" -eq "$max_attempts" ]; then
      echo "GRADLE_RATE_LIMIT_RETRIES_EXHAUSTED"
      return 1
    fi
    sleep $((attempt * 20))
    attempt=$((attempt + 1))
  done
}

run_gradle_with_transient_retry :app:assembleDebug :app:assembleDebugAndroidTest
run_gradle_with_transient_retry :app:connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=cl.caggrometal.deep33.Deep33UiContractV2Test,cl.caggrometal.deep33.Deep33FeaturePersistenceTest,cl.caggrometal.deep33.Deep33LifecycleRecoveryTest,cl.caggrometal.deep33.SessionStorePersistenceTest
