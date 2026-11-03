-- Star rating (1-5) and the number of reviews behind it, from the sources that have them (Google Maps).
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS rating DOUBLE PRECISION;
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS review_count INTEGER;
