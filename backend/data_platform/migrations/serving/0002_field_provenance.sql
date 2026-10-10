-- Field-level provenance (which source supplied each attribute) and the best provider confidence.
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS field_sources JSONB NOT NULL DEFAULT '{}';
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS source_confidence DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS businesses_field_sources_idx ON gold.businesses USING gin (field_sources);
