-- DEEP33 idempotency RPC foundation.
-- Security-definer functions keep idempotency writes independent from client RLS
-- policy state while preserving one atomic claim per scoped request key.

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
  v_existing public.ai_idempotency%rowtype;
  v_lease_token text := gen_random_uuid()::text;
  v_expires timestamptz := now() + make_interval(secs => greatest(30, least(p_lease_seconds, 300)));
begin
  if coalesce(trim(p_idempotency_key), '') = '' or coalesce(trim(p_request_hash), '') = '' then
    raise exception 'IDEMPOTENCY_ARGUMENTS_REQUIRED';
  end if;

  delete from public.ai_idempotency
   where idempotency_key = p_idempotency_key
     and expires_at < now();

  insert into public.ai_idempotency(
    idempotency_key, operation, status_code, response, request_hash,
    expires_at, state, lock_token, updated_at
  ) values (
    p_idempotency_key, p_operation, 102,
    jsonb_build_object('lease_token', v_lease_token),
    p_request_hash, v_expires, 'IN_PROGRESS', v_lease_token, now()
  )
  on conflict (idempotency_key) do nothing;

  if found then
    return jsonb_build_object('state','CLAIMED','lease_token',v_lease_token);
  end if;

  select * into v_existing
    from public.ai_idempotency
   where idempotency_key = p_idempotency_key
   for update;

  if v_existing.idempotency_key is null then
    return jsonb_build_object('state','MISSING');
  end if;

  if v_existing.request_hash is distinct from p_request_hash then
    return jsonb_build_object('state','CONFLICT');
  end if;

  if v_existing.status_code = 102 or v_existing.state = 'IN_PROGRESS' then
    return jsonb_build_object('state','IN_PROGRESS');
  end if;

  if v_existing.status_code >= 400 or v_existing.state = 'FAILED' then
    return jsonb_build_object(
      'state','FAILED',
      'status_code',v_existing.status_code,
      'response',v_existing.response
    );
  end if;

  return jsonb_build_object(
    'state','COMPLETED',
    'status_code',v_existing.status_code,
    'response',v_existing.response
  );
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
  v_existing public.ai_idempotency%rowtype;
begin
  select * into v_existing
    from public.ai_idempotency
   where idempotency_key = p_idempotency_key;

  if v_existing.idempotency_key is null then
    return jsonb_build_object('state','MISSING');
  end if;
  if v_existing.request_hash is distinct from p_request_hash then
    return jsonb_build_object('state','CONFLICT');
  end if;
  if v_existing.status_code = 102 or v_existing.state = 'IN_PROGRESS' then
    return jsonb_build_object('state','IN_PROGRESS');
  end if;
  if v_existing.status_code >= 400 or v_existing.state = 'FAILED' then
    return jsonb_build_object(
      'state','FAILED',
      'status_code',v_existing.status_code,
      'response',v_existing.response
    );
  end if;
  return jsonb_build_object(
    'state','COMPLETED',
    'status_code',v_existing.status_code,
    'response',v_existing.response
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
  v_updated integer;
begin
  update public.ai_idempotency
     set status_code = p_status_code,
         response = coalesce(p_response, '{}'::jsonb),
         state = case when p_status_code >= 400 then 'FAILED' else 'COMPLETED' end,
         lock_token = null,
         updated_at = now(),
         expires_at = case
           when p_status_code >= 400 then now() + interval '5 minutes'
           else now() + interval '24 hours'
         end
   where idempotency_key = p_idempotency_key
     and request_hash = p_request_hash
     and status_code = 102
     and lock_token = p_lease_token;
  get diagnostics v_updated = row_count;
  if v_updated = 0 then
    raise exception 'IDEMPOTENCY_LEASE_MISMATCH_OR_MISSING';
  end if;
  return jsonb_build_object('ok',true);
end;
$$;

revoke all on function public.deep33_idempotency_claim(text,text,text,integer) from public, anon, authenticated;
revoke all on function public.deep33_idempotency_status(text,text) from public, anon, authenticated;
revoke all on function public.deep33_idempotency_complete(text,text,text,integer,jsonb) from public, anon, authenticated;
grant execute on function public.deep33_idempotency_claim(text,text,text,integer) to service_role;
grant execute on function public.deep33_idempotency_status(text,text) to service_role;
grant execute on function public.deep33_idempotency_complete(text,text,text,integer,jsonb) to service_role;
