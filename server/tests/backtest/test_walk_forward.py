from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.backtest.walk_forward import (
    TimedSample,
    WalkForwardConfig,
    purged_walk_forward,
)


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def sample(
    day: int,
    *,
    horizon_days: int = 2,
    segment: str = "BTC-PERP",
    segment_valid_until: datetime | None = None,
) -> TimedSample:
    observed = BASE + timedelta(days=day)
    return TimedSample(
        sample_id=f"s{day}",
        observed_at=observed,
        label_end_at=observed + timedelta(days=horizon_days),
        market_segment=segment,
        segment_valid_until=segment_valid_until,
    )


def config() -> WalkForwardConfig:
    return WalkForwardConfig(
        train_span=timedelta(days=10),
        validation_span=timedelta(days=4),
        test_span=timedelta(days=4),
        embargo=timedelta(days=1),
        step=timedelta(days=4),
    )


def test_splitter_is_strictly_time_ordered_and_never_shuffles():
    samples = tuple(sample(day) for day in range(40))

    folds = purged_walk_forward(samples, config())

    assert len(folds) >= 2
    for fold in folds:
        assert tuple(item.observed_at for item in fold.train) == tuple(
            sorted(item.observed_at for item in fold.train)
        )
        assert tuple(item.observed_at for item in fold.validation) == tuple(
            sorted(item.observed_at for item in fold.validation)
        )
        assert tuple(item.observed_at for item in fold.test) == tuple(
            sorted(item.observed_at for item in fold.test)
        )
        assert max(item.observed_at for item in fold.train) < min(
            item.observed_at for item in fold.validation
        )
        assert max(item.observed_at for item in fold.validation) < min(
            item.observed_at for item in fold.test
        )


def test_purge_removes_train_labels_overlapping_validation_boundary():
    samples = tuple(sample(day, horizon_days=3) for day in range(30))
    first = purged_walk_forward(samples, config())[0]

    assert first.train
    assert all(item.label_end_at < first.validation_start for item in first.train)
    assert any(item.sample_id in first.purged_sample_ids for item in samples)


def test_validation_is_purged_before_test_and_embargo_gap_is_empty():
    samples = tuple(sample(day, horizon_days=2) for day in range(30))
    first = purged_walk_forward(samples, config())[0]
    used = (*first.train, *first.validation, *first.test)

    assert all(item.label_end_at < first.test_start for item in first.validation)
    assert first.validation_start - first.train_end == timedelta(days=1)
    assert first.test_start - first.validation_end == timedelta(days=1)
    assert not any(
        first.train_end <= item.observed_at < first.validation_start for item in used
    )
    assert not any(
        first.validation_end <= item.observed_at < first.test_start for item in used
    )


def test_market_closures_do_not_create_synthetic_samples_or_reorder_data():
    # Deliberate calendar gaps represent weekends/closures. Splitter works on
    # actual available observations and never backfills missing days.
    samples = tuple(
        sample(day)
        for day in (0, 1, 2, 5, 6, 9, 10, 13, 14, 17, 18, 21, 22, 25, 26, 29)
    )

    folds = purged_walk_forward(
        samples,
        WalkForwardConfig(
            train_span=timedelta(days=9),
            validation_span=timedelta(days=4),
            test_span=timedelta(days=4),
            embargo=timedelta(days=0),
            step=timedelta(days=4),
        ),
    )

    observed_ids = {item.sample_id for item in samples}
    for fold in folds:
        assert {
            item.sample_id for item in (*fold.train, *fold.validation, *fold.test)
        } <= observed_ids


def test_contract_roll_crossing_labels_are_excluded_fail_closed():
    roll_at = BASE + timedelta(days=12)
    samples = [sample(day) for day in range(30)]
    samples[10] = sample(
        10, horizon_days=4, segment="RIU6", segment_valid_until=roll_at
    )
    samples[11] = sample(
        11, horizon_days=1, segment="RIU6", segment_valid_until=roll_at
    )

    folds = purged_walk_forward(tuple(samples), config())

    used = {
        item.sample_id
        for fold in folds
        for item in (*fold.train, *fold.validation, *fold.test)
    }
    assert "s10" not in used
    assert "s11" in used


def test_unsorted_or_duplicate_samples_are_rejected_instead_of_silently_sorted():
    with pytest.raises(ValueError, match="chronological"):
        purged_walk_forward((sample(1), sample(0)), config())

    duplicate = TimedSample(
        sample_id="s0",
        observed_at=BASE + timedelta(hours=1),
        label_end_at=BASE + timedelta(days=1),
        market_segment="BTC-PERP",
    )
    with pytest.raises(ValueError, match="sample_id"):
        purged_walk_forward((sample(0), duplicate), config())
