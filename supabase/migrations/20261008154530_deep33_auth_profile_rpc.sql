-- DEEP33 auth profile mapping must remain private. Edge Functions use
-- tightly-granted public RPCs because PostgREST does not expose private schemas.

create or replace function public.deep33_auth_profile_lookup(p_memory_profile_id text)
returns uuid
language sql
set search_path = private, public
as $$
  select user_id
  from private.deep33_user_profiles
  where memory_profile_id = p_memory_profile_id
  limit 1;
$$;

create or replace function public.deep33_auth_profile_bind(
  p_user_id uuid,
  p_memory_profile_id text
)
returns boolean
language plpgsql
set search_path = private, public
as $$
begin
  insert into private.deep33_user_profiles(user_id, memory_profile_id)
  values (p_user_id, p_memory_profile_id);
  return true;
exception
  when unique_violation then
    return false;
end;
$$;

revoke all on function public.deep33_auth_profile_lookup(text) from public, anon, authenticated;
revoke all on function public.deep33_auth_profile_bind(uuid, text) from public, anon, authenticated;
grant execute on function public.deep33_auth_profile_lookup(text) to service_role;
grant execute on function public.deep33_auth_profile_bind(uuid, text) to service_role;
