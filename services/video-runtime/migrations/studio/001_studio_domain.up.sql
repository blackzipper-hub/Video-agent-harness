CREATE SCHEMA IF NOT EXISTS cuti_studio;

CREATE TABLE IF NOT EXISTS cuti_studio.creative_bibles (
  project_id text PRIMARY KEY,
  payload jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cuti_studio.skill_locks (
  project_id text NOT NULL,
  skill_id text NOT NULL,
  version text NOT NULL,
  digest text NOT NULL,
  source text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  payload jsonb NOT NULL,
  PRIMARY KEY (project_id, skill_id)
);
