ALTER TABLE predictions ADD COLUMN IF NOT EXISTS preprocessing_seconds double precision;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS prediction_seconds double precision;
