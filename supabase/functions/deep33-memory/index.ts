import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

type ActionResult = { body: unknown; status: number };

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-headers":
        "authorization, x-client-info, apikey, content-type, x-idempotency-key, x-deep33-internal-token",
      "access-control-allow-methods": "POST, OPTIONS",
      "content-type": "application/json; charset=utf-8",
    },
  });
}

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function requireHybridInternalToken(req: Request): Promise<boolean> {
  const supplied = req.headers.get("x-deep33-internal-token")?.trim();
  if (!supplied || supplied.length < 24) return false;
  const { data, error } = await supabase
    .schema("private")
    .from("deep33_runtime_secrets")
    .select("secret_hash")
    .eq("name", "hybrid_search")
    .maybeSingle();
  if (error || !data?.secret_hash) return false;
  return (await sha256(supplied)) === data.secret_hash;
}

async function handleHybridAction(body: Record<string, unknown>, req: Request): Promise<Response> {
  if (!(await requireHybridInternalToken(req))) {
    return response({ error: "HYBRID_AUTH_REQUIRED" }, 401);
  }
  const action = String(body.action || "");
  if (action === "hybrid_search") {
    const query = String(body.query || "").trim().slice(0, 2000);
    const embedding = Array.isArray(body.embedding)
      ? "[" + body.embedding.map((value) => Number(value)).join(",") + "]"
      : body.embedding ? String(body.embedding) : null;
    const limit = Math.max(1, Math.min(20, Number(body.limit || 8)));
    const metadataFilter = typeof body.metadata_filter === "object" && body.metadata_filter !== null
      ? body.metadata_filter
      : {};
    const { data, error } = await supabase.rpc("search_deep33_chunks", {
      p_query: query,
      p_embedding: embedding,
      p_limit: limit,
      p_metadata_filter: metadataFilter,
      p_rrf_k: 60,
    });
    if (error) throw error;
    return response({ ok: Array.isArray(data) && data.length > 0, engine: "DEEP33 Hybrid Search", rows: Array.isArray(data) ? data : [] });
  }
  if (action === "hybrid_index") {
    const documentId = String(body.document_id || "").trim().slice(0, 200);
    const chunks = Array.isArray(body.chunks) ? body.chunks : [];
    if (!documentId || !chunks.length || chunks.length > 200) return response({ error: "HYBRID_INDEX_INPUT_INVALID" }, 400);
    const { data, error } = await supabase.rpc("index_deep33_chunks", { p_document_id: documentId, p_chunks: chunks });
    if (error) throw error;
    return response({ ok: true, engine: "DEEP33 Hybrid Search", indexed: data?.[0]?.indexed_chunks ?? 0 });
  }
  return response({ error: "UNKNOWN_HYBRID_ACTION" }, 400);
}

async function scopedIdempotencyKey(sessionId: string, key: string): Promise<string> {
  return sha256(`deep33|${sessionId}|${key}`);
}

