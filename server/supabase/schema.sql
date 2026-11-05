-- Member data for the Legislative Funding Atlas. Run once in the Supabase SQL editor.
--
-- The browser uses the public key, so row level security decides what it can touch:
-- people can read their own membership and usage, and manage their own saved work.
-- Only the server, using the secret key, can change memberships or usage.
-- Question text is never stored. advisor_runs holds counts and costs only.

create table public.memberships (
  user_id uuid primary key references auth.users (id) on delete cascade,
  status text not null default 'none',
  stripe_customer_id text unique,
  stripe_subscription_id text unique,
  price_id text,
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  updated_at timestamptz not null default now()
);

create table public.advisor_runs (
  id uuid primary key default gen_random_uuid(),
  -- Kept when an account is deleted, so the month's spending total stays correct.
  user_id uuid references auth.users (id) on delete set null,
  month text not null,
  status text not null default 'running' check (status in ('running', 'answered', 'stopped', 'refused', 'failed')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  reserve_micros bigint not null,
  cost_micros bigint not null default 0,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  cache_read_tokens bigint not null default 0,
  cache_write_tokens bigint not null default 0,
  tool_calls integer not null default 0,
  model text
);
create index advisor_runs_user_month on public.advisor_runs (user_id, month);
create index advisor_runs_month on public.advisor_runs (month);

create table public.saved_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  title text not null check (char_length(title) <= 200),
  data jsonb not null default '{}'::jsonb check (pg_column_size(data) <= 50000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index saved_items_user on public.saved_items (user_id, updated_at desc);

alter table public.memberships enable row level security;
alter table public.advisor_runs enable row level security;
alter table public.saved_items enable row level security;

-- Exact access for each role, whether or not Supabase exposes new tables automatically.
revoke all on public.memberships, public.advisor_runs, public.saved_items from anon, authenticated;
grant select on public.memberships, public.advisor_runs to authenticated;
grant select, insert, update, delete on public.saved_items to authenticated;
grant all on public.memberships, public.advisor_runs, public.saved_items to service_role;

create policy "read own membership" on public.memberships for select to authenticated using (user_id = auth.uid());
create policy "read own usage" on public.advisor_runs for select to authenticated using (user_id = auth.uid());
create policy "read own saved" on public.saved_items for select to authenticated using (user_id = auth.uid());
create policy "add own saved" on public.saved_items for insert to authenticated with check (user_id = auth.uid());
create policy "change own saved" on public.saved_items for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "delete own saved" on public.saved_items for delete to authenticated using (user_id = auth.uid());

-- Starts a question if the person is under their limit and the month's budget has room.
-- Money for the run is set aside first, so simultaneous questions can't overrun the budget.
-- A run that never finishes keeps its reserve, because it may really have spent that money.
create function public.advisor_begin(p_user uuid, p_month text, p_limit integer, p_cap_micros bigint, p_reserve_micros bigint, p_busy_seconds integer default 300)
returns table (result text, run_id uuid)
language plpgsql
security definer
set search_path = public
as $$
declare
  used integer;
  committed bigint;
  new_id uuid;
begin
  -- One at a time, so every check below sees the latest totals.
  perform pg_advisory_xact_lock(hashtext('advisor_budget:' || p_month));

  if exists (select 1 from advisor_runs where user_id = p_user and status = 'running'
             and started_at > now() - make_interval(secs => p_busy_seconds)) then
    return query select 'busy'::text, null::uuid;
    return;
  end if;

  select count(*) into used from advisor_runs where user_id = p_user and month = p_month and status <> 'failed';
  if used >= p_limit then
    return query select 'user_limit'::text, null::uuid;
    return;
  end if;

  select coalesce(sum(case when status = 'running' then reserve_micros else cost_micros end), 0) into committed
    from advisor_runs where month = p_month;
  if committed + p_reserve_micros > p_cap_micros then
    return query select 'budget_reached'::text, null::uuid;
    return;
  end if;

  insert into advisor_runs (user_id, month, reserve_micros) values (p_user, p_month, p_reserve_micros) returning id into new_id;
  return query select 'ok'::text, new_id;
end;
$$;

-- Records what a question really cost. Only a server failure gives the person their question back.
create function public.advisor_finish(p_run uuid, p_status text, p_cost_micros bigint, p_input bigint, p_output bigint, p_cache_read bigint, p_cache_write bigint, p_tool_calls integer, p_model text)
returns void
language sql
security definer
set search_path = public
as $$
  update advisor_runs set status = p_status, finished_at = now(), cost_micros = p_cost_micros, input_tokens = p_input,
    output_tokens = p_output, cache_read_tokens = p_cache_read, cache_write_tokens = p_cache_write, tool_calls = p_tool_calls, model = p_model
  where id = p_run and status = 'running';
$$;

-- Owner report: run in the SQL editor to see the real cost per question.
create view public.advisor_cost_by_month with (security_invoker = true) as
  select month, status, count(*) as questions, round(sum(cost_micros) / 1000000.0, 2) as dollars,
    round(avg(cost_micros) / 1000000.0, 4) as average_dollars,
    round((percentile_cont(0.9) within group (order by cost_micros) / 1000000.0)::numeric, 4) as p90_dollars
  from advisor_runs group by month, status order by month desc, status;

revoke all on public.advisor_cost_by_month from anon, authenticated;
grant select on public.advisor_cost_by_month to service_role;

revoke all on function public.advisor_begin(uuid, text, integer, bigint, bigint, integer) from public, anon, authenticated;
revoke all on function public.advisor_finish(uuid, text, bigint, bigint, bigint, bigint, bigint, integer, text) from public, anon, authenticated;
grant execute on function public.advisor_begin(uuid, text, integer, bigint, bigint, integer) to service_role;
grant execute on function public.advisor_finish(uuid, text, bigint, bigint, bigint, bigint, bigint, integer, text) to service_role;
