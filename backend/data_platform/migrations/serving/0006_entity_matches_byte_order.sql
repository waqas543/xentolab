-- Match pairs are ordered by entity resolution in DuckDB, which compares strings byte by byte. The original check
-- used the database collation (en_US sorts '_' and '-' differently), so ids containing them, such as Google place
-- ids, were rejected even though DuckDB had ordered them. Compare bytes here too.
ALTER TABLE er.entity_matches DROP CONSTRAINT IF EXISTS entity_matches_check;
ALTER TABLE er.entity_matches ADD CONSTRAINT entity_matches_check CHECK (left_id < right_id COLLATE "C");
