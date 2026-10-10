import pytest

from tests.fixtures.osm_fixture import write_fixture


@pytest.fixture(scope="session")
def synced(database):
    from data_platform.sources.registry import sync_to_control_plane

    sync_to_control_plane()
    return database


@pytest.fixture(scope="session")
def osm_files(tmp_path_factory):
    folder = tmp_path_factory.mktemp("osm")
    return {1: write_fixture(folder / "testland-v1.osm.pbf", 1), 2: write_fixture(folder / "testland-v2.osm.pbf", 2)}


def run_to_completion(run_id: int, limit: int = 50) -> dict:
    from data_platform import repository
    from data_platform.engine.worker import Worker

    worker = Worker()
    for _ in range(limit):
        run = repository.get_run(run_id)
        if run["status"] in ("COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_COMPLETED"):
            return run
        worker.run_once()
    raise AssertionError(f"run {run_id} did not finish")


def stage_map(run_id: int) -> dict:
    from data_platform import repository

    return {s["stage"]: s for s in repository.get_stages(run_id)}


def reset_platform():
    """Empty all runs, artifacts, catalog versions, serving tables and data files (definitions stay)."""
    import shutil

    from data_platform import db
    from data_platform.config import get_settings

    with db.control() as conn:
        conn.execute("""
            TRUNCATE dp.quality_results, dp.serving_state, dp.dataset_versions, dp.pipeline_checkpoints,
                     dp.pipeline_metrics, dp.pipeline_logs, dp.pipeline_errors, dp.pipeline_stage_runs,
                     dp.saved_views, dp.dataset_artifacts, dp.pipeline_runs, dp.pipeline_schedules CASCADE
        """)
    with db.serving() as conn:
        conn.execute("TRUNCATE er.entity_matches, gold.businesses, gold.business_sources, gold.organizations, "
                     "gold.organization_sources, gold.domains, gold.cities")
    data_dir = get_settings().data_dir
    for child in data_dir.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()


@pytest.fixture(scope="module")
def clean_platform(synced):
    reset_platform()
    return synced
