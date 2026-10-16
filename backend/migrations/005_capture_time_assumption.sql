ALTER TABLE photos ADD COLUMN IF NOT EXISTS capture_time_assumed boolean NOT NULL DEFAULT false;
