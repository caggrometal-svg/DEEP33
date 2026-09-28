create extension if not exists vector;
create extension if not exists pgcrypto;

create table if not exists public.deep33_knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id text not null,
  chunk_index integer not null,
  content text not null,
  embedding vector(1536),
  search_tsv tsvector generated always as (to_tsvector('simple', content)) stored,
  metadata jsonb not null default '{}'::jsonb,
  token_count integer not null default 0 check (token_count >= 0),
  checksum text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (document_id, chunk_index),
  unique (document_id, checksum)
);

create index if not exists deep33_knowledge_chunks_tsv_idx
  on public.deep33_knowledge_chunks using gin (search_tsv);

create index if not exists deep33_knowledge_chunks_metadata_idx
  on public.deep33_knowledge_chunks using gin (metadata);

create index if not exists deep33_knowledge_chunks_embedding_idx
  on public.deep33_knowledge_chunks using hnsw (embedding vector_cosine_ops);

create or replace function public.index_deep33_chunks(
  p_document_id text,
  p_chunks jsonb
) returns table(indexed_chunks integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  item jsonb;
  total integer := 0;
begin
  if length(trim(coalesce(p_document_id, ''))) = 0 then
    raise exception 'DOCUMENT_ID_REQUIRED';
  end if;

  delete from public.deep33_knowledge_chunks
  where document_id = p_document_id;

  for item in select value from jsonb_array_elements(coalesce(p_chunks, '[]'::jsonb))
  loop
    if length(trim(coalesce(item->>'content', ''))) = 0 then
      continue;
    end if;

    insert into public.deep33_knowledge_chunks (
      document_id,
      chunk_index,
      content,
      embedding,
      metadata,
      token_count,
      checksum,
      updated_at
    ) values (
      left(p_document_id, 200),
      greatest(0, coalesce((item->>'chunk_index')::integer, total)),
      item->>'content',
      case
        when nullif(trim(item->>'embedding'), '') is null then null
        else (item->>'embedding')::vector
      end,
      coalesce(item->'metadata', '{}'::jsonb),
      greatest(0, coalesce((item->>'token_count')::integer, 0)),
      coalesce(item->>'checksum', md5(item->>'content')),
      now()
    );

    total := total + 1;
  end loop;

  return query select total;
end;
$$;

create or replace function public.search_deep33_chunks(
  p_query text default '',
  p_embedding text default null,
  p_limit integer default 8,
  p_metadata_filter jsonb default '{}'::jsonb,
  p_rrf_k integer default 60
) returns table(
  document_id text,
  chunk_index integer,
  content text,
  metadata jsonb,
  keyword_rank integer,
  vector_rank integer,
  keyword_score real,
  vector_score real,
  rrf_score double precision
)
language sql
stable
security definer
set search_path = public
as $$
with params as (
  select
    greatest(1, least(coalesce(p_limit, 8), 20)) as result_limit,
    greatest(1, least(coalesce(p_rrf_k, 60), 200)) as rrf_k,
    nullif(trim(coalesce(p_query, '')), '') as query_text,
    nullif(trim(coalesce(p_embedding, '')), '') as embedding_text,
    coalesce(p_metadata_filter, '{}'::jsonb) as metadata_filter
),
keyword_ranked as (
  select
    c.id,
    row_number() over (
      order by ts_rank_cd(c.search_tsv, plainto_tsquery('simple', p.query_text)) desc,
               c.updated_at desc,
               c.id desc
    )::integer as keyword_rank,
    ts_rank_cd(c.search_tsv, plainto_tsquery('simple', p.query_text))::real as keyword_score
  from public.deep33_knowledge_chunks c
  cross join params p
  where p.query_text is not null
    and c.search_tsv @@ plainto_tsquery('simple', p.query_text)
    and (p.metadata_filter = '{}'::jsonb or c.metadata @> p.metadata_filter)
  order by keyword_score desc
  limit 40
),
vector_ranked as (
  select
    c.id,
    row_number() over (
      order by c.embedding <=> p.embedding_text::vector asc,
               c.updated_at desc,
               c.id desc
    )::integer as vector_rank,
    greatest(0.0, 1.0 - (c.embedding <=> p.embedding_text::vector))::real as vector_score
  from public.deep33_knowledge_chunks c
  cross join params p
  where p.embedding_text is not null
    and c.embedding is not null
    and (p.metadata_filter = '{}'::jsonb or c.metadata @> p.metadata_filter)
  order by c.embedding <=> p.embedding_text::vector asc
  limit 40
),
fused as (
  select
    coalesce(k.id, v.id) as id,
    k.keyword_rank,
    v.vector_rank,
    coalesce(k.keyword_score, 0)::real as keyword_score,
    coalesce(v.vector_score, 0)::real as vector_score,
    (
      case when k.keyword_rank is not null
        then 0.45 / (p.rrf_k + k.keyword_rank)
        else 0 end
      +
      case when v.vector_rank is not null
        then 0.55 / (p.rrf_k + v.vector_rank)
        else 0 end
    )::double precision as rrf_score
  from keyword_ranked k
  full outer join vector_ranked v on v.id = k.id
  cross join params p
)
select
  c.document_id,
  c.chunk_index,
  c.content,
  c.metadata,
  f.keyword_rank,
  f.vector_rank,
  f.keyword_score,
  f.vector_score,
  f.rrf_score
from fused f
join public.deep33_knowledge_chunks c on c.id = f.id
order by f.rrf_score desc, f.vector_score desc, f.keyword_score desc, c.updated_at desc
limit (select result_limit from params);
$$;

revoke all on function public.search_deep33_chunks(text, text, integer, jsonb, integer) from public;
revoke all on function public.index_deep33_chunks(text, jsonb) from public;
grant execute on function public.search_deep33_chunks(text, text, integer, jsonb, integer) to service_role;
grant execute on function public.index_deep33_chunks(text, jsonb) to service_role;

comment on table public.deep33_knowledge_chunks is
  'DEEP33 intelligent chunks with pgvector, PostgreSQL full-text search and advanced source metadata.';
