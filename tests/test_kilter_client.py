"""Tests for the new-Kilter PowerSync parsing/diagnostic (no credentials)."""

import json

from kilter_garmin_sync.diagnose import (
    _looks_like_ascents,
    format_report,
    summarize_user_buckets,
)
from kilter_garmin_sync.kilter_client import iter_data_ops


def _op(object_type, object_id, data, op="PUT"):
    # PowerSync raw_data delivers the row payload as a JSON *string*.
    return {
        "op_id": object_id,
        "op": op,
        "object_type": object_type,
        "object_id": str(object_id),
        "checksum": 0,
        "data": json.dumps(data),
    }


def _data_msg(bucket, ops):
    return {"data": {"bucket": bucket, "has_more": False, "data": ops}}


def fake_stream():
    """A representative PowerSync stream: checkpoint, mixed buckets, complete."""
    user_bucket = 'user_buckets["c33babc5-20d8-46c3-93a9-79a614b37059"]'
    return [
        {
            "checkpoint": {
                "buckets": [
                    {"bucket": "global_climbs[]", "count": 37629},
                    {"bucket": user_bucket, "count": 3},
                ]
            }
        },
        _data_msg(
            "global_climbs[]",
            [_op("climbs", 1, {"uuid": "abc", "name": "Test Climb"})],
        ),
        _data_msg(
            user_bucket,
            [
                _op(
                    "ascents",
                    10,
                    {
                        "uuid": "asc-1",
                        "climb_uuid": "abc",
                        "angle": 40,
                        "is_ascent": True,
                        "difficulty": 18,
                        "tries": 2,
                        "climbed_at": "2026-09-20 18:05:00",
                    },
                ),
                _op(
                    "ascents",
                    11,
                    {
                        "uuid": "asc-2",
                        "climb_uuid": "def",
                        "angle": 45,
                        "is_ascent": True,
                        "difficulty": 20,
                        "tries": 1,
                        "climbed_at": "2026-09-20 19:00:00",
                    },
                ),
                _op("circuits", 12, {"uuid": "cir-1", "name": "My Circuit"}),
            ],
        ),
        {"checkpoint_complete": {"last_op_id": "12"}},
        # Anything after checkpoint_complete must be ignored.
        _data_msg(user_bucket, [_op("ascents", 99, {"uuid": "late"})]),
    ]


def test_iter_data_ops_decodes_raw_json_and_filters_bucket():
    ops = list(
        iter_data_ops(fake_stream(), bucket_prefix="user_buckets")
    )
    object_types = [op["object_type"] for _b, op in ops]
    assert object_types == ["ascents", "ascents", "circuits"]
    # raw_data JSON string is decoded to a dict.
    first = ops[0][1]["data"]
    assert first["climb_uuid"] == "abc"
    assert first["angle"] == 40


def test_summarize_counts_and_samples_user_buckets_only():
    summary = summarize_user_buckets(fake_stream())
    assert summary["saw_checkpoint_complete"] is True

    # Only user_buckets object_types are counted (not global_climbs "climbs").
    ots = summary["object_types"]
    assert set(ots) == {"ascents", "circuits"}
    assert ots["ascents"]["count"] == 2  # the post-complete op is ignored
    assert ots["circuits"]["count"] == 1

    # Fields come from a sample row.
    assert "climb_uuid" in ots["ascents"]["fields"]
    assert ots["ascents"]["sample"]["angle"] == 40

    # user bucket surfaced from the checkpoint message.
    assert summary["checkpoint_buckets"][0]["count"] == 3

    # The global catalog spans ALL buckets (incl. global_climbs "climbs").
    catalog = summary["global_catalog"]
    assert catalog["climbs"]["count"] == 1
    assert catalog["ascents"]["count"] == 2


def test_ascent_detection_and_report():
    summary = summarize_user_buckets(fake_stream())
    report = format_report(summary)
    assert "ASCENT-LIKE" in report
    assert "Phase 2" in report
    assert "ascents" in report


def test_looks_like_ascents_by_name_and_by_fields():
    assert _looks_like_ascents("ascents", [])
    assert _looks_like_ascents("bids", [])
    # unknown name but ascent-shaped fields
    assert _looks_like_ascents("foo", ["climb_uuid", "angle", "is_ascent"])
    # clearly not ascents
    assert not _looks_like_ascents("circuits", ["uuid", "name"])
