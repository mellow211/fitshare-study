-- FitShare Vercel 저장소 (Supabase). Supabase SQL Editor에서 한 번 실행하면 됩니다. 여러 번 실행해도 안전합니다.
-- server.py는 server-data 폴더에 파일로 저장합니다. Vercel에서는 그 파일들을 아래 표와 Storage 버킷에 보관합니다.
--   fitshare_files  : 파일 목록과 버전. JSON 파일은 content에 글자로, 사진은 Storage 'fitshare' 버킷에 둡니다.
--   fitshare_locks  : 여러 Vercel 서버가 동시에 저장하지 않도록 차례를 정합니다.
--   fitshare_chunks : 4.5MB가 넘는 요청·응답을 잠시 나눠 담습니다.
-- 모든 표는 RLS를 켜고 정책을 두지 않아, 서버의 비밀 키로만 읽고 쓸 수 있습니다.

create table if not exists public.fitshare_files (
  path text primary key,
  version text not null,
  kind text not null check (kind in ('text', 'object')),
  size bigint not null,
  object_path text,
  content text,
  updated_at timestamptz not null default now()
);

create table if not exists public.fitshare_locks (
  name text primary key,
  token text not null,
  expires_at timestamptz not null
);

create table if not exists public.fitshare_chunks (
  key text primary key,
  value text not null,
  expires_at timestamptz not null
);

alter table public.fitshare_files enable row level security;
alter table public.fitshare_locks enable row level security;
alter table public.fitshare_chunks enable row level security;
revoke all on public.fitshare_files, public.fitshare_locks, public.fitshare_chunks from anon, authenticated;

-- 파일 목록(내용 제외). 버전이 바뀐 파일만 내려받는 데 씁니다.
-- API의 1000줄 제한에 걸리지 않도록 JSON 하나로 묶어 돌려줍니다.
create or replace function public.fitshare_manifest()
returns jsonb
language sql stable set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('path', path, 'version', version, 'kind', kind,
                                               'size', size, 'object_path', object_path)), '[]'::jsonb)
  from fitshare_files;
$$;

create or replace function public.fitshare_read(paths text[])
returns jsonb
language sql stable set search_path = public as $$
  select coalesce(jsonb_object_agg(path, content), '{}'::jsonb) from fitshare_files where path = any(paths);
$$;

-- 다른 서버가 먼저 바꾸지 않았을 때만 저장·삭제합니다(비교 후 저장). 결과는 작업마다 true/false.
create or replace function public.fitshare_apply(ops jsonb)
returns jsonb
language plpgsql set search_path = public as $$
declare
  op jsonb;
  changed integer;
  results jsonb := '[]'::jsonb;
begin
  for op in select value from jsonb_array_elements(ops) loop
    if op->>'action' = 'delete' then
      delete from fitshare_files where path = op->>'path' and version = op->>'expected';
    elsif coalesce(op->>'expected', '') = '' then
      insert into fitshare_files (path, version, kind, size, object_path, content)
      values (op->>'path', op->>'version', op->>'kind', (op->>'size')::bigint, op->>'object_path', op->>'content')
      on conflict (path) do nothing;
    else
      update fitshare_files
         set version = op->>'version', kind = op->>'kind', size = (op->>'size')::bigint,
             object_path = op->>'object_path', content = op->>'content', updated_at = now()
       where path = op->>'path' and version = op->>'expected';
    end if;
    get diagnostics changed = row_count;
    results := results || to_jsonb(changed = 1);
  end loop;
  return results;
end;
$$;

create or replace function public.fitshare_lock(p_name text, p_token text, p_seconds integer)
returns boolean
language plpgsql set search_path = public as $$
declare
  changed integer;
begin
  insert into fitshare_locks (name, token, expires_at)
  values (p_name, p_token, now() + make_interval(secs => p_seconds))
  on conflict (name) do update set token = excluded.token, expires_at = excluded.expires_at
  where fitshare_locks.expires_at < now();
  get diagnostics changed = row_count;
  return changed = 1;
end;
$$;

create or replace function public.fitshare_unlock(p_name text, p_token text)
returns boolean
language sql set search_path = public as $$
  with removed as (delete from fitshare_locks where name = p_name and token = p_token returning 1)
  select exists (select 1 from removed);
$$;

create or replace function public.fitshare_put_chunk(p_key text, p_value text, p_seconds integer)
returns boolean
language plpgsql set search_path = public as $$
begin
  delete from fitshare_chunks where expires_at < now();
  insert into fitshare_chunks (key, value, expires_at)
  values (p_key, p_value, now() + make_interval(secs => p_seconds))
  on conflict (key) do update set value = excluded.value, expires_at = excluded.expires_at;
  return true;
end;
$$;

create or replace function public.fitshare_take_chunks(keys text[])
returns jsonb
language sql set search_path = public as $$
  with taken as (
    delete from fitshare_chunks c where c.key = any(keys) and c.expires_at >= now() returning c.key, c.value
  )
  select coalesce(jsonb_object_agg(key, value), '{}'::jsonb) from taken;
$$;

revoke all on function public.fitshare_manifest(), public.fitshare_read(text[]), public.fitshare_apply(jsonb),
  public.fitshare_lock(text, text, integer), public.fitshare_unlock(text, text),
  public.fitshare_put_chunk(text, text, integer), public.fitshare_take_chunks(text[]) from public, anon, authenticated;
grant execute on function public.fitshare_manifest(), public.fitshare_read(text[]), public.fitshare_apply(jsonb),
  public.fitshare_lock(text, text, integer), public.fitshare_unlock(text, text),
  public.fitshare_put_chunk(text, text, integer), public.fitshare_take_chunks(text[]) to service_role;

-- 사진 보관용 비공개 버킷
insert into storage.buckets (id, name, public) values ('fitshare', 'fitshare', false)
on conflict (id) do nothing;
