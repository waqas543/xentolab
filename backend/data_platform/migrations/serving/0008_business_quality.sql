-- Data quality (gold-v9): canonical categories, display names, exact positions, city ids and contact kinds.
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS category_label TEXT;        -- canonical category label
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS subcategory TEXT;           -- the source's own category
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS original_name TEXT;         -- the name as listed
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS name_local TEXT;            -- local-script spelling
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS generic_name BOOLEAN;       -- "Brick Kiln", "Medical Store"
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS city_id TEXT;               -- gazetteer id of `city`
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS location_precision TEXT;    -- exact | approximate
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS social_url TEXT;            -- a social / link page given as website
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS website_kind TEXT;          -- own | shared
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS phone_kind TEXT;            -- direct | shared | hotline
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS email_shared BOOLEAN;
ALTER TABLE gold.businesses ADD COLUMN IF NOT EXISTS opening_hours TEXT;         -- OSM opening_hours syntax
CREATE INDEX IF NOT EXISTS businesses_city_id_idx ON gold.businesses (country_code, city_id);
