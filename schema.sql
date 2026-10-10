CREATE SCHEMA IF NOT EXISTS osm_raw;

CREATE TABLE IF NOT EXISTS osm_raw.nodes (
    id          BIGINT PRIMARY KEY,
    latitude    DOUBLE PRECISION NOT NULL,
    longitude   DOUBLE PRECISION NOT NULL,
    version     INTEGER,
    changeset   BIGINT,
    uid         BIGINT,
    user_name   TEXT,
    timestamp   TIMESTAMPTZ,
    visible     BOOLEAN,
    tags        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS osm_raw.ways (
    id          BIGINT PRIMARY KEY,
    version     INTEGER,
    changeset   BIGINT,
    uid         BIGINT,
    user_name   TEXT,
    timestamp   TIMESTAMPTZ,
    visible     BOOLEAN,
    tags        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS osm_raw.way_nodes (
    way_id      BIGINT NOT NULL,
    node_id     BIGINT NOT NULL,
    sequence    INTEGER NOT NULL,
    PRIMARY KEY (way_id, sequence)
);

CREATE TABLE IF NOT EXISTS osm_raw.relations (
    id          BIGINT PRIMARY KEY,
    version     INTEGER,
    changeset   BIGINT,
    uid         BIGINT,
    user_name   TEXT,
    timestamp   TIMESTAMPTZ,
    visible     BOOLEAN,
    tags        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS osm_raw.relation_members (
    relation_id BIGINT NOT NULL,
    member_type CHAR(1) NOT NULL,
    member_id   BIGINT NOT NULL,
    role        TEXT,
    sequence    INTEGER NOT NULL,
    PRIMARY KEY (relation_id, sequence)
);