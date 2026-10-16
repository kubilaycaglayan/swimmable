CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS source_observations (
 id bigserial PRIMARY KEY, source text NOT NULL CHECK (source IN ('air','sea')),
 station text NOT NULL, measured_at timestamptz NOT NULL, retrieved_at timestamptz NOT NULL DEFAULT now(),
 temperature_c double precision NOT NULL, context jsonb NOT NULL DEFAULT '{}',
 UNIQUE(source, station, measured_at)
);
CREATE TABLE IF NOT EXISTS collection_attempts (
 id bigserial PRIMARY KEY, source text NOT NULL CHECK (source IN ('air','sea')),
 local_day date NOT NULL, slot text NOT NULL, attempted_at timestamptz NOT NULL DEFAULT now(),
 state text NOT NULL DEFAULT 'started', error_code text,
 UNIQUE(source, local_day, slot)
);
CREATE TABLE IF NOT EXISTS photos (
 id uuid PRIMARY KEY, sha256 text NOT NULL UNIQUE, private_path text NOT NULL,
 original_name text NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now(),
 captured_at timestamptz NOT NULL, offset_assumed boolean NOT NULL,
 width integer NOT NULL, height integer NOT NULL, format text NOT NULL
);
CREATE TABLE IF NOT EXISTS prediction_jobs (
 id uuid PRIMARY KEY, photo_id uuid NOT NULL UNIQUE REFERENCES photos(id),
 state text NOT NULL DEFAULT 'queued', queued_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz, finished_at timestamptz, error_code text
);
CREATE TABLE IF NOT EXISTS predictions (
 id bigserial PRIMARY KEY, photo_id uuid NOT NULL UNIQUE REFERENCES photos(id),
 waviness double precision NOT NULL CHECK (waviness >= 0 AND waviness <= 1),
 model_version text NOT NULL, preprocessing_version text NOT NULL,
 input_path text NOT NULL, completed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS score_revisions (
 id bigserial PRIMARY KEY, policy_version text NOT NULL, air_observation_id bigint NOT NULL REFERENCES source_observations(id),
 sea_observation_id bigint NOT NULL REFERENCES source_observations(id),
 prediction_id bigint NOT NULL REFERENCES predictions(id), score integer NOT NULL CHECK (score BETWEEN 0 AND 100),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(policy_version, air_observation_id, sea_observation_id, prediction_id)
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash text PRIMARY KEY, csrf_token text NOT NULL, expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS login_attempts (
 id bigserial PRIMARY KEY, ip_hash text NOT NULL, attempted_at timestamptz NOT NULL DEFAULT now(), success boolean NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_latest ON source_observations(source, measured_at DESC);
CREATE INDEX IF NOT EXISTS predictions_by_capture ON photos(captured_at DESC);
