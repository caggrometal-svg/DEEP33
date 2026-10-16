from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TERTIARY = (ROOT / "supabase/functions/deep33-tertiary/index.ts").read_text(encoding="utf-8")


def test_tertiary_uses_shared_generation_idempotency_contract() -> None:
    assert 'operation: "deep33.ai.generate"' in TERTIARY
    assert "canonicalIdempotencyJson" in TERTIARY
    assert "Match backend/main.py generation_request_hash()" in TERTIARY
    assert '"ai.chat.stream"' not in TERTIARY
    assert '"ai.generate"' not in TERTIARY


def test_tertiary_delegates_child_backend_idempotency_key() -> None:
    assert "delegatedIdempotencyKey" in TERTIARY
    assert '"edge-render:" + await sha256("deep33|render-fallback|" + idempotencyKey)' in TERTIARY
    assert '"X-Idempotency-Key": delegatedIdempotencyKey' in TERTIARY


def test_tertiary_names_railway_backend_explicitly() -> None:
    assert '"railway-backend-fallback"' in TERTIARY
    assert '"render-backend-fallback"' in TERTIARY
    assert '"render-api-fallback"' in TERTIARY
    assert '"render-backup-fallback"' in TERTIARY

def test_edge_realtime_news_keeps_publisher_provenance_and_ignores_time_fillers() -> None:
    proxy = (ROOT / "supabase/functions/deep33-proxy/index.ts").read_text(encoding="utf-8")
    assert '"horas","hour","hours"' in proxy
    assert 'publisherUrlMatch = itemXml.match(/<source\\b' in proxy
    assert 'source_domain: String(item.source_domain ?? "").slice(0, 255)' in proxy
    assert 'publisherHost.endsWith(".cl")' in proxy
    assert 'source_domain: String(result.source_domain || "").slice(0, 255)' in proxy
    assert 'const publisherDomain = String(item.source_domain || "")' in proxy

