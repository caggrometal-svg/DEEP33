import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-headers":
        "authorization, x-client-info, apikey, content-type, x-idempotency-key",
      "access-control-allow-methods": "POST, OPTIONS",
      "content-type": "application/json; charset=utf-8",
    },
  });
}

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function scopedKey(sessionId: string, key: string): Promise<string> {
  return sha256(`deep33|${sessionId}|${key}`);
}

async function readIdempotency(key: string) {
  const { data, error } = await supabase
    .from("ai_idempotency")
    .select("idempotency_key, operation, status_code, response, request_hash, expires_at")
    .eq("idempotency_key", key)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function claim(sessionId: string, key: string, operation: string, requestHash: string, leaseSeconds = 180) {
  if (!key || !requestHash) throw new Error("IDEMPOTENCY_ARGUMENTS_REQUIRED");
  const skey = await scopedKey(sessionId, key);
  const now = new Date();
  await supabase.from("ai_idempotency").delete().eq("idempotency_key", skey).lt("expires_at", now.toISOString());

  const leaseToken = crypto.randomUUID();
  const { error } = await supabase.from("ai_idempotency").insert({
    idempotency_key: skey,
    operation,
    status_code: 102,
    response: { lease_token: leaseToken },
    request_hash: requestHash,
    expires_at: new Date(now.getTime() + leaseSeconds * 1000).toISOString(),
  });
  if (!error) return { state: "CLAIMED", lease_token: leaseToken };

  if (String(error.code || "") !== "23505") throw error;
  const existing = await readIdempotency(skey);
  if (!existing) return { state: "CLAIMED", lease_token: leaseToken };
  if (existing.request_hash && existing.request_hash !== requestHash) return { state: "CONFLICT" };
  if (existing.status_code === 102) return { state: "IN_PROGRESS" };
  if (existing.status_code >= 400) return { state: "FAILED", status_code: existing.status_code, response: existing.response };
  return { state: "COMPLETED", status_code: existing.status_code, response: existing.response };
}

async function complete(sessionId: string, key: string, requestHash: string, leaseToken: string, statusCode: number, result: unknown) {
  const skey = await scopedKey(sessionId, key);
  const existing = await readIdempotency(skey);
  if (!existing) throw new Error("IDEMPOTENCY_MISSING");
  if (existing.request_hash && existing.request_hash !== requestHash) throw new Error("IDEMPOTENCY_KEY_REUSED");
  if (existing.status_code !== 102 || existing.response?.lease_token !== leaseToken) {
    throw new Error("IDEMPOTENCY_LEASE_MISMATCH");
  }
  const { error } = await supabase.from("ai_idempotency").update({
    status_code: statusCode,
    response: result,
    expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
  }).eq("idempotency_key", skey).eq("status_code", 102);
  if (error) throw error;
}

async function status(sessionId: string, key: string, requestHash: string) {
  const row = await readIdempotency(await scopedKey(sessionId, key));
  if (!row) return { state: "MISSING" };
  if (row.request_hash && row.request_hash !== requestHash) return { state: "CONFLICT" };
  if (row.status_code === 102) return { state: "IN_PROGRESS" };
  if (row.status_code >= 400) return { state: "FAILED", status_code: row.status_code, response: row.response };
  return { state: "COMPLETED", status_code: row.status_code, response: row.response };
}

async function idempotentWrite(
  sessionId: string,
  body: Record<string, unknown>,
  operation: string,
  handler: () => Promise<{ body: unknown; status: number }>,
): Promise<Response> {
  const key = String(body.idempotency_key || "").trim();
  const requestHash = String(body.request_hash || "").trim();
  if (!key || !requestHash) return response({ error: "IDEMPOTENCY_REQUIRED" }, 400);

  const c = await claim(sessionId, key, operation, requestHash);
  if (c.state === "CONFLICT") return response({ error: "IDEMPOTENCY_KEY_REUSED" }, 409);
  if (c.state === "COMPLETED" || c.state === "FAILED") return response(c.response, c.status_code);

  if (c.state === "IN_PROGRESS") {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      const s = await status(sessionId, key, requestHash);
      if (s.state === "CONFLICT") return response({ error: "IDEMPOTENCY_KEY_REUSED" }, 409);
      if (s.state === "COMPLETED" || s.state === "FAILED") return response(s.response, s.status_code);
    }
    return response({ error: "IDEMPOTENCY_IN_PROGRESS" }, 409);
  }

  const leaseToken = String(c.lease_token || "");
  try {
    const result = await handler();
    await complete(sessionId, key, requestHash, leaseToken, result.status, result.body);
    return response(result.body, result.status);
  } catch (error) {
    try {
      await complete(
        sessionId,
        key,
        requestHash,
        leaseToken,
        500,
        { error: "MEMORY_INTERNAL_ERROR" },
      );
    } catch (recordError) {
      console.error("idempotency failure record", recordError);
    }
    console.error(error);
    return response({ error: "MEMORY_INTERNAL_ERROR" }, 500);
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", {
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-headers":
        "authorization, x-client-info, apikey, content-type, x-idempotency-key",
      "access-control-allow-methods": "POST, OPTIONS",
    },
  });

  try {
    const body = await req.json();
    const action = String(body.action || "");
    const sessionId = String(body.session_id || "").trim();

    if (action === "ping") return response({ ok: true, service: "deep33-memory", version: 3 });
    if (!sessionId || sessionId.length > 128) return response({ error: "SESSION_ID_REQUIRED" }, 400);

    if (action === "idempotency_claim") {
      return response(await claim(
        sessionId,
        String(body.idempotency_key || ""),
        String(body.operation || "deep33.unknown"),
        String(body.request_hash || ""),
        Math.max(30, Math.min(300, Number(body.lease_seconds || 180))),
      ));
    }
    if (action === "idempotency_status") {
      return response(await status(
        sessionId,
        String(body.idempotency_key || ""),
        String(body.request_hash || ""),
      ));
    }
    if (action === "idempotency_complete" || action === "idempotency_fail") {
      await complete(
        sessionId,
        String(body.idempotency_key || ""),
        String(body.request_hash || ""),
        String(body.lease_token || ""),
        Number(body.status_code || 200),
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
      const { data: messages, error: messagesError } = await supabase
        .from("deep33_messages")
        .select("role, content, model, provider, request_id, created_at")
        .eq("session_id", sessionId)
        .in("role", ["user", "assistant"])
        .order("created_at", { ascending: false })
        .order("id", { ascending: false })
        .limit(50);
      if (messagesError) throw messagesError;
      const { data: memories, error: memoryError } = await supabase
        .from("deep33_memories")
        .select("id, kind, content, created_at, updated_at")
        .eq("session_id", sessionId)
        .order("updated_at", { ascending: false })
        .order("id", { ascending: false })
        .limit(20);
      if (memoryError) throw memoryError;
      return response({ session, messages: (messages || []).reverse(), memories: memories || [] });
    }

    if (action === "sync") {
      return await idempotentWrite(sessionId, body, "deep33.memory.sync", async () => {
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
        const { error: sessionError } = await supabase.from("deep33_sessions").upsert(sessionPatch, { onConflict: "session_id" });
        if (sessionError) throw sessionError;

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
          const { error } = await supabase.from("deep33_messages").upsert(rows, {
            onConflict: "session_id,fingerprint",
            ignoreDuplicates: true,
          });
          if (error) throw error;
        }
        return { body: { ok: true, session_id: sessionId, saved: rows.length }, status: 200 };
      });
    }

    if (action === "remember") {
      return await idempotentWrite(sessionId, body, "deep33.memory.remember", async () => {
        const kind = String(body.kind || "explicit");
        const content = String(body.content || "").trim();
        if (!["preference", "explicit", "summary", "context"].includes(kind) || !content) {
          return { body: { error: "MEMORY_INVALID" }, status: 400 };
        }
        const fingerprint = await sha256(kind + "\n" + content);
        const { error: sessionError } = await supabase.from("deep33_sessions").upsert(
          { session_id: sessionId, updated_at: new Date().toISOString() },
          { onConflict: "session_id" },
        );
        if (sessionError) throw sessionError;
        const { error } = await supabase.from("deep33_memories").upsert(
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
      return await idempotentWrite(sessionId, body, "deep33.memory.preferences", async () => {
        const patch: Record<string, unknown> = {
          session_id: sessionId,
          updated_at: new Date().toISOString(),
        };
        if (body.personality) patch.personality = String(body.personality).slice(0, 32);
        if (typeof body.preferences === "object" && body.preferences !== null) {
          patch.preferences = body.preferences;
        }
        const { error } = await supabase.from("deep33_sessions").upsert(patch, { onConflict: "session_id" });
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