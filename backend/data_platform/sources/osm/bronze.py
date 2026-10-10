"""OSM PBF -> Bronze Parquet (nodes, ways, relations), streamed in bounded batches.

Nothing is filtered: every object, tag, coordinate, way->node reference and relation
member is preserved, so Silver can always be rebuilt without the original download.
"""

from __future__ import annotations

from datetime import datetime, timezone

import osmium
import pyarrow as pa

from ...engine.context import StageContext
from ...lake.writer import DatasetVersionWriter

BATCH_ROWS = 200_000

TAGS = pa.map_(pa.string(), pa.string())
_COMMON = [
    pa.field("id", pa.int64(), nullable=False),
    pa.field("version", pa.int32()),
    pa.field("changeset", pa.int64()),
    pa.field("timestamp", pa.timestamp("s", tz="UTC")),
    pa.field("uid", pa.int64()),
    pa.field("user", pa.string()),
    pa.field("visible", pa.bool_()),
    pa.field("tags", TAGS),
]
_LINEAGE = [
    pa.field("source", pa.string()),
    pa.field("artifact_id", pa.int64()),
    pa.field("pipeline_run_id", pa.int64()),
    pa.field("ingested_at", pa.timestamp("s", tz="UTC")),
]
MEMBER = pa.struct([pa.field("type", pa.string()), pa.field("ref", pa.int64()), pa.field("role", pa.string())])

SCHEMAS = {
    "bronze.osm_nodes": pa.schema(_COMMON + [pa.field("latitude", pa.float64()), pa.field("longitude", pa.float64())]
                                  + _LINEAGE),
    "bronze.osm_ways": pa.schema(_COMMON + [pa.field("node_refs", pa.list_(pa.int64()))] + _LINEAGE),
    "bronze.osm_relations": pa.schema(_COMMON + [pa.field("members", pa.list_(MEMBER))] + _LINEAGE),
}


class _Buffer:
    def __init__(self, dataset_id: str, writer: DatasetVersionWriter, lineage: dict):
        self.schema = SCHEMAS[dataset_id]
        self.writer = writer
        self.lineage = lineage
        self.columns: dict[str, list] = {f.name: [] for f in self.schema if f.name not in lineage}
        self.size = 0
        self.total = 0

    def add(self, values: dict) -> None:
        for name, column in self.columns.items():
            column.append(values[name])
        self.size += 1
        if self.size >= BATCH_ROWS:
            self.flush()

    def flush(self) -> None:
        if not self.size:
            return
        arrays = []
        for field in self.schema:
            if field.name in self.lineage:
                arrays.append(pa.array([self.lineage[field.name]] * self.size, type=field.type))
            else:
                arrays.append(pa.array(self.columns[field.name], type=field.type))
        self.writer.write_batch(pa.RecordBatch.from_arrays(arrays, schema=self.schema))
        self.total += self.size
        for column in self.columns.values():
            column.clear()
        self.size = 0


class _Handler(osmium.SimpleHandler):
    def __init__(self, buffers: dict[str, _Buffer], ctx: StageContext):
        super().__init__()
        self.nodes = buffers["bronze.osm_nodes"]
        self.ways = buffers["bronze.osm_ways"]
        self.relations = buffers["bronze.osm_relations"]
        self.ctx = ctx
        self.count = 0

    def _common(self, obj) -> dict:
        timestamp = obj.timestamp
        return {
            "id": obj.id,
            "version": obj.version,
            "changeset": obj.changeset,
            "timestamp": timestamp if timestamp and timestamp.year > 1970 else None,
            "uid": obj.uid,
            "user": obj.user or None,
            "visible": obj.visible,
            "tags": [(t.k, t.v) for t in obj.tags] if len(obj.tags) else [],
        }

    def _tick(self):
        self.count += 1
        if self.count % 100_000 == 0:
            self.ctx.progress(self.count, None, "objects",
                              f"{self.nodes.total + self.nodes.size:,} nodes · {self.ways.total + self.ways.size:,} ways"
                              f" · {self.relations.total + self.relations.size:,} relations")

    def node(self, node):
        values = self._common(node)
        location = node.location
        if location.valid():
            values["latitude"], values["longitude"] = location.lat, location.lon
        else:
            values["latitude"] = values["longitude"] = None
        self.nodes.add(values)
        self._tick()

    def way(self, way):
        values = self._common(way)
        values["node_refs"] = [n.ref for n in way.nodes]
        self.ways.add(values)
        self._tick()

    def relation(self, relation):
        values = self._common(relation)
        values["members"] = [{"type": m.type, "ref": m.ref, "role": m.role} for m in relation.members]
        self.relations.add(values)
        self._tick()


def ingest(path: str, artifact: dict, ctx: StageContext, versions: dict) -> dict[str, dict]:
    """Parse the PBF into three new Bronze dataset versions. Returns {dataset_id: committed version}."""
    partition = {"artifact_id": artifact["id"]}
    inputs = [{"type": "artifact", "id": artifact["id"], "sha256": artifact["sha256"]}]
    lineage = {"source": "osm", "artifact_id": artifact["id"], "pipeline_run_id": ctx.run_id,
               "ingested_at": datetime.now(timezone.utc).replace(microsecond=0)}

    writers = {dataset_id: DatasetVersionWriter(ctx.storage, dataset_id, partition, schema, run_id=ctx.run_id,
                                                versions=versions, inputs=inputs)
               for dataset_id, schema in SCHEMAS.items()}
    entered = []
    try:
        for writer in writers.values():
            writer.__enter__()
            entered.append(writer)
        buffers = {dataset_id: _Buffer(dataset_id, writer, lineage) for dataset_id, writer in writers.items()}
        handler = _Handler(buffers, ctx)
        handler.apply_file(path, locations=False)
        for buffer in buffers.values():
            buffer.flush()
        ctx.progress(handler.count, handler.count, "objects", "parsing complete", force=True)
    except BaseException as error:
        for writer in entered:
            writer.__exit__(type(error), error, error.__traceback__)
        raise
    for writer in entered:
        writer.__exit__(None, None, None)
    return {dataset_id: writer.version for dataset_id, writer in writers.items()}
