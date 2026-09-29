import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const UPSTREAM = "https://deep33.c-33.blitz.cloud";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type, x-deep33-session-id, x-request-id, x-idempotency-key",
  "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
};

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      ...cors,
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });

function normalizePath(pathname: string) {
  return pathname
    .replace(/^\/functions\/v1\/(?:deep33-proxy|deep33-tertiary)/, "")
    .replace(/^\/(?:deep33-proxy|deep33-tertiary)/, "") || "/";
}

function headerSubset(req: Request) {
  const headers = new Headers();
  for (const name of [
    "content-type",
    "accept",
    "x-deep33-session-id",
    "x-request-id",
    "x-idempotency-key",
  ]) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

async function fetchUpstream(
  path: string,
  init: RequestInit = {},
  sessionId = "deep33-edge",
) {
  const headers = new Headers(init.headers || {});
  headers.delete("Authorization");
  headers.delete("apikey");
  headers.delete("x-client-info");
  if (!headers.has("x-deep33-session-id")) {
    headers.set("x-deep33-session-id", sessionId);
  }
  return fetch(UPSTREAM + path, {
    ...init,
    headers,
    redirect: "follow",
  });
}

async function readJson(res: Response): Promise<Record<string, unknown>> {
  return (await res.json().catch(() => ({}))) as Record<string, unknown>;
}

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SECRET_KEYS = (() => {
  try {
    return JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") ?? "{}");
  } catch {
    return {};
  }
})();
const SUPABASE_SECRET_KEY =
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
  SUPABASE_SECRET_KEYS?.default ??
  "";

const MEMORY_FUNCTION_URL =
  SUPABASE_URL.replace(/\/$/, "") + "/functions/v1/deep33-memory";

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function copyResponseHeaders(source: Response): Headers {
  const headers = new Headers(cors);
  for (const name of ["content-type", "cache-control", "x-request-id"]) {
    const value = source.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

async function memoryCall(
  action: string,
  sessionId: string,
  payload: Record<string, unknown> = {},
): Promise<{ status: number; body: Record<string, unknown> }> {
  if (!MEMORY_FUNCTION_URL || !SUPABASE_SECRET_KEY) {
    throw new Error("DEEP33_MEMORY_EDGE_NOT_CONFIGURED");
  }

  const needsIdempotency =
    action === "sync" || action === "remember" || action === "preferences";
  const requestBody: Record<string, unknown> = {
    action,
    session_id: sessionId,
    ...payload,
  };

  if (needsIdempotency) {
    const requestHash = await sha256(JSON.stringify(requestBody));
    requestBody.idempotency_key = "edge:" + action + ":" + requestHash;
    requestBody.request_hash = requestHash;
  }

  const response = await fetch(MEMORY_FUNCTION_URL, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + SUPABASE_SECRET_KEY,
      apikey: SUPABASE_SECRET_KEY,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(requestBody),
  });

  const body = await readJson(response);
  if (!response.ok) {
    throw new Error("DEEP33_MEMORY_HTTP_" + response.status);
  }
  return { status: response.status, body };
}

async function handleMemoryRequest(
  req: Request,
  path: string,
  sessionId: string,
): Promise<Response> {
  if (path === "/v1/memory/context" && req.method === "GET") {
    const result = await memoryCall("context", sessionId);
    return json(result.body, result.status);
  }

  if (path === "/v1/memory/remember" && req.method === "POST") {
    const payload = (await req.json().catch(() => ({}))) as Record<string, unknown>;
    const result = await memoryCall("remember", sessionId, {
      kind: payload.kind,
      content: payload.content,
    });
    return json(result.body, result.status);
  }

  if (path === "/v1/memory/preferences" && req.method === "PUT") {
    const payload = (await req.json().catch(() => ({}))) as Record<string, unknown>;
    const result = await memoryCall("preferences", sessionId, {
      personality: payload.personality,
      preferences: payload.preferences,
    });
    return json(result.body, result.status);
  }

  if (path === "/v1/memory/sync" && req.method === "POST") {
    const payload = (await req.json().catch(() => ({}))) as Record<string, unknown>;
    const result = await memoryCall("sync", sessionId, {
      messages: Array.isArray(payload.messages) ? payload.messages : [],
      personality: payload.personality,
      preferences: payload.preferences,
    });
    return json(result.body, result.status);
  }

  return json({ error: "MEMORY_ROUTE_NOT_FOUND" }, 404);
}

async function probeHealth(sessionId: string) {
  const response = await fetchUpstream("/health", {}, sessionId);
  const body = await readJson(response);
  return {
    ok: response.ok && body.status === "PASS",
    status: body.status ?? (response.ok ? "PASS" : "FAIL"),
    body,
  };
}

async function probeInference(sessionId: string) {
  const response = await fetchUpstream(
    "/v1/ai/inference-check",
    {},
    sessionId,
  );
  const body = await readJson(response);
  return {
    ok:
      response.ok &&
      body.status === "PASS" &&
      body.text_ok === true,
    status: body.status ?? (response.ok ? "PASS" : "FAIL"),
    body,
  };
}

async function probeMemory(sessionId: string) {
  const response = await fetchUpstream(
    "/v1/memory/context",
    {},
    sessionId,
  );
  return {
    ok: response.ok,
    status: response.ok ? "PASS" : "FAIL",
    http_status: response.status,
  };
}

async function edgeSearch(query: string, sessionId = "deep33-edge-search") {
  const q = query.trim();
  if (!q) return { ok: false, error: "SEARCH_QUERY_REQUIRED", results: [] };
  const res = await fetchUpstream(
    "/v1/web/search?q=" + encodeURIComponent(q),
    {},
    sessionId,
  );
  const data = await readJson(res);
  if (!res.ok) {
    return {
      ok: false,
      error: "SEARCH_HTTP_" + res.status,
      results: [],
      upstream: data,
    };
  }
  return data;
}

async function readinessResponse(sessionId: string) {
  const [health, inference] = await Promise.all([
    probeHealth(sessionId),
    probeInference(sessionId),
  ]);

  const ready = health.ok && inference.ok;

  return {
    status: ready ? "PASS" : "FAIL",
    ready,
    service: "DEEP33 AI Edge",
    upstream: UPSTREAM,
    health: health.body,
    inference: inference.body,
    checks: {
      BACKEND: health.ok ? "PASS" : "FAIL",
      MODEL: inference.ok ? "PASS" : "FAIL",
      CHAT: inference.ok ? "PASS" : "FAIL",
    },
    timestamp: new Date().toISOString(),
  };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: cors });
  }

  const url = new URL(req.url);
  const path = normalizePath(url.pathname);
  const sessionId =
    req.headers.get("x-deep33-session-id")?.trim() ||
    "deep33-mobile";

  try {
    if (path === "/ready" && req.method === "GET") {
      const body = await readinessResponse(sessionId);
      return json(body, body.ready ? 200 : 503);
    }

    if (
      path.startsWith("/v1/memory/") &&
      ["GET", "POST", "PUT"].includes(req.method)
    ) {
      return await handleMemoryRequest(req, path, sessionId);
    }

    if (path === "/v1/connectivity/audit" && req.method === "GET") {
      const started = performance.now();
      const auditSession = sessionId || "deep33-audit";
      const [health, inference, webStatus] = await Promise.all([
        probeHealth(auditSession),
        probeInference(auditSession),
        fetchUpstream("/v1/web/status", {}, auditSession).then(readJson),
      ]);

      let search: Record<string, unknown>;
      try {
        search = await edgeSearch("DEEP33 internet", auditSession);
      } catch (error) {
        search = {
          ok: false,
          error: error instanceof Error ? error.message : String(error),
          results: [],
        };
      }

      let memory = { ok: false, status: "FAIL", http_status: 0 };
      try {
        memory = await probeMemory(auditSession);
      } catch (error) {
        memory = {
          ok: false,
          status: "FAIL",
          http_status: 0,
        };
      }

      const aiReady = inference.ok;
      const searchOk = search.ok === true;
      const backendOk = health.ok;

      return json({
        status: backendOk && aiReady && searchOk ? "PASS" : "FAIL",
        edge: "PASS",
        internet: searchOk ? "PASS" : "FAIL",
        ready: backendOk && aiReady ? "PASS" : "FAIL",
        search,
        memory,
        upstream: {
          health: backendOk,
          ready: aiReady,
          web_status: webStatus,
        },
        latency_ms: Math.round(performance.now() - started),
        timestamp: new Date().toISOString(),
      });
    }

    if (path === "/v1/search" && req.method === "GET") {
      return json(
        await edgeSearch(
          url.searchParams.get("q") ||
            url.searchParams.get("query") ||
            "",
          sessionId,
        ),
      );
    }

    if (
      path.startsWith("/v1/") ||
      path === "/health" ||
      path === "/metrics"
    ) {
      const body =
        req.method === "GET" || req.method === "HEAD"
          ? undefined
          : await req.arrayBuffer();

      const upstream = await fetchUpstream(
        path + url.search,
        {
          method: req.method,
          headers: headerSubset(req),
          body,
        },
        sessionId,
      );

      const out = new Headers(cors);
      for (const name of [
        "content-type",
        "cache-control",
        "x-request-id",
      ]) {
        const value = upstream.headers.get(name);
        if (value) out.set(name, value);
      }

      return new Response(upstream.body, {
        status: upstream.status,
        headers: out,
      });
    }

    return json({
      status: "PASS",
      service: "DEEP33 Internet Edge",
      path,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    return json(
      {
        status: "FAIL",
        error: error instanceof Error ? error.message : String(error),
        path,
        timestamp: new Date().toISOString(),
      },
      502,
    );
  }
});
