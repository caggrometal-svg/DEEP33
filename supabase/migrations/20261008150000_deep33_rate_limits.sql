create table if not exists public.deep33_rate_limits (
  user_id uuid not null,
  bucket_start timestamptz not null,
  operation text not null,
  request_count integer not null default 0,
  primary key (user_id, bucket_start, operation)
);

create index if not exists deep33_rate_limits_bucket_idx
  on public.deep33_rate_limits (bucket_start);

alter table public.deep33_rate_limits enable row level security;

revoke all on table public.deep33_rate_limits from anon, authenticated, public;
grant all on table public.deep33_rate_limits to service_role;

create or replace function public.deep33_rate_limit_claim(
  p_user_id uuid,
  p_bucket_start timestamptz,
  p_operation text,
  p_max_count integer
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  if p_user_id is null
     or p_operation is null
     or length(trim(p_operation)) = 0
     or p_max_count < 1 then
    return false;
  end if;

  delete from public.deep33_rate_limits
  where bucket_start < now() - interval '10 minutes';

  insert into public.deep33_rate_limits (
    user_id, bucket_start, operation, request_count
  )
  values (
    p_user_id, p_bucket_start, left(trim(p_operation), 64), 1
  )
  on conflict (user_id, bucket_start, operation)
  do update set request_count = deep33_rate_limits.request_count + 1
  returning request_count into v_count;

  return v_count <= p_max_count;
end;
$$;

revoke all on function public.deep33_rate_limit_claim(uuid, timestamptz, text, integer) from public, anon, authenticated;
grant execute on function public.deep33_rate_limit_claim(uuid, timestamptz, text, integer) to service_role;
