from backend import main

def test_runtime_resilience_contract():
    assert main.APP_NAME == "DEEP33 Backend"
    assert main.APP_VERSION == "0.2.0"
    assert main.GIT_SHA
    assert main.gateway.config.provider_timeout_seconds >= 3
    assert main.gateway.config.max_retries >= 0
    assert main.gateway.config.circuit_failure_threshold >= 1
