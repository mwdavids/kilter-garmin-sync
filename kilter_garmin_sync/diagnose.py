"""Phase-1 diagnostic: what user data does the new Kilter backend sync?

Authenticates to Keycloak, performs one PowerSync full sync, and enumerates the
``object_type`` values present in the user's ``user_buckets`` bucket(s) — with
per-type counts and a sample row schema. The point is to answer one question:
**are the user's ascents / logbook entries synced to their account?**

Run this on a GitHub Actions runner (``mode: diagnose`` in the export workflow),
not locally: the cloud runner reaches ``kiltergrips.com`` without the corporate
Global Secure Access throttling that cripples local streaming.

No secrets are printed. Sample rows contain the user's own climb metadata only.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, OrderedDict
from collections.abc import Iterable
from typing import Any

from kilter_garmin_sync.kilter_client import (
    USER_BUCKET_PREFIX,
    KilterAuthError,
    KilterSyncError,
    get_access_token,
    iter_data_ops,
    stream_sync,
)

# object_type names that would plausibly hold logbook/ascent entries.
ASCENT_HINT_WORDS = ("ascent", "tick", "bid", "log", "send", "attempt", "climb")


def summarize_user_buckets(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a PowerSync stream (works on live or fixture messages).

    Returns a dict with:

    * ``checkpoint_buckets``: ``[{"bucket", "count"}]`` for user buckets seen in
      the checkpoint message.
    * ``object_types``: ordered ``{object_type: {"count", "fields", "sample"}}``
      restricted to ``user_buckets`` ops.
    * ``saw_checkpoint_complete``: whether the stream terminated cleanly.
    """
    checkpoint_buckets: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    samples: dict[str, dict[str, Any]] = {}
    saw_complete = False

    def handle(msg: dict[str, Any]) -> bool:
        """Process one message; return False to stop iteration."""
        nonlocal saw_complete
        if "checkpoint" in msg and isinstance(msg["checkpoint"], dict):
            for b in msg["checkpoint"].get("buckets", []) or []:
                name = b.get("bucket", "")
                if name.startswith(USER_BUCKET_PREFIX):
                    checkpoint_buckets.append(
                        {"bucket": name, "count": b.get("count")}
                    )
        if "checkpoint_complete" in msg:
            saw_complete = True
            return False
        return True

    # We need both the checkpoint scan and the per-op scan, so tee the stream
    # through a small generator that records checkpoints and stops on complete.
    def gated() -> Iterable[dict[str, Any]]:
        for msg in messages:
            keep_going = handle(msg)
            yield msg
            if not keep_going:
                return

    for _bucket, op in iter_data_ops(
        gated(), bucket_prefix=USER_BUCKET_PREFIX, stop_at_checkpoint_complete=True
    ):
        object_type = op.get("object_type") or "<unknown>"
        if op.get("op") == "REMOVE":
            continue
        counts[object_type] += 1
        if object_type not in samples:
            row = op.get("data")
            if isinstance(row, dict):
                samples[object_type] = row

    object_types: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for object_type, count in counts.most_common():
        sample = samples.get(object_type, {})
        object_types[object_type] = {
            "count": count,
            "fields": sorted(sample.keys()),
            "sample": sample,
        }

    return {
        "checkpoint_buckets": checkpoint_buckets,
        "object_types": object_types,
        "saw_checkpoint_complete": saw_complete,
    }


def _looks_like_ascents(object_type: str, fields: list[str]) -> bool:
    name = object_type.lower()
    if any(word in name for word in ASCENT_HINT_WORDS):
        return True
    field_blob = " ".join(fields).lower()
    return "climb_uuid" in field_blob and (
        "angle" in field_blob or "is_ascent" in field_blob or "tries" in field_blob
    )


def format_report(summary: dict[str, Any]) -> str:
    """Render a human-readable report from :func:`summarize_user_buckets`."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("KILTER user_buckets DIAGNOSTIC")
    lines.append("=" * 70)

    buckets = summary.get("checkpoint_buckets", [])
    lines.append(f"\nuser_buckets in checkpoint: {len(buckets)}")
    for b in buckets:
        lines.append(f"  - {b['bucket']}  (count={b['count']})")

    object_types = summary.get("object_types", {})
    total_ops = sum(v["count"] for v in object_types.values())
    lines.append(
        f"\nobject_types in user_buckets: {len(object_types)} "
        f"({total_ops} PUT ops total)"
    )

    ascent_types: list[str] = []
    for object_type, info in object_types.items():
        flag = ""
        if _looks_like_ascents(object_type, info["fields"]):
            ascent_types.append(object_type)
            flag = "   <-- ASCENT-LIKE"
        lines.append(f"\n  * {object_type}  (count={info['count']}){flag}")
        lines.append(f"      fields: {', '.join(info['fields']) or '(none)'}")
        sample = info.get("sample") or {}
        if sample:
            lines.append(f"      sample: {json.dumps(sample, default=str)[:600]}")

    lines.append("\n" + "-" * 70)
    if ascent_types:
        lines.append(
            "VERDICT: ascent-like object_type(s) FOUND in user_buckets: "
            + ", ".join(ascent_types)
        )
        lines.append("=> Phase 2 (build the new-Kilter fetcher) is viable.")
    else:
        lines.append(
            "VERDICT: no obvious ascent/logbook object_type in user_buckets."
        )
        lines.append(
            "=> Review the object_types above; the logbook may be stranded "
            "in the old app."
        )
    if not summary.get("saw_checkpoint_complete"):
        lines.append(
            "WARNING: stream ended without checkpoint_complete "
            "(sync may be incomplete)."
        )
    lines.append("-" * 70)
    return "\n".join(lines)


def _redacted_summary_for_artifact(summary: dict[str, Any]) -> dict[str, Any]:
    """Summary safe to persist as a build artifact (keeps schema + samples,
    which are the user's own climb metadata, never tokens/passwords)."""
    return {
        "checkpoint_buckets": summary.get("checkpoint_buckets", []),
        "saw_checkpoint_complete": summary.get("saw_checkpoint_complete", False),
        "object_types": {
            ot: {
                "count": info["count"],
                "fields": info["fields"],
                "sample": info.get("sample", {}),
                "ascent_like": _looks_like_ascents(ot, info["fields"]),
            }
            for ot, info in summary.get("object_types", {}).items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = None
    if "--json-out" in argv:
        i = argv.index("--json-out")
        try:
            out_path = argv[i + 1]
        except IndexError:
            print("--json-out requires a path", file=sys.stderr)
            return 2

    username = os.environ.get("KILTER_USERNAME", "")
    password = os.environ.get("KILTER_PASSWORD", "")

    try:
        print("Authenticating to Keycloak...", flush=True)
        token = get_access_token(username, password)
        print("Authenticated. Streaming PowerSync (this can take a moment)...", flush=True)
        summary = summarize_user_buckets(stream_sync(token))
    except (KilterAuthError, KilterSyncError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(format_report(summary))

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(_redacted_summary_for_artifact(summary), fh, indent=2, default=str)
        print(f"\nWrote machine-readable summary to {out_path}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
