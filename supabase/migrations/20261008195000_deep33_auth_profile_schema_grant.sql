-- Allow the service role to traverse the private schema for the
-- tightly-scoped DEEP33 auth profile RPCs.
grant usage on schema private to service_role;
