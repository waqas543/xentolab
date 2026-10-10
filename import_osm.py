import argparse
import csv
import json
import os
import tempfile
from pathlib import Path

import osmium
import psycopg


class OSMRawExporter(osmium.SimpleHandler):
    def __init__(self, output_dir: Path):
        super().__init__()

        self.files = {}
        self.writers = {}

        definitions = {
            "nodes": [
                "id", "latitude", "longitude", "version", "changeset",
                "uid", "user_name", "timestamp", "visible", "tags"
            ],
            "ways": [
                "id", "version", "changeset", "uid",
                "user_name", "timestamp", "visible", "tags"
            ],
            "way_nodes": [
                "way_id", "node_id", "sequence"
            ],
            "relations": [
                "id", "version", "changeset", "uid",
                "user_name", "timestamp", "visible", "tags"
            ],
            "relation_members": [
                "relation_id", "member_type", "member_id", "role", "sequence"
            ],
        }

        for name in definitions:
            file_obj = open(
                output_dir / f"{name}.csv",
                "w",
                newline="",
                encoding="utf-8",
            )

            self.files[name] = file_obj
            self.writers[name] = csv.writer(file_obj)

        self.node_count = 0
        self.way_count = 0
        self.relation_count = 0

    @staticmethod
    def tags_to_json(tags):
        return json.dumps(
            {tag.k: tag.v for tag in tags},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def timestamp(obj):
        if not obj.timestamp:
            return None

        return str(obj.timestamp)

    def node(self, node):
        self.writers["nodes"].writerow([
            node.id,
            node.location.lat,
            node.location.lon,
            node.version,
            node.changeset,
            node.uid,
            node.user,
            self.timestamp(node),
            node.visible,
            self.tags_to_json(node.tags),
        ])

        self.node_count += 1

        if self.node_count % 1_000_000 == 0:
            print(f"Nodes parsed: {self.node_count:,}")

    def way(self, way):
        self.writers["ways"].writerow([
            way.id,
            way.version,
            way.changeset,
            way.uid,
            way.user,
            self.timestamp(way),
            way.visible,
            self.tags_to_json(way.tags),
        ])

        for sequence, node in enumerate(way.nodes):
            self.writers["way_nodes"].writerow([
                way.id,
                node.ref,
                sequence,
            ])

        self.way_count += 1

        if self.way_count % 100_000 == 0:
            print(f"Ways parsed: {self.way_count:,}")

    def relation(self, relation):
        self.writers["relations"].writerow([
            relation.id,
            relation.version,
            relation.changeset,
            relation.uid,
            relation.user,
            self.timestamp(relation),
            relation.visible,
            self.tags_to_json(relation.tags),
        ])

        type_mapping = {
            "n": "n",
            "w": "w",
            "r": "r",
        }

        for sequence, member in enumerate(relation.members):
            self.writers["relation_members"].writerow([
                relation.id,
                type_mapping.get(member.type, member.type),
                member.ref,
                member.role,
                sequence,
            ])

        self.relation_count += 1

    def close(self):
        for file_obj in self.files.values():
            file_obj.close()


def copy_csv(connection, table, columns, filename):
    columns_sql = ", ".join(columns)

    sql = f"""
        COPY {table} ({columns_sql})
        FROM STDIN
        WITH (
            FORMAT CSV,
            NULL ''
        )
    """

    print(f"Loading {table}...")

    with connection.cursor() as cursor:
        with cursor.copy(sql) as copy:
            with open(filename, "r", encoding="utf-8") as file_obj:
                while data := file_obj.read(1024 * 1024):
                    copy.write(data)

    connection.commit()

    print(f"Loaded {table}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("pbf")
    parser.add_argument(
        "--dsn",
        default="dbname=ds_companies",
    )

    args = parser.parse_args()

    pbf = Path(args.pbf).expanduser().resolve()

    if not pbf.exists():
        raise FileNotFoundError(pbf)

    print(f"PBF: {pbf}")
    print(f"Size: {pbf.stat().st_size / 1024 / 1024:.2f} MB")

    with tempfile.TemporaryDirectory(prefix="osm_import_") as temp:
        output_dir = Path(temp)

        print(f"Temporary directory: {output_dir}")
        print("Parsing PBF...")

        handler = OSMRawExporter(output_dir)

        try:
            handler.apply_file(str(pbf))
        finally:
            handler.close()

        print()
        print("Parsing complete")
        print(f"Nodes:     {handler.node_count:,}")
        print(f"Ways:      {handler.way_count:,}")
        print(f"Relations: {handler.relation_count:,}")

        with psycopg.connect(args.dsn) as connection:

            copy_csv(
                connection,
                "osm_raw.nodes",
                [
                    "id", "latitude", "longitude", "version",
                    "changeset", "uid", "user_name",
                    "timestamp", "visible", "tags",
                ],
                output_dir / "nodes.csv",
            )

            copy_csv(
                connection,
                "osm_raw.ways",
                [
                    "id", "version", "changeset", "uid",
                    "user_name", "timestamp", "visible", "tags",
                ],
                output_dir / "ways.csv",
            )

            copy_csv(
                connection,
                "osm_raw.way_nodes",
                ["way_id", "node_id", "sequence"],
                output_dir / "way_nodes.csv",
            )

            copy_csv(
                connection,
                "osm_raw.relations",
                [
                    "id", "version", "changeset", "uid",
                    "user_name", "timestamp", "visible", "tags",
                ],
                output_dir / "relations.csv",
            )

            copy_csv(
                connection,
                "osm_raw.relation_members",
                [
                    "relation_id", "member_type",
                    "member_id", "role", "sequence",
                ],
                output_dir / "relation_members.csv",
            )


if __name__ == "__main__":
    main()