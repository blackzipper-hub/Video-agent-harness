CREATE SCHEMA IF NOT EXISTS cuti_videochat_v2;

CREATE TABLE cuti_videochat_v2.agent_runs (
  id text PRIMARY KEY, user_id text NOT NULL, project_id text NOT NULL,
  thread_id text NOT NULL, idempotency_key text NOT NULL, status text NOT NULL,
  current_revision integer NOT NULL, updated_at timestamptz NOT NULL,
  payload jsonb NOT NULL, UNIQUE(user_id, idempotency_key)
);
CREATE INDEX agent_runs_status_idx ON cuti_videochat_v2.agent_runs(status);
CREATE INDEX agent_runs_thread_idx ON cuti_videochat_v2.agent_runs(user_id, thread_id, updated_at);

CREATE TABLE cuti_videochat_v2.plan_revisions (
  id text PRIMARY KEY, run_id text NOT NULL, number integer NOT NULL,
  payload jsonb NOT NULL, UNIQUE(run_id, number)
);
CREATE TABLE cuti_videochat_v2.tasks (
  id text PRIMARY KEY, run_id text NOT NULL, status text NOT NULL,
  remote_operation_id text, created_at timestamptz NOT NULL, payload jsonb NOT NULL
);
CREATE INDEX tasks_remote_operation_idx ON cuti_videochat_v2.tasks(remote_operation_id);
CREATE TABLE cuti_videochat_v2.task_attempts (
  id text PRIMARY KEY, task_id text NOT NULL, payload jsonb NOT NULL
);
CREATE TABLE cuti_videochat_v2.artifact_versions (
  id text PRIMARY KEY, artifact_id text NOT NULL, project_id text NOT NULL,
  artifact_type text NOT NULL, version integer NOT NULL,
  created_at timestamptz NOT NULL, payload jsonb NOT NULL
);
CREATE INDEX artifact_versions_project_idx
  ON cuti_videochat_v2.artifact_versions(project_id, created_at);
CREATE TABLE cuti_videochat_v2.artifact_edges (
  id text PRIMARY KEY, project_id text NOT NULL, payload jsonb NOT NULL
);
CREATE TABLE cuti_videochat_v2.artifact_selections (
  project_id text NOT NULL, artifact_type text NOT NULL,
  artifact_version_id text NOT NULL, payload jsonb NOT NULL,
  PRIMARY KEY(project_id, artifact_type)
);
CREATE TABLE cuti_videochat_v2.tool_invocations (
  id text PRIMARY KEY, run_id text NOT NULL, task_id text,
  idempotency_key text UNIQUE NOT NULL, status text NOT NULL, payload jsonb NOT NULL
);
CREATE TABLE cuti_videochat_v2.events (
  id text PRIMARY KEY, run_id text NOT NULL, sequence bigint NOT NULL,
  event_type text NOT NULL, payload jsonb NOT NULL, UNIQUE(run_id, sequence)
);
CREATE TABLE cuti_videochat_v2.source_events (
  fingerprint text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE cuti_videochat_v2.commands (
  id text PRIMARY KEY, run_id text NOT NULL, idempotency_key text NOT NULL,
  payload jsonb NOT NULL, UNIQUE(run_id, idempotency_key)
);
CREATE TABLE cuti_videochat_v2.chat_messages (
  id text PRIMARY KEY, run_id text NOT NULL, sequence bigint NOT NULL,
  created_at timestamptz NOT NULL, payload jsonb NOT NULL,
  UNIQUE(run_id, sequence)
);
CREATE INDEX chat_messages_run_idx
  ON cuti_videochat_v2.chat_messages(run_id, sequence);
