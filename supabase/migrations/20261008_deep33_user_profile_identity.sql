create schema if not exists private;

create table if not exists private.deep33_user_profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    memory_profile_id text not null unique,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists deep33_user_profiles_memory_profile_idx
    on private.deep33_user_profiles(memory_profile_id);

alter table private.deep33_user_profiles enable row level security;

revoke all on private.deep33_user_profiles from anon, authenticated;
grant all on private.deep33_user_profiles to service_role;
