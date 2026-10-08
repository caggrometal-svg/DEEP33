alter table private.deep33_user_profiles enable row level security;

drop policy if exists deep33_user_profiles_service_role_only on private.deep33_user_profiles;
create policy deep33_user_profiles_service_role_only
on private.deep33_user_profiles
for all
to service_role
using (true)
with check (true);

revoke execute on function public.deep33_idempotency_claim(text, text, text, integer) from anon, authenticated;
revoke execute on function public.deep33_idempotency_complete(text, text, text, integer, jsonb) from anon, authenticated;
revoke execute on function public.deep33_idempotency_status(text, text) from anon, authenticated;


revoke execute on function public.deep33_idempotency_claim(text, text, text, integer) from public;
revoke execute on function public.deep33_idempotency_complete(text, text, text, integer, jsonb) from public;
revoke execute on function public.deep33_idempotency_status(text, text) from public;
grant execute on function public.deep33_idempotency_claim(text, text, text, integer) to service_role;
grant execute on function public.deep33_idempotency_complete(text, text, text, integer, jsonb) to service_role;
grant execute on function public.deep33_idempotency_status(text, text) to service_role;
