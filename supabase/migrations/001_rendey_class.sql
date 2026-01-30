-- Rendey Class (Teacher SaaS) - minimal schema & policies
-- Run this in Supabase SQL editor if your tables are missing or inconsistent.
-- If you already created tables, compare and adapt.

-- PROFILES
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  role text default 'teacher',
  created_at timestamptz default now()
);

alter table public.profiles enable row level security;

create policy if not exists "profiles_select_own"
on public.profiles for select
using (auth.uid() = id);

create policy if not exists "profiles_upsert_own"
on public.profiles for insert
with check (auth.uid() = id);

create policy if not exists "profiles_update_own"
on public.profiles for update
using (auth.uid() = id);

-- ASSIGNMENTS
create table if not exists public.assignments (
  id uuid primary key default gen_random_uuid(),
  created_by uuid references auth.users(id) on delete set null,
  title text not null,
  type text default 'exam',
  content_json jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

alter table public.assignments enable row level security;

create policy if not exists "assignments_crud_own"
on public.assignments
for all
using (auth.uid() = created_by)
with check (auth.uid() = created_by);

-- OFFLINE EXAMS
create table if not exists public.offline_exams (
  id uuid primary key default gen_random_uuid(),
  access_code text unique not null,
  title text,
  assignment_id uuid references public.assignments(id) on delete cascade,
  status text default 'ready',
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz default now()
);

alter table public.offline_exams enable row level security;

-- Teacher can manage own
create policy if not exists "offline_exams_crud_own"
on public.offline_exams
for all
using (auth.uid() = created_by)
with check (auth.uid() = created_by);

-- Students can read by code (public access) so the exam can be opened without login
create policy if not exists "offline_exams_public_read_by_code"
on public.offline_exams
for select
using (status in ('ready','in_progress'));

-- EXAM ATTEMPTS
create table if not exists public.exam_attempts (
  id uuid primary key default gen_random_uuid(),
  offline_exam_code text not null,
  device_id text not null,
  student_name text not null,
  answers_json jsonb default '{}'::jsonb,
  submitted_at timestamptz default now()
);

alter table public.exam_attempts enable row level security;

-- Students can insert attempts if there is an active offline exam with that code
create policy if not exists "attempts_public_insert_if_exam_exists"
on public.exam_attempts
for insert
with check (
  exists (
    select 1 from public.offline_exams e
    where e.access_code = offline_exam_code
      and e.status in ('ready','in_progress')
  )
);

-- Teachers can read attempts for their own exams
create policy if not exists "attempts_teacher_read_own"
on public.exam_attempts
for select
using (
  exists (
    select 1 from public.offline_exams e
    where e.access_code = offline_exam_code
      and e.created_by = auth.uid()
  )
);
