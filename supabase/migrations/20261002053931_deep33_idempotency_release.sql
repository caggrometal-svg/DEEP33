create or replace function public.deep33_idempotency_release(
  p_idempotency_key text,
  p_request_hash text,
  p_lease_token text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_deleted integer;
begin
  if coalesce(trim(p_idempotency_key), '') = ''
     or coalesce(trim(p_request_hash), '') = ''
     or coalesce(trim(p_lease_token), '') = '' then
    raise exception 'IDEMPOTENCY_ARGUMENTS_REQUIRED';
  end if;

  delete from public.ai_idempotency
   where idempotency_key = p_idempotency_key
     and request_hash = p_request_hash
     and status_code = 102
     and lock_token = p_lease_token;

  get diagnostics v_deleted = row_count;

  return jsonb_build_object('ok', true, 'released', v_deleted > 0);
end;
$$;

revoke all on function public.deep33_idempotency_release(text,text,text) from public, anon, authenticated;
grant execute on function public.deep33_idempotency_release(text,text,text) to service_role;
