"""Direct contracts for the opt-in PostgreSQL candidate-index benchmark."""

from __future__ import annotations

import sys

import pytest

from scripts import benchmark_indexes


def _read_samples(*, buffers: list[int], sorts: list[bool]):
    return {
        "samples": [
            {"buffer_blocks": buffer_blocks, "sort_present": sort_present}
            for buffer_blocks, sort_present in zip(buffers, sorts, strict=True)
        ]
    }


def _write_samples(values: list[float]):
    return {"runs_ms": values}


def test_candidate_indexes_are_isolated_and_substring_has_no_extension() -> None:
    assert len(benchmark_indexes.CANDIDATES) == 4
    assert len({candidate.index_name for candidate in benchmark_indexes.CANDIDATES}) == 4
    assert all(
        candidate.index_name.startswith("benchmark_idx_")
        for candidate in benchmark_indexes.CANDIDATES
    )
    assert all(
        "CREATE INDEX benchmark_idx_" in candidate.create_sql
        for candidate in benchmark_indexes.CANDIDATES
    )
    assert all(
        "CREATE EXTENSION" not in candidate.create_sql for candidate in benchmark_indexes.CANDIDATES
    )
    assert "ILIKE '%needle%'" in benchmark_indexes.SUBSTRING_QUERY


def test_plan_evidence_recurses_through_buffers_and_sorts() -> None:
    document = [
        {
            "Plan": {
                "Node Type": "Limit",
                "Shared Hit Blocks": 2,
                "Plans": [
                    {
                        "Node Type": "Sort",
                        "Sort Key": ["created_at DESC"],
                        "Sort Method": "quicksort",
                        "Sort Space Used": 31,
                        "Sort Space Type": "Memory",
                        "Shared Read Blocks": 3,
                    }
                ],
            }
        }
    ]
    evidence = benchmark_indexes.plan_evidence(document)
    assert evidence["buffer_blocks"] == 2
    assert evidence["buffer_nodes"][1]["buffers"]["Shared Read Blocks"] == 3
    assert evidence["sort_present"] is True
    assert evidence["sort_nodes"][0]["sort_method"] == "quicksort"


@pytest.mark.parametrize(
    ("after_buffers", "after_sorts", "after_writes", "expected"),
    [
        ([40] * 5, [False] * 5, [10.5] * 5, True),
        ([100] * 5, [False] * 5, [10.5] * 5, True),
        ([40] * 5, [False] * 5, [11.5, 11.5, 11.5, 10.0, 10.0], False),
        ([100] * 5, [True] * 5, [10.0] * 5, False),
    ],
)
def test_recommendation_requires_read_evidence_and_write_guard(
    after_buffers: list[int],
    after_sorts: list[bool],
    after_writes: list[float],
    expected: bool,
) -> None:
    result = benchmark_indexes.recommendation(
        _read_samples(buffers=[100] * 5, sorts=[True] * 5),
        _read_samples(buffers=after_buffers, sorts=after_sorts),
        _write_samples([10.0] * 5),
        _write_samples(after_writes),
    )
    assert result["recommended"] is expected


@pytest.mark.asyncio
async def test_non_postgresql_url_is_refused_before_engine_creation() -> None:
    with pytest.raises(ValueError, match="explicit PostgreSQL"):
        await benchmark_indexes.benchmark("sqlite+aiosqlite:///:memory:")


def test_cli_requires_explicit_disposable_schema_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_indexes.py",
            "--disposable-database-url",
            "postgresql+asyncpg://localhost/disposable",
            "--output",
            str(tmp_path / "result.json"),
        ],
    )
    with pytest.raises(SystemExit) as raised:
        benchmark_indexes.main()
    assert raised.value.code == 2
