import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const STORAGE_VECTOR_DIMENSIONS = 1536;
const MODEL_VECTOR_DIMENSIONS = 384;
const embeddingSession = new Supabase.ai.Session("gte-small");

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

async function embedText(text: string): Promise<number[]> {
  const clean = String(text || "").trim();
  if (!clean) throw new Error("EMBEDDING_TEXT_REQUIRED");
  const raw = await embeddingSession.run(clean, { mean_pool: true, normalize: true });
  const compact = Array.from(raw as ArrayLike<number>, (value) => Number(value));
  if (compact.length !== MODEL_VECTOR_DIMENSIONS || !compact.every(Number.isFinite)) {
    throw new Error("EMBEDDING_MODEL_OUTPUT_INVALID");
  }
  // Zero-padding preserves cosine similarity and retains the existing vector(1536) contract.
  return compact.concat(Array(STORAGE_VECTOR_DIMENSIONS - MODEL_VECTOR_DIMENSIONS).fill(0));
}

function suppliedVector(value: unknown): number[] | null {
  if (value == null || value === "") return null;
  if (Array.isArray(value)) {
    vectorLiteral(value);
    return value.map(Number);
  }
  if (typeof value === "string" && value.trim().startsWith("[") && value.trim().endsWith("]")) {
    const parsed = value.trim().slice(1, -1).split(",").map((entry) => Number(entry.trim()));
    vectorLiteral(parsed);
    return parsed;
  }
  throw new Error("EMBEDDING_VALUES_INVALID");
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-headers":
        "authorization, x-client-info, apikey, content-type",
      "access-control-allow-methods": "POST, OPTIONS",
      "content-type": "application/json; charset=utf-8",
    },
  });
}

function vectorLiteral(values: unknown): string | null {
  if (values == null) return null;
  if (!Array.isArray(values) || values.length !== STORAGE_VECTOR_DIMENSIONS) {
    throw new Error(`EMBEDDING_DIMENSIONS_REQUIRED_${STORAGE_VECTOR_DIMENSIONS}`);
  }
  if (!values.every((value) => Number.isFinite(Number(value)))) {
    throw new Error("EMBEDDING_VALUES_INVALID");
  }
  return `[${values.map((value) => Number(value).toString()).join(",")}]`;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return json({ ok: true });
  if (req.method !== "POST") return json({ error: "METHOD_NOT_ALLOWED" }, 405);

  try {
    const body = await req.json();
    const action = String(body.action || "");

    if (action === "search") {
      const query = String(body.query || "").trim();
      const embedding = vectorLiteral(body.embedding);
      const limit = Math.max(1, Math.min(20, Number(body.limit || 8)));
      const metadataFilter =
        body.metadata_filter && typeof body.metadata_filter === "object"
          ? body.metadata_filter
          : {};

      if (!query && !embedding) {
        return json({ error: "HYBRID_QUERY_REQUIRED" }, 400);
      }

      const { data, error } = await supabase.rpc("search_deep33_chunks", {
        p_query: query,
        p_embedding: embedding,
        p_limit: limit,
        p_metadata_filter: metadataFilter,
        p_rrf_k: 60,
      });
      if (error) {
        console.error("search_deep33_chunks", error);
        return json({ error: "HYBRID_SEARCH_DATABASE_ERROR" }, 502);
      }
      return json(Array.isArray(data) ? data : []);
    }

    if (action === "index") {
      const documentId = String(body.document_id || "").trim();
      const chunks = Array.isArray(body.chunks) ? body.chunks : [];
      if (!documentId) return json({ error: "DOCUMENT_ID_REQUIRED" }, 400);
      if (!chunks.length) return json({ error: "DOCUMENT_CONTENT_REQUIRED" }, 400);

      const { data, error } = await supabase.rpc("index_deep33_chunks", {
        p_document_id: documentId.slice(0, 200),
        p_chunks: chunks,
      });
      if (error) {
        console.error("index_deep33_chunks", error);
        return json({ error: "HYBRID_INDEX_DATABASE_ERROR" }, 502);
      }
      return json(Array.isArray(data) ? data : data ?? { indexed_chunks: chunks.length });
    }

    if (action === "ping") {
      return json({ ok: true, service: "deep33-hybrid-search", version: 1 });
    }

    return json({ error: "ACTION_NOT_SUPPORTED" }, 400);
  } catch (error) {
    console.error(error);
    return json(
      { error: error instanceof Error ? error.message : "HYBRID_INTERNAL_ERROR" },
      500,
    );
  }
});
