import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_KEY =
  Deno.env.get("SUPABASE_ANON_KEY") ??
  Deno.env.get("SUPABASE_PUBLISHABLE_KEY") ??
  "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

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

function validProfileId(value: string): boolean {
  return /^[A-Za-z0-9._-]{8,96}$/.test(value);
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
      const localProfileId = String(body.memory_profile_id ?? "").trim();
      if (!validProfileId(localProfileId)) {
        return json({ error: "MEMORY_PROFILE_ID_REQUIRED" }, 400);
      }

      if (!SUPABASE_SERVICE_ROLE_KEY) {
        return json({ error: "AUTH_MAPPING_NOT_CONFIGURED" }, 503);
      }

      const admin = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
        auth: { persistSession: false, autoRefreshToken: false },
      });

      const { data: existingMapping, error: lookupError } = await admin
        .schema("private").from("deep33_user_profiles")
        .select("user_id")
        .eq("memory_profile_id", localProfileId)
        .maybeSingle();

      if (lookupError) {
        return json({ error: "AUTH_PROFILE_LOOKUP_FAILED" }, 503);
      }
      if (existingMapping?.user_id) {
        return json({ error: "AUTH_PROFILE_ALREADY_BOUND" }, 409);
      }

      let session: { access_token: string; refresh_token: string; expires_in: number; expires_at?: number | null } | null = null;
      let userId = "";
      let createdFallbackUserId: string | null = null;

      const anonymous = await supabase.auth.signInAnonymously();
      if (!anonymous.error && anonymous.data.session && anonymous.data.user) {
        session = anonymous.data.session;
        userId = anonymous.data.user.id;
      } else {
        const syntheticEmail = "p_" + localProfileId + "@deep33.invalid";
        const syntheticPassword = crypto.randomUUID() + crypto.randomUUID();

        const { data: created, error: createError } =
          await admin.auth.admin.createUser({
            email: syntheticEmail,
            password: syntheticPassword,
            email_confirm: true,
            user_metadata: {
              deep33_profile_id: localProfileId,
              deep33_logical_anonymous: true,
            },
          });

        if (createError || !created.user) {
          return json({
            error: "AUTH_SESSION_CREATE_FAILED",
            detail: createError?.message ?? anonymous.error?.message ?? "No session returned",
          }, 503);
        }

        createdFallbackUserId = created.user.id;
        const signedIn = await supabase.auth.signInWithPassword({
          email: syntheticEmail,
          password: syntheticPassword,
        });

        if (signedIn.error || !signedIn.data.session || !signedIn.data.user) {
          await admin.auth.admin.deleteUser(created.user.id);
          return json({
            error: "AUTH_SESSION_SIGNIN_FAILED",
            detail: signedIn.error?.message ?? "No session returned",
          }, 503);
        }

        session = signedIn.data.session;
        userId = signedIn.data.user.id;
      }

      const { error: mapError } = await admin
        .from("deep33_user_profiles")
        .insert({
          user_id: userId,
          memory_profile_id: localProfileId,
        });

      if (mapError) {
        if (createdFallbackUserId) await admin.auth.admin.deleteUser(createdFallbackUserId);
        return json({ error: "AUTH_PROFILE_BINDING_FAILED" }, 503);
      }

      return json({
        ok: true,
        access_token: session.access_token,
        refresh_token: session.refresh_token,
        expires_in: session.expires_in,
        expires_at: session.expires_at,
        user_id: userId,
        memory_profile_id: localProfileId,
        is_anonymous: true,
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
