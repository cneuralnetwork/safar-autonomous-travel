alter table if exists public.conversations
  add column if not exists visual_theme text;

alter table if exists public.conversations
  drop constraint if exists conversations_visual_theme_check;

alter table if exists public.conversations
  add constraint conversations_visual_theme_check
  check (
    visual_theme is null
    or visual_theme in ('coast', 'mountains', 'heritage', 'nature', 'city')
  );

update public.conversations
set visual_theme = case
  when lower(destination) ~ '(alibaug|andaman|bali|chennai|goa|gokarna|kovalam|lakshadweep|mahabalipuram|maldives|mangalore|mumbai|pondicherry|puducherry|santorini|varkala|visakhapatnam)'
    then 'coast'
  when lower(destination) ~ '(auli|darjeeling|dharamshala|gangtok|gulmarg|kashmir|ladakh|leh|manali|mussoorie|nainital|shimla|srinagar|switzerland)'
    then 'mountains'
  when lower(destination) ~ '(agra|amritsar|hampi|jaipur|jaisalmer|jodhpur|khajuraho|kyoto|lucknow|mysore|udaipur|varanasi)'
    then 'heritage'
  when lower(destination) ~ '(alleppey|assam|cherrapunji|coorg|kerala|kochi|kumarakom|meghalaya|munnar|ooty|thekkady|wayanad)'
    then 'nature'
  else 'city'
end
where destination is not null
  and visual_theme is null;
