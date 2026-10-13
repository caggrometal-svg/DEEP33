import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

declare const Supabase: {
  ai: {
    Session: new (model: string) => {
      run: (
        text: string,
        options?: { mean_pool?: boolean; normalize?: boolean },
      ) => Promise<ArrayLike<number>>;
    };
  };
};

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

async function embedMissingChunks(limit = 8): Promise<number> {
  const { data, error } = await supabase
    .from("deep33_knowledge_chunks")
    .select("id, content")
    .is("embedding", null)
    .order("updated_at", { ascending: false })
    .limit(Math.max(1, Math.min(8, limit)));
  if (error) {
    console.warn("hybrid_embedding_backfill_select_failed", error.code || "DB_ERROR");
    return 0;
  }
  let embedded = 0;
  for (const row of data || []) {
    try {
      const vector = await embedText(String(row.content || ""));
      const { error: updateError } = await supabase
        .from("deep33_knowledge_chunks")
        .update({ embedding: vector })
        .eq("id", row.id)
        .is("embedding", null);
      if (!updateError) embedded += 1;
      else console.warn("hybrid_embedding_backfill_update_failed", updateError.code || "DB_ERROR");
    } catch (error) {
      console.warn("hybrid_embedding_backfill_item_failed",
        error instanceof Error ? error.message : "EMBEDDING_FAILED");
    }
  }
  return embedded;
}

function json(
  body: unknown,
  status = 200,
  extraHeaders: Record<string, string> = {},
) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-headers":
        "authorization, x-client-info, apikey, content-type",
      "access-control-allow-methods": "POST, OPTIONS",
      "content-type": "application/json; charset=utf-8",
      ...extraHeaders,
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
      let embeddingValues = suppliedVector(body.embedding);
      if (!embeddingValues && query) embeddingValues = await embedText(query);
      const embedding = vectorLiteral(embeddingValues);
      const limit = Math.max(1, Math.min(20, Number(body.limit || 8)));
      const metadataFilter =
        body.metadata_filter && typeof body.metadata_filter === "object"
          ? body.metadata_filter
          : {};

      if (!query && !embedding) {
        return json({ error: "HYBRID_QUERY_REQUIRED" }, 400);
      }

      // Backfill a bounded batch of legacy chunks before vector search.
      if (embeddingValues && query) await embedMissingChunks(8);
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
      return json(Array.isArray(data) ? data : [], 200, {
        "x-deep33-search-mode": query && embeddingValues ? "hybrid" : "vector",
        "x-deep33-embedding-model": "Supabase/gte-small",
      });
    }

    if (action === "index") {
      const documentId = String(body.document_id || "").trim();
      const chunks = Array.isArray(body.chunks) ? body.chunks : [];
      if (!documentId) return json({ error: "DOCUMENT_ID_REQUIRED" }, 400);
      if (!chunks.length) return json({ error: "DOCUMENT_CONTENT_REQUIRED" }, 400);

      const embeddedChunks = await Promise.all(chunks.map(async (item: Record<string, unknown>) => {
        const content = String(item.content || "").trim();
        if (!content) return item;
        const supplied = suppliedVector(item.embedding);
        const embedding = supplied || await embedText(content);
        return { ...item, embedding };
      }));

      const { data, error } = await supabase.rpc("index_deep33_chunks", {
        p_document_id: documentId.slice(0, 200),
        p_chunks: embeddedChunks,
      });
      if (error) {
        console.error("index_deep33_chunks", error);
        return json({ error: "HYBRID_INDEX_DATABASE_ERROR" }, 502);
      }
      return json(Array.isArray(data) ? data : data ?? { indexed_chunks: chunks.length });
    }

    if (action === "ping") {
      return json({ ok: true, service: "deep33-hybrid-search", version: 2, embedding_model: "Supabase/gte-small", storage_dimensions: STORAGE_VECTOR_DIMENSIONS });
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
