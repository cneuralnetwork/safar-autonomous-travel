-- Cover ownership and conversation foreign keys used by RLS and cleanup paths.
create index if not exists agent_events_user_id_idx
  on public.agent_events (user_id);

create index if not exists model_calls_conversation_id_idx
  on public.model_calls (conversation_id);

create index if not exists model_calls_user_id_idx
  on public.model_calls (user_id);
