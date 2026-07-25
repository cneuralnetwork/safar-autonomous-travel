-- Durable, observable state for the Safar v2 agent harness.

alter table public.runs
  add column if not exists harness_version smallint not null default 1,
  add column if not exists phase text not null default 'interpreting',
  add column if not exists lease_owner text,
  add column if not exists lease_expires_at timestamptz;

create table public.run_tasks (
  run_id uuid not null references public.runs(id) on delete cascade,
  task_id text not null,
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (run_id, task_id)
);

create table public.agent_events (
  id bigint generated always as identity primary key,
  run_id uuid not null references public.runs(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  type text not null,
  phase text not null,
  status text not null,
  summary text not null,
  reason text,
  task_id text,
  provider text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.model_calls (
  id uuid primary key,
  run_id uuid not null references public.runs(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  phase text not null,
  model text not null,
  prompt_version text not null,
  status text not null check (status in ('completed', 'failed')),
  attempts integer not null default 1 check (attempts between 0 and 5),
  latency_ms integer not null check (latency_ms >= 0),
  prompt_tokens integer not null default 0 check (prompt_tokens >= 0),
  completion_tokens integer not null default 0 check (completion_tokens >= 0),
  total_tokens integer not null default 0 check (total_tokens >= 0),
  error_code text,
  error_message text,
  created_at timestamptz not null default now()
);

create index run_tasks_user_updated_idx
  on public.run_tasks (user_id, updated_at desc);
create index agent_events_run_id_idx
  on public.agent_events (run_id, id);
create index agent_events_conversation_id_idx
  on public.agent_events (conversation_id, id);
create index model_calls_run_created_idx
  on public.model_calls (run_id, created_at);
create index runs_agent_lease_idx
  on public.runs (status, lease_expires_at)
  where status in ('planned', 'running', 'replanning');

alter table public.run_tasks enable row level security;
alter table public.agent_events enable row level security;
alter table public.model_calls enable row level security;

create policy "run_tasks_select_own"
  on public.run_tasks for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "agent_events_select_own"
  on public.agent_events for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "model_calls_select_own"
  on public.model_calls for select to authenticated
  using ((select auth.uid()) = user_id);

grant select on public.run_tasks to authenticated;
grant select on public.agent_events to authenticated;
grant select on public.model_calls to authenticated;
grant all privileges on table
  public.run_tasks,
  public.agent_events,
  public.model_calls
to service_role;
grant usage, select on sequence public.agent_events_id_seq to service_role;

create or replace function public.claim_agent_run(
  p_run_id uuid,
  p_worker_id text,
  p_lease_seconds integer default 120
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  affected integer;
begin
  update public.runs
  set
    lease_owner = p_worker_id,
    lease_expires_at = now() + make_interval(secs => greatest(30, p_lease_seconds)),
    updated_at = now()
  where id = p_run_id
    and (
      lease_expires_at is null
      or lease_expires_at < now()
      or lease_owner = p_worker_id
    );
  get diagnostics affected = row_count;
  return affected > 0;
end;
$$;

create or replace function public.release_agent_run(
  p_run_id uuid,
  p_worker_id text
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  affected integer;
begin
  update public.runs
  set
    lease_owner = null,
    lease_expires_at = null,
    updated_at = now()
  where id = p_run_id and lease_owner = p_worker_id;
  get diagnostics affected = row_count;
  return affected > 0;
end;
$$;

revoke execute on function public.claim_agent_run(uuid, text, integer)
  from public, anon, authenticated;
revoke execute on function public.release_agent_run(uuid, text)
  from public, anon, authenticated;
grant execute on function public.claim_agent_run(uuid, text, integer)
  to service_role;
grant execute on function public.release_agent_run(uuid, text)
  to service_role;

alter publication supabase_realtime add table
  public.run_tasks,
  public.agent_events,
  public.model_calls;
