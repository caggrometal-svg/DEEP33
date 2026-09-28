import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

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
  if (!Array.isArray(values) || values.length !== 1536) {
    throw new Error("EMBEDDING_DIMENSIONS_REQUIRED_1536");
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
