-- Safar production schema.
-- Every table in the exposed public schema has RLS enabled.

create extension if not exists pgcrypto;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  display_name text not null default 'Traveller',
  avatar_url text,
  google_sub text,
  home_city text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null default 'New trip',
  destination text,
  last_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  run_id uuid,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system', 'tool')),
  kind text not null default 'text',
  text text not null,
  payload jsonb not null default '{}'::jsonb,
  idempotency_key text,
  created_at timestamptz not null default now(),
  unique (user_id, idempotency_key)
);

create table public.runs (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null,
  state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.messages
  add constraint messages_run_id_fkey
  foreign key (run_id) references public.runs(id) on delete set null
  deferrable initially deferred;

create table public.approvals (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.runs(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  action text not null,
  payload_hash text not null,
  decision text check (decision in ('approve', 'edit', 'cancel')),
  expires_at timestamptz not null,
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);

create table public.user_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  home_city text,
  preferred_airport text,
  avoid_early_flights boolean not null default false,
  hotel_preference text,
  preferences jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

-- Backend-only. It is in public solely so PostgREST can be used from Render.
-- RLS is enabled and no client policy is created; the secret key bypasses RLS.
create table public.oauth_tokens (
  user_id uuid not null references auth.users(id) on delete cascade,
  provider text not null,
  encrypted_payload jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, provider)
);

create index conversations_user_updated_idx
  on public.conversations (user_id, updated_at desc);
create index messages_conversation_created_idx
  on public.messages (conversation_id, created_at);
create index runs_conversation_updated_idx
  on public.runs (conversation_id, updated_at desc);
create index approvals_user_run_idx
  on public.approvals (user_id, run_id);

alter table public.profiles enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.runs enable row level security;
alter table public.approvals enable row level security;
alter table public.user_preferences enable row level security;
alter table public.oauth_tokens enable row level security;

create policy "profiles_select_own"
  on public.profiles for select to authenticated
  using ((select auth.uid()) = id);
create policy "profiles_update_own"
  on public.profiles for update to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

create policy "conversations_select_own"
  on public.conversations for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "conversations_insert_own"
  on public.conversations for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "conversations_update_own"
  on public.conversations for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "conversations_delete_own"
  on public.conversations for delete to authenticated
  using ((select auth.uid()) = user_id);

create policy "messages_select_own"
  on public.messages for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "messages_insert_own"
  on public.messages for insert to authenticated
  with check (
    (select auth.uid()) = user_id
    and exists (
      select 1 from public.conversations c
      where c.id = conversation_id and c.user_id = (select auth.uid())
    )
  );

create policy "runs_select_own"
  on public.runs for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "approvals_select_own"
  on public.approvals for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "approvals_insert_own"
  on public.approvals for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "approvals_update_own"
  on public.approvals for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "preferences_select_own"
  on public.user_preferences for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "preferences_insert_own"
  on public.user_preferences for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "preferences_update_own"
  on public.user_preferences for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

grant usage on schema public to authenticated;
grant select, insert, update, delete on public.profiles to authenticated;
grant select, insert, update, delete on public.conversations to authenticated;
grant select, insert on public.messages to authenticated;
grant select on public.runs to authenticated;
grant select, insert, update on public.approvals to authenticated;
grant select, insert, update on public.user_preferences to authenticated;
revoke all on public.oauth_tokens from anon, authenticated;

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email, display_name, avatar_url, google_sub)
  values (
    new.id,
    coalesce(new.email, ''),
    coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name', 'Traveller'),
    coalesce(new.raw_user_meta_data ->> 'avatar_url', new.raw_user_meta_data ->> 'picture'),
    new.raw_user_meta_data ->> 'sub'
  )
  on conflict (id) do update set
    email = excluded.email,
    display_name = excluded.display_name,
    avatar_url = excluded.avatar_url,
    google_sub = excluded.google_sub,
    updated_at = now();
  return new;
end;
$$;

revoke execute on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
  after insert or update on auth.users
  for each row execute procedure public.handle_new_user();

alter publication supabase_realtime add table
  public.conversations,
  public.messages,
  public.runs,
  public.approvals;

