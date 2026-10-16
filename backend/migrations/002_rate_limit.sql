ALTER TABLE collection_attempts ADD COLUMN IF NOT EXISTS retry_after_at timestamptz;
