-- DEEP33 idempotency RPCs required by supabase/functions/deep33-memory.
create or replace function public.deep33_idempotency_claim(
  p_idempotency_key text,
  p_operation text,
  p_request_hash text,
  p_lease_seconds integer default 180
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  row_data public.ai_idempotency%rowtype;
  new_lock text;
  lease_seconds integer := greatest(30, least(coalesce(p_lease_seconds, 180), 300));
begin
  if nullif(trim(p_idempotency_key), '') is null
     or nullif(trim(p_request_hash), '') is null then
    raise exception 'IDEMPOTENCY_ARGUMENTS_REQUIRED';
  end if;
  new_lock := md5(random()::text || clock_timestamp()::text || p_idempotency_key);
  insert into public.ai_idempotency(
    idempotency_key, operation, status_code, response,
    created_at, expires_at, request_hash, state, lock_token, updated_at
  )
  values(
    p_idempotency_key, left(coalesce(p_operation, 'deep33.unknown'), 200), 0, '{}'::jsonb,
    now(), now() + make_interval(secs => lease_seconds), p_request_hash, 'IN_PROGRESS', new_lock, now()
  )
  on conflict (idempotency_key) do nothing
  returning * into row_data;
  if found then
    return jsonb_build_object('state','CLAIMED','lease_token',new_lock);
  end if;
  select * into row_data from public.ai_idempotency
  where idempotency_key = p_idempotency_key for update;
  if row_data.request_hash is distinct from p_request_hash then
    return jsonb_build_object('state','CONFLICT');
  end if;
  if row_data.state in ('COMPLETED','FAILED') then
    return jsonb_build_object(
      'state', row_data.state,
      'status_code', row_data.status_code,
      'response', coalesce(row_data.response, '{}'::jsonb)
    );
  end if;
  if row_data.state = 'IN_PROGRESS'
     and row_data.updated_at > now() - make_interval(secs => lease_seconds) then
    return jsonb_build_object('state','IN_PROGRESS');
  end if;
  update public.ai_idempotency
     set operation = left(coalesce(p_operation, operation), 200),
         status_code = 0,
         response = '{}'::jsonb,
         expires_at = now() + make_interval(secs => lease_seconds),
         request_hash = p_request_hash,
         state = 'IN_PROGRESS',
         lock_token = new_lock,
         updated_at = now()
   where idempotency_key = p_idempotency_key;
  return jsonb_build_object('state','CLAIMED','lease_token',new_lock);
end;
$$;

create or replace function public.deep33_idempotency_status(
  p_idempotency_key text,
  p_request_hash text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  row_data public.ai_idempotency%rowtype;
begin
  select * into row_data from public.ai_idempotency
  where idempotency_key = p_idempotency_key;
  if not found then return jsonb_build_object('state','MISSING'); end if;
  if row_data.request_hash is distinct from p_request_hash then
    return jsonb_build_object('state','CONFLICT');
  end if;
  return jsonb_build_object(
    'state', row_data.state,
    'status_code', row_data.status_code,
    'response', coalesce(row_data.response, '{}'::jsonb),
    'lease_token', row_data.lock_token
  );
end;
$$;

create or replace function public.deep33_idempotency_complete(
  p_idempotency_key text,
  p_request_hash text,
  p_lease_token text,
  p_status_code integer,
  p_response jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_count integer;
  final_state text;
begin
  final_state := case when p_status_code between 200 and 299 then 'COMPLETED' else 'FAILED' end;
  update public.ai_idempotency
     set status_code = p_status_code,
         response = coalesce(p_response, '{}'::jsonb),
         state = final_state,
         lock_token = null,
         updated_at = now()
   where idempotency_key = p_idempotency_key
     and request_hash = p_request_hash
     and lock_token = p_lease_token
     and state = 'IN_PROGRESS';
  get diagnostics updated_count = row_count;
  if updated_count = 0 then return jsonb_build_object('ok',false,'state','NOT_UPDATED'); end if;
  return jsonb_build_object('ok',true,'state',final_state);
end;
$$;

grant execute on function public.deep33_idempotency_claim(text,text,text,integer) to public;
grant execute on function public.deep33_idempotency_status(text,text) to public;
grant execute on function public.deep33_idempotency_complete(text,text,text,integer,jsonb) to public;