async function fetchIdempotency(scopedKey: string) {
  const { data, error } = await supabase
    .from("ai_idempotency")
    .select("idempotency_key, operation, status_code, response, request_hash, created_at, expires_at")
    .eq("idempotency_key", scopedKey)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function claimIdempotency(
  sessionId: string,
  key: string,
  operation: string,
  requestHash: string,
  leaseSeconds = 180,
) {
  if (!key || !requestHash) throw new Error("IDEMPOTENCY_ARGUMENTS_REQUIRED");
  const scopedKey = await scopedIdempotencyKey(sessionId, key);
  const now = new Date();
  const expires = new Date(now.getTime() + leaseSeconds * 1000).toISOString();

  await supabase
    .from("ai_idempotency")
    .delete()
    .eq("idempotency_key", scopedKey)
    .lt("expires_at", now.toISOString());

  const leaseToken = crypto.randomUUID();
  const { error: insertError } = await supabase
    .from("ai_idempotency")
    .insert({
      idempotency_key: scopedKey,
      operation,
      status_code: 102,
      response: { lease_token: leaseToken },
      request_hash: requestHash,
      expires_at: expires,
    });

  if (!insertError) {
    return { state: "CLAIMED", lease_token: leaseToken };
  }

  if (String(insertError.code || "") !== "23505") {
    throw insertError;
  }

  const existing = await fetchIdempotency(scopedKey);
  if (!existing) {
    return { state: "CLAIMED", lease_token: leaseToken };
  }
  if (existing.request_hash && existing.request_hash !== requestHash) {
    return { state: "CONFLICT" };
  }
  if (existing.status_code === 102) {
    return { state: "IN_PROGRESS" };
  }
  if (existing.status_code >= 400) {
    return {
      state: "FAILED",
      status_code: existing.status_code,
      response: existing.response,
    };
  }
  return {
    state: "COMPLETED",
    status_code: existing.status_code,
    response: existing.response,
  };
}

async function completeIdempotency(
  sessionId: string,
  key: string,
  requestHash: string,
  leaseToken: string,
  statusCode: number,
  body: unknown,
) {
  const scopedKey = await scopedIdempotencyKey(sessionId, key);
  const existing = await fetchIdempotency(scopedKey);
  if (!existing) throw new Error("IDEMPOTENCY_MISSING");
  if (existing.request_hash && existing.request_hash !== requestHash) {
    throw new Error("IDEMPOTENCY_KEY_REUSED");
  }
  const token = existing.status_code === 102 && existing.response?.lease_token;
  if (token !== leaseToken) throw new Error("IDEMPOTENCY_LEASE_MISMATCH");

  const { error } = await supabase
    .from("ai_idempotency")
    .update({
      status_code: statusCode,
      response: body,
      expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
    })
    .eq("idempotency_key", scopedKey)
    .eq("status_code", 102);
  if (error) throw error;
}

async function statusIdempotency(
  sessionId: string,
  key: string,
  requestHash: string,
) {
  const scopedKey = await scopedIdempotencyKey(sessionId, key);
  const existing = await fetchIdempotency(scopedKey);
  if (!existing) return { state: "MISSING" };
  if (existing.request_hash && existing.request_hash !== requestHash) {
    return { state: "CONFLICT" };
  }
  if (existing.status_code === 102) return { state: "IN_PROGRESS" };
  if (existing.status_code >= 400) {
    return { state: "FAILED", status_code: existing.status_code, response: existing.response };
  }
  return { state: "COMPLETED", status_code: existing.status_code, response: existing.response };
}

async function failIdempotency(
  sessionId: string,
  key: string,
  requestHash: string,
  leaseToken: string,
  statusCode: number,
  body: unknown,
) {
  await completeIdempotency(sessionId, key, requestHash, leaseToken, statusCode, body);
}

async function runIdempotentWrite(
  sessionId: string,
  body: Record<string, unknown>,
  operation: string,
  handler: () => Promise<ActionResult>,
): Promise<Response> {
  const key = String(body.idempotency_key || "").trim();
  const requestHash = String(body.request_hash || "").trim();

  if (!key || !requestHash) {
    return response({ error: "IDEMPOTENCY_REQUIRED" }, 400);
  }

  const claim = await claimIdempotency(sessionId, key, operation, requestHash);
  if (claim.state === "CONFLICT") return response({ error: "IDEMPOTENCY_KEY_REUSED" }, 409);
  if (claim.state === "COMPLETED" || claim.state === "FAILED") {
    return response(claim.response, claim.status_code);
  }

  if (claim.state === "IN_PROGRESS") {
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      const status = await statusIdempotency(sessionId, key, requestHash);
      if (status.state === "CONFLICT") return response({ error: "IDEMPOTENCY_KEY_REUSED" }, 409);
      if (status.state === "COMPLETED" || status.state === "FAILED") {
        return response(status.response, status.status_code);
      }
    }
    return response({ error: "IDEMPOTENCY_IN_PROGRESS" }, 409);
  }

  const leaseToken = String(claim.lease_token || "");
  if (!leaseToken) return response({ error: "IDEMPOTENCY_LEASE_MISSING" }, 500);

  try {
    const result = await handler();
    await completeIdempotency(sessionId, key, requestHash, leaseToken, result.status, result.body);
    return response(result.body, result.status);
  } catch (error) {
    const message = error instanceof Error ? error.message : "MEMORY_INTERNAL_ERROR";
    try {
      await failIdempotency(
        sessionId,
        key,
        requestHash,
        leaseToken,
        500,
        { error: "MEMORY_INTERNAL_ERROR", detail: message },
      );
    } catch (storeError) {
      console.error("idempotency-failure-record", storeError);
    }
    throw error;
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", {
      headers: {
        "access-control-allow-origin": "*",
        "access-control-allow-headers":
          "authorization, x-client-info, apikey, content-type, x-idempotency-key, x-deep33-internal-token",
        "access-control-allow-methods": "POST, OPTIONS",
      },
    });
  }

  try {
    const body = await req.json();
    const action = String(body.action || "");
    const sessionId = String(body.session_id || "").trim();

    if (action === "ping") {
      return response({ ok: true, service: "deep33-memory", version: 5 });
    }

    if (action === "hybrid_search" || action === "hybrid_index") {
      return await handleHybridAction(body, req);
    }

    if (action === "hybrid_search" || action === "hybrid_index") {
      return await handleHybridAction(body, req);
    }

    if (!sessionId || sessionId.length > 128) {
      return response({ error: "SESSION_ID_REQUIRED" }, 400);
    }

    if (action === "idempotency_claim") {
      return response(
        await claimIdempotency(
          sessionId,
          String(body.idempotency_key || ""),
          String(body.operation || "deep33.unknown"),
          String(body.request_hash || ""),
          Math.max(30, Math.min(300, Number(body.lease_seconds || 180))),
        ),
      );
    }

    if (action === "idempotency_status") {
      return response(
        await statusIdempotency(
          sessionId,
          String(body.idempotency_key || ""),
          String(body.request_hash || ""),
        ),
      );
    }

    if (action === "idempotency_complete" || action === "idempotency_fail") {
      const statusCode = Number(body.status_code || 200);
      await completeIdempotency(
        sessionId,
        String(body.idempotency_key || ""),
        String(body.request_hash || ""),
        String(body.lease_token || ""),
        statusCode,
        body.response ?? {},
      );
      return response({ ok: true });
    }

    if (action === "context") {
      const { data: session, error: sessionError } = await supabase
        .from("deep33_sessions")
        .select("session_id, personality, preferences, created_at, updated_at")
        .eq("session_id", sessionId)
        .maybeSingle();
      if (sessionError) throw sessionError;

      const { data: messages, error: messageError } = await supabase
        .from("deep33_messages")
        .select("role, content, model, provider, request_id, created_at")
        .eq("session_id", sessionId)
        .in("role", ["user", "assistant"])
        .order("created_at", { ascending: false })
        .order("id", { ascending: false })
        .limit(50);
      if (messageError) throw messageError;

      const { data: memories, error: memoryError } = await supabase
        .from("deep33_memories")
        .select("id, kind, content, created_at, updated_at")
        .eq("session_id", sessionId)
        .order("updated_at", { ascending: false })
        .order("id", { ascending: false })
        .limit(20);
      if (memoryError) throw memoryError;

      return response({
        session,
        messages: (messages || []).reverse(),
        memories: memories || [],
      });
    }

    if (action === "sync") {
      return await runIdempotentWrite(sessionId, body, "deep33.memory.sync", async () => {
        const sessionPatch: Record<string, unknown> = {
          session_id: sessionId,
          updated_at: new Date().toISOString(),
        };

        if (typeof body.personality === "string" && body.personality.trim()) {
          sessionPatch.personality = body.personality.slice(0, 32);
        }
        if (typeof body.preferences === "object" && body.preferences !== null) {
          sessionPatch.preferences = body.preferences;
        }

        const incoming = Array.isArray(body.messages) ? body.messages : [];
        const rows = [];
        for (const message of incoming.slice(-50)) {
          const role = String(message?.role || "");
          const content = String(message?.content || "");
          if (!["user", "assistant"].includes(role) || !content.trim()) continue;

          rows.push({
            session_id: sessionId,
            role,
            content,
            model: message?.model ? String(message.model).slice(0, 200) : null,
            provider: message?.provider ? String(message.provider).slice(0, 200) : null,
            request_id: message?.request_id ? String(message.request_id).slice(0, 100) : null,
            fingerprint: await sha256(role + "\n" + content),
          });
        }

        if (rows.length) {
          const { error } = await supabase
            .from("deep33_messages")
            .upsert(rows, {
              onConflict: "session_id,fingerprint",
              ignoreDuplicates: true,
            });
          if (error) throw error;
        }

        return { body: { ok: true, session_id: sessionId, saved: rows.length }, status: 200 };
      });
    }

    if (action === "remember") {
      return await runIdempotentWrite(sessionId, body, "deep33.memory.remember", async () => {
        const kind = String(body.kind || "explicit");
        const content = String(body.content || "").trim();

        if (!["preference", "explicit", "summary", "context"].includes(kind) || !content) {
          return { body: { error: "MEMORY_INVALID" }, status: 400 };
        }

        const fingerprint = await sha256(kind + "\n" + content);

        const { error: sessionError } = await supabase
          .from("deep33_sessions")
          .upsert(
            { session_id: sessionId, updated_at: new Date().toISOString() },
            { onConflict: "session_id" },
          );
        if (sessionError) throw sessionError;

        const { error } = await supabase
          .from("deep33_memories")
          .upsert(
            {
              session_id: sessionId,
              kind,
              content,
              fingerprint,
              updated_at: new Date().toISOString(),
            },
            { onConflict: "session_id,fingerprint" },
          );
        if (error) throw error;

        return { body: { ok: true, remembered: true }, status: 200 };
      });
    }

    if (action === "preferences") {
      return await runIdempotentWrite(sessionId, body, "deep33.memory.preferences", async () => {
        const patch: Record<string, unknown> = {
          session_id: sessionId,
          updated_at: new Date().toISOString(),
        };

        if (body.personality) patch.personality = String(body.personality).slice(0, 32);
        if (typeof body.preferences === "object" && body.preferences !== null) {
          patch.preferences = body.preferences;
        }

        const { error } = await supabase
          .from("deep33_sessions")
          .upsert(patch, { onConflict: "session_id" });
        if (error) throw error;

        return { body: { ok: true }, status: 200 };
      });
    }

    return response({ error: "UNKNOWN_ACTION" }, 400);
  } catch (error) {
    console.error(error);
    return response({ error: "MEMORY_INTERNAL_ERROR" }, 500);
  }
});