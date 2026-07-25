-- Make the backend-only token table's client denial explicit for security audits.
create policy "oauth_tokens_deny_clients"
  on public.oauth_tokens for all
  to anon, authenticated
  using (false)
  with check (false);

-- Cover foreign keys used for cascades and run/user lookups.
create index approvals_run_idx on public.approvals (run_id);
create index messages_run_idx on public.messages (run_id);
create index runs_user_idx on public.runs (user_id);
