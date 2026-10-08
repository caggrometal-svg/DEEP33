import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_KEY =
  Deno.env.get("SUPABASE_ANON_KEY") ??
  Deno.env.get("SUPABASE_PUBLISHABLE_KEY") ??
  "";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-request-id",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Cache-Control": "no-store",
  "Content-Type": "application/json; charset=utf-8",
};

const WINDOW_MS = 60_000;
const BURST_LIMIT = 5;
const ipBuckets = new Map<string, { start: number; count: number }>();

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders });
}

function clientIp(req: Request): string {
  return (
    req.headers.get("cf-connecting-ip") ??
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown"
  ).trim();
}

function allowRequest(req: Request): boolean {
  const ip = clientIp(req);
  const now = Date.now();
  const current = ipBuckets.get(ip);
  if (!current || now - current.start >= WINDOW_MS) {
    ipBuckets.set(ip, { start: now, count: 1 });
    if (ipBuckets.size > 5000) {
      for (const [key, bucket] of ipBuckets) {
        if (now - bucket.start >= WINDOW_MS) ipBuckets.delete(key);
      }
    }
    return true;
  }
  if (current.count >= BURST_LIMIT) return false;
  current.count += 1;
  return true;
}

function requireClientBody(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new Error("AUTH_BODY_INVALID");
  }
  return body as Record<string, unknown>;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "METHOD_NOT_ALLOWED" }, 405);
  if (!allowRequest(req)) return json({ error: "AUTH_RATE_LIMITED" }, 429);

  if (!SUPABASE_URL || !SUPABASE_KEY) {
    return json({ error: "AUTH_PROVIDER_NOT_CONFIGURED" }, 503);
  }

  try {
    const body = requireClientBody(await req.json());
    const action = String(body.action ?? "session").trim().toLowerCase();
    const supabase = createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: { persistSession: false, autoRefreshToken: false },
    });

    if (action === "session") {
      const { data, error } = await supabase.auth.signInAnonymously();
      if (error || !data.session || !data.user) {
        return json({
          error: "ANONYMOUS_AUTH_FAILED",
          detail: error?.message ?? "No session returned",
        }, 503);
      }
      return json({
        ok: true,
        access_token: data.session.access_token,
        refresh_token: data.session.refresh_token,
        expires_in: data.session.expires_in,
        expires_at: data.session.expires_at,
        user_id: data.user.id,
        is_anonymous: data.user.is_anonymous === true,
      });
    }

    if (action === "refresh") {
      const refreshToken = String(body.refresh_token ?? "").trim();
      if (!refreshToken || refreshToken.length > 4096) {
        return json({ error: "REFRESH_TOKEN_REQUIRED" }, 400);
      }
      const { data, error } = await supabase.auth.refreshSession({
        refresh_token: refreshToken,
      });
      if (error || !data.session || !data.user) {
        return json({ error: "AUTH_REFRESH_FAILED" }, 401);
      }
      return json({
        ok: true,
        access_token: data.session.access_token,
        refresh_token: data.session.refresh_token,
        expires_in: data.session.expires_in,
        expires_at: data.session.expires_at,
        user_id: data.user.id,
        is_anonymous: data.user.is_anonymous === true,
      });
    }

    return json({ error: "AUTH_ACTION_INVALID" }, 400);
  } catch {
    return json({ error: "AUTH_REQUEST_INVALID" }, 400);
  }
});
