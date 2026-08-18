"""Leakage-resistant time-based walk-forward splitting for strategy research.

The splitter never shuffles or synthesises observations.  It works on the
actual timestamped sample stream, keeps explicit embargo gaps between train,
validation and test windows, and purges any label whose outcome horizon reaches
into the next evaluation window.  Optional ``segment_valid_until`` metadata
fails closed for futures/contract-roll or other market-segment boundaries.

This is offline measurement infrastructure only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class TimedSample:
    """One point-in-time feature/label sample eligible for walk-forward use."""

    sample_id: str
    observed_at: datetime
    label_end_at: datetime
    market_segment: str
    segment_valid_until: datetime | None = None

    def __post_init__(self) -> None:
        if not self.sample_id.strip():
            raise ValueError("sample_id is required")
        if self.observed_at.tzinfo is None or self.label_end_at.tzinfo is None:
            raise ValueError("sample timestamps must be timezone-aware")
        if self.label_end_at < self.observed_at:
            raise ValueError("label_end_at cannot precede observed_at")
        if not self.market_segment.strip():
            raise ValueError("market_segment is required")
        if self.segment_valid_until is not None:
            if self.segment_valid_until.tzinfo is None:
                raise ValueError("segment_valid_until must be timezone-aware")
            if self.segment_valid_until < self.observed_at:
                raise ValueError("segment_valid_until cannot precede observed_at")

    @property
    def crosses_market_segment_boundary(self) -> bool:
        return (
            self.segment_valid_until is not None
            and self.label_end_at > self.segment_valid_until
        )


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    train_span: timedelta
    validation_span: timedelta
    test_span: timedelta
    embargo: timedelta
    step: timedelta

    def __post_init__(self) -> None:
        for name in ("train_span", "validation_span", "test_span", "step"):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.embargo < timedelta(0):
            raise ValueError("embargo must be non-negative")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_index: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    train: tuple[TimedSample, ...]
    validation: tuple[TimedSample, ...]
    test: tuple[TimedSample, ...]
    purged_sample_ids: tuple[str, ...]
    embargoed_sample_ids: tuple[str, ...]
    invalid_segment_sample_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.train or not self.validation or not self.test:
            raise ValueError("walk-forward fold must contain train/validation/test samples")


def _validate_input(samples: tuple[TimedSample, ...]) -> None:
    ids = [sample.sample_id for sample in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("sample_id values must be unique")
    times = [sample.observed_at for sample in samples]
    if times != sorted(times):
        raise ValueError("samples must be supplied in chronological order")


def _in_window(
    sample: TimedSample,
    start: datetime,
    end: datetime,
) -> bool:
    return start <= sample.observed_at < end


def purged_walk_forward(
    samples: tuple[TimedSample, ...] | list[TimedSample],
    config: WalkForwardConfig,
) -> tuple[WalkForwardFold, ...]:
    """Build deterministic rolling train/validation/test folds.

    Purge rule is deliberately strict: a train label must finish *before* the
    validation window and a validation label must finish *before* the test
    window.  Equality is treated as overlap because the outcome becomes known
    at the same timestamp the evaluation slice begins.

    Gaps in the input stream (weekends, exchange closures, maintenance) remain
    gaps.  No calendar backfill or synthetic sample generation occurs.
    """

    ordered = tuple(samples)
    _validate_input(ordered)
    if not ordered:
        return ()

    valid_samples = tuple(
        sample for sample in ordered if not sample.crosses_market_segment_boundary
    )
    invalid_segment_ids = tuple(
        sample.sample_id for sample in ordered if sample.crosses_market_segment_boundary
    )
    if not valid_samples:
        return ()

    anchor = ordered[0].observed_at
    last_observed = ordered[-1].observed_at
    folds: list[WalkForwardFold] = []
    fold_index = 0

    while True:
        train_start = anchor + config.step * fold_index
        train_end = train_start + config.train_span
        validation_start = train_end + config.embargo
        validation_end = validation_start + config.validation_span
        test_start = validation_end + config.embargo
        test_end = test_start + config.test_span

        if test_start > last_observed:
            break

        raw_train = tuple(
            sample
            for sample in valid_samples
            if _in_window(sample, train_start, train_end)
        )
        raw_validation = tuple(
            sample
            for sample in valid_samples
            if _in_window(sample, validation_start, validation_end)
        )
        test = tuple(
            sample
            for sample in valid_samples
            if _in_window(sample, test_start, test_end)
        )

        train = tuple(
            sample for sample in raw_train if sample.label_end_at < validation_start
        )
        validation = tuple(
            sample
            for sample in raw_validation
            if sample.label_end_at < test_start
        )

        purged_ids = tuple(
            sample.sample_id
            for sample in (*raw_train, *raw_validation)
            if sample not in train and sample not in validation
        )
        embargoed_ids = tuple(
            sample.sample_id
            for sample in valid_samples
            if (
                train_end <= sample.observed_at < validation_start
                or validation_end <= sample.observed_at < test_start
            )
        )

        if train and validation and test:
            folds.append(
                WalkForwardFold(
                    fold_index=fold_index,
                    train_start=train_start,
                    train_end=train_end,
                    validation_start=validation_start,
                    validation_end=validation_end,
                    test_start=test_start,
                    test_end=test_end,
                    train=train,
                    validation=validation,
                    test=test,
                    purged_sample_ids=purged_ids,
                    embargoed_sample_ids=embargoed_ids,
                    invalid_segment_sample_ids=invalid_segment_ids,
                )
            )

        fold_index += 1

    return tuple(folds)


__all__ = [
    "TimedSample",
    "WalkForwardConfig",
    "WalkForwardFold",
    "purged_walk_forward",
]
