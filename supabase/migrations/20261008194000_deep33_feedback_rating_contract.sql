-- Normalize the feedback contract across clean installs and upgrades.
-- Older clients may send positive/negative; storage uses useful/not_useful.
update public.deep33_feedback
set rating = case
  when rating = 'positive' then 'useful'
  when rating = 'negative' then 'not_useful'
  else rating
end
where rating in ('positive', 'negative');

alter table public.deep33_feedback
  drop constraint if exists deep33_feedback_rating_check;

alter table public.deep33_feedback
  add constraint deep33_feedback_rating_check
  check (rating in ('useful', 'not_useful'));
