import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.database import AsyncSessionLocal, engine
from app.models.alert import AlertMatchedTerm
from app.models.slur import SlurEntry
from app.models.system_settings import SystemSettings
from app.schemas.alert import normalize_term
from tests.conftest import finalized_alert_fields

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20261021_0011_seed_settings_and_default_dictionary.py"
)
_spec = importlib.util.spec_from_file_location("seed_migration_0011", MIGRATION_PATH)
seed_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_migration)

ALL_TERMS = seed_migration.LEGACY_TERMS + seed_migration.EDGE_TERMS


async def _empty_seed_tables() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE TABLE alert_matched_terms, slur_dictionary, system_settings CASCADE")
        )


async def _run(function) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(function)


async def _counts() -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        settings_rows = await session.scalar(select(func.count(SystemSettings.setting_id)))
        terms = await session.scalar(select(func.count(SlurEntry.term_id)))
    return settings_rows, terms


def test_seed_list_is_normalized_unique_and_valid():
    texts = [term for term, _, _ in ALL_TERMS]

    assert len(seed_migration.LEGACY_TERMS) == 15
    assert len(ALL_TERMS) == 549
    assert len(set(texts)) == len(texts)
    assert all(normalize_term(term) == term and 0 < len(term) <= 100 for term in texts)
    assert {language for _, language, _ in ALL_TERMS} <= {"fil", "ceb", "en"}
    assert all(0.0 <= weight <= 1.0 for _, _, weight in ALL_TERMS)


@pytest.mark.asyncio
async def test_seed_fills_an_empty_database_and_is_idempotent():
    await _empty_seed_tables()

    await _run(seed_migration.seed)
    first = await _counts()
    await _run(seed_migration.seed)
    second = await _counts()

    assert first == (1, len(ALL_TERMS))
    assert second == first
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(SystemSettings))
    assert (row.confidence_threshold, row.aggression_duration_threshold) == (0.55, 2.0)
    assert row.device_status == "offline"


@pytest.mark.asyncio
async def test_seed_preserves_existing_settings_and_terms():
    # The isolated fixture already inserted a customized settings row.
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                SlurEntry(slur_text="putangina", language="ceb", severity_weight=0.9),
                SlurEntry(slur_text=" Bobo ", language="fil", severity_weight=0.1),
                SlurEntry(slur_text="custom admin term", language="en", severity_weight=0.3),
            ]
        )
        await session.commit()

    await _run(seed_migration.seed)

    async with AsyncSessionLocal() as session:
        settings_rows = (await session.execute(select(SystemSettings))).scalars().all()
        entries = (await session.execute(select(SlurEntry))).scalars().all()
    by_normalized = {}
    for entry in entries:
        by_normalized.setdefault(normalize_term(entry.slur_text), []).append(entry)

    assert len(settings_rows) == 1
    assert settings_rows[0].stt_model_version == "local-test"
    assert all(len(matches) == 1 for matches in by_normalized.values())
    assert by_normalized["bobo"][0].severity_weight == 0.1
    assert by_normalized["custom admin term"][0].severity_weight == 0.3
    assert len(entries) == len(ALL_TERMS) + 1


@pytest.mark.asyncio
async def test_every_seeded_term_is_accepted_by_alert_ingest(client):
    await _empty_seed_tables()
    await _run(seed_migration.seed)

    for start in range(0, len(ALL_TERMS), 50):
        batch = ALL_TERMS[start : start + 50]
        payload = finalized_alert_fields()
        payload.update(
            {
                "severity": "medium",
                "confidence": 0.9,
                "duration": 1.0,
                "location": "Room 101",
                "transcribed_text": "Synthetic finalized transcript.",
                # Alternate the edge's two shapes: with a language, and with it omitted.
                "matched_terms": [
                    {"term": term, "language": language}
                    if index % 2
                    else {"term": term, "match_type": "word"}
                    for index, (term, language, _) in enumerate(batch)
                ],
            }
        )
        response = await client.post("/alerts/", json=payload)
        assert response.status_code == 200, (start, response.text)


@pytest.mark.asyncio
async def test_unseed_removes_only_untouched_unreferenced_seeded_terms(client):
    await _empty_seed_tables()
    await _run(seed_migration.seed)
    referenced, edited = seed_migration.EDGE_TERMS[0][0], seed_migration.EDGE_TERMS[1][0]
    async with AsyncSessionLocal() as session:
        entry = await session.scalar(select(SlurEntry).where(SlurEntry.slur_text == edited))
        entry.severity_weight = 0.01
        await session.commit()
    payload = finalized_alert_fields()
    payload.update(
        {
            "severity": "medium",
            "confidence": 0.9,
            "duration": 1.0,
            "location": "Room 101",
            "transcribed_text": "Synthetic finalized transcript.",
            "matched_terms": [{"term": referenced}],
        }
    )
    assert (await client.post("/alerts/", json=payload)).status_code == 200

    await _run(seed_migration.unseed)

    async with AsyncSessionLocal() as session:
        remaining = set((await session.execute(select(SlurEntry.slur_text))).scalars().all())
        links = await session.scalar(select(func.count(AlertMatchedTerm.id)))
    assert remaining == {term for term, _, _ in seed_migration.LEGACY_TERMS} | {
        referenced,
        edited,
    }
    assert links == 1
    assert (await _counts())[0] == 1
