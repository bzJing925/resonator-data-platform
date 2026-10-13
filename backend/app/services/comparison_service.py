"""Scalar cohorts compare only with verifiable extraction conditions and full counts."""

import hashlib
import json
import math
from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Device, ProcessingRun, StagedDeviceRow
from app.services.api_errors import api_error
from app.services.batch_scope import active_batch_clause
from app.services.field_metadata import COMPARISON_METRICS, NUMERIC_UNITS
from app.services.processing_retry import configuration_digest
from app.services.query_manifest import make_manifest, scope_version
from app.services.query_spec import _build_filters, _resolve_column, query_spec, stable_order
from app.services.replication_v3 import source_snapshot
from app.services.source_identity import batch_source_ref

STRUCTURE_FIELDS = ["eg", "fl", "ag", "s_param_port"]


def finite(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def statistics(rows, metric):
    values = np.array([row[metric] for row in rows if finite(row.get(metric))], dtype=float)
    n = len(values)
    scale = float(np.max(np.abs(values))) if n else 1.0
    scaled = values / (scale or 1.0)

    def rescale(value):
        result = float(value) * (scale or 1.0)
        return result if math.isfinite(result) else None

    return {
        "valid_n": n,
        "missing_n": len(rows) - n,
        "mean": rescale(scaled.mean()) if n else None,
        "median": rescale(np.median(scaled)) if n else None,
        "p25": rescale(np.quantile(scaled, 0.25, method="linear")) if n else None,
        "p75": rescale(np.quantile(scaled, 0.75, method="linear")) if n else None,
        "std": rescale(scaled.std(ddof=1)) if n >= 2 else None,
        "min": float(values.min()) if n else None,
        "max": float(values.max()) if n else None,
    }


def finite_difference(a, b):
    if a is None or b is None:
        return None
    result = b - a
    return result if math.isfinite(result) else None


def _quality(row, rule):
    if rule is None:
        return None
    if not rule.predicates or any(not finite(row.get(p.field)) for p in rule.predicates):
        return "unknown"
    passed = all(
        row[p.field] >= p.value if p.op == "gte" else row[p.field] <= p.value
        for p in rule.predicates
    )
    return "pass" if passed else "fail"


def _conditions(db, batch):
    run = db.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.batch_id == batch.id,
            ProcessingRun.status == "success",
            ProcessingRun.published_count > 0,
            ProcessingRun.base_result_revision == batch.result_revision - 1,
        )
        .order_by(ProcessingRun.finished_at.desc(), ProcessingRun.id.desc())
        .limit(1)
    )
    return condition_proof(batch, run)


def condition_proof(batch, run):
    """Shared metadata proof for a live read or one published SQLite snapshot."""
    if (
        run is None
        or not run.algorithm_digest
        or not run.mapping_digest
        or run.mapping_digest == "missing"
        or run.config_json.get("accepted_config_digest") != configuration_digest(run.config_json)
        or not finite(batch.f_start_ghz)
        or not finite(batch.f_end_ghz)
    ):
        raise api_error(422, "CONDITIONS_UNKNOWN", "缺少已发布处理条件，请重算或比较已保存快照")
    config = run.config_json
    expected = {
        "f_start_ghz": batch.f_start_ghz,
        "f_end_ghz": batch.f_end_ghz,
        "deembed": batch.deembedded,
        "deembed_method": batch.deembed_method,
        "process_type": batch.process_type,
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise api_error(422, "CONDITIONS_UNKNOWN", "当前批次条件与发布证据不一致")
    return {
        "frequency_ghz": [batch.f_start_ghz, batch.f_end_ghz],
        "deembedded": batch.deembedded,
        "deembed_method": batch.deembed_method,
        "process_type": batch.process_type,
        "config_digest": config["accepted_config_digest"],
        "algorithm_digest": run.algorithm_digest,
        "mapping_digest": run.mapping_digest,
    }, run.id


def _cohort(db, endpoint, metrics, rule, maximum):
    reference = endpoint.source.model_dump(mode="json")
    batch = db.scalar(
        select(Batch).where(
            Batch.origin_id == reference["origin_id"],
            Batch.source_batch_id == reference["source_batch_id"],
            active_batch_clause(),
        )
    )
    if batch is None:
        raise api_error(410, "SOURCE_UNAVAILABLE", "比较来源不可读")
    if reference["source_revision"] is None or batch_source_ref(db, batch) != reference:
        raise api_error(409, "SOURCE_CHANGED", "比较来源版本不一致，请重新查询")
    proof, published_run_id = _conditions(db, batch)
    spec = query_spec(**endpoint.query_spec.model_dump())
    fields = list(
        dict.fromkeys(
            [
                "id",
                "source_device_key",
                "wafer",
                "x",
                "y",
                "pf",
                *STRUCTURE_FIELDS,
                *metrics,
                *([p.field for p in rule.predicates] if rule else []),
            ]
        )
    )
    where = [active_batch_clause(), Device.batch_id == batch.id, *_build_filters(spec.filters)]
    statement = select(
        *[_resolve_column(field).label(field) for field in fields],
        StagedDeviceRow.payload_json.label("_published_payload"),
        StagedDeviceRow.payload_digest.label("_published_digest"),
    )
    statement = statement.select_from(Device).join(Batch, Device.batch_id == Batch.id)
    statement = statement.outerjoin(
        StagedDeviceRow,
        (StagedDeviceRow.run_id == published_run_id)
        & (StagedDeviceRow.source_device_key == Device.source_device_key),
    )
    statement = statement.where(*where).order_by(*stable_order(spec.order_by)).limit(maximum + 1)
    result = db.execute(statement.execution_options(yield_per=2000)).mappings()
    rows = []
    try:
        for row in result:
            if len(rows) == maximum:
                raise api_error(413, "COMPARISON_ROW_LIMIT", "比较超过完整读取上限，不能用采样替代")
            item = {
                key: (None if isinstance(value, float) and not math.isfinite(value) else value)
                for key, value in dict(row).items()
            }
            if not item["source_device_key"]:
                raise api_error(422, "CONDITIONS_UNKNOWN", "器件缺少稳定来源身份")
            payload, digest = item.pop("_published_payload"), item.pop("_published_digest")
            if not isinstance(payload, dict):
                raise api_error(422, "CONDITIONS_UNKNOWN", "缺少当前指标的发布证据")
            encoded = json.dumps(payload, sort_keys=True, allow_nan=False, default=str)
            checked_fields = list(
                dict.fromkeys(
                    [
                        *metrics,
                        *STRUCTURE_FIELDS,
                        *([p.field for p in rule.predicates] if rule else []),
                    ]
                )
            )
            if hashlib.sha256(encoded.encode()).hexdigest() != digest or any(
                field not in payload or payload[field] != item[field] for field in checked_fields
            ):
                raise api_error(422, "CONDITIONS_UNKNOWN", "指标或结构已改变，原处理证据不再适用")
            item["_source_ref"] = {**reference, "source_device_key": item["source_device_key"]}
            item["_quality_rule"] = _quality(item, rule)
            rows.append(item)
    finally:
        result.close()
    filters = {
        "op": "and",
        "children": [spec.filters, {"field": "batch_id", "op": "eq", "value": batch.id}],
    }
    return {
        "source": reference,
        "rows": rows,
        "_fields": fields,
        "_filters": filters,
        "_batch_id": batch.id,
        "_proof": proof,
    }


def _structure(row):
    return tuple(row[field] for field in STRUCTURE_FIELDS)


def _summarize(cohort, metrics, group_fields, rule):
    grouped = defaultdict(list)
    for row in cohort["rows"]:
        grouped[tuple(row[field] for field in group_fields)].append(row)
    groups = []
    for key, rows in sorted(grouped.items(), key=lambda item: repr(item[0])):
        groups.append(
            {
                "group": dict(zip(group_fields, key, strict=True)),
                "metrics": {metric: statistics(rows, metric) for metric in metrics},
            }
        )
    statuses = Counter(row["_quality_rule"] for row in cohort["rows"])
    pf = Counter(row["pf"] if row["pf"] in {"Y", "N"} else "unknown" for row in cohort["rows"])
    evaluated = statuses["pass"] + statuses["fail"]
    manifest = make_manifest(
        query_spec(cohort["_filters"], cohort["_fields"]),
        {cohort["_batch_id"]: cohort["source"]},
        True,
        len(cohort["rows"]),
        len(cohort["rows"]),
    )
    return {
        "source": cohort["source"],
        "rows": cohort["rows"],
        "groups": groups,
        "statistics": {metric: statistics(cohort["rows"], metric) for metric in metrics},
        "quality": {
            "rule": rule.model_dump(mode="json") if rule else None,
            "pass": statuses["pass"],
            "fail": statuses["fail"],
            "unknown": statuses["unknown"],
            "evaluated": evaluated,
            "pass_rate": statuses["pass"] / evaluated if evaluated else None,
        },
        "pf": {"Y": pf["Y"], "N": pf["N"], "unknown": pf["unknown"]},
        "manifest": manifest.model_dump(mode="json"),
    }


def compare(engine, request):
    comparison, rule = request.comparison, request.quality_rule
    metrics, groups = comparison.metrics, comparison.group_fields
    if set(metrics) - COMPARISON_METRICS or set(groups) - set(STRUCTURE_FIELDS):
        raise api_error(422, "COMPARISON_FIELD_INVALID", "比较指标或分组字段不支持")
    if comparison.wafer_metric is not None and comparison.wafer_metric not in metrics:
        raise api_error(422, "COMPARISON_FIELD_INVALID", "wafer指标必须在比较指标中")
    if rule:
        for predicate in rule.predicates:
            if NUMERIC_UNITS.get(predicate.field) != predicate.unit or not finite(predicate.value):
                raise api_error(422, "UNITS_DIFFER", "规则字段单位未知或不一致")
    from app.config import get_settings

    maximum = get_settings().COMPARISON_MAX_ROWS
    with source_snapshot(engine) as db:
        before = scope_version(db)
        a = _cohort(db, comparison.a, metrics, rule, maximum)
        b = _cohort(db, comparison.b, metrics, rule, maximum)
        differences = [key for key in a["_proof"] if a["_proof"][key] != b["_proof"][key]]
        frequency = comparison.common_frequency_ghz
        if differences or (
            frequency is not None and list(frequency) != a["_proof"]["frequency_ghz"]
        ):
            raise api_error(
                422,
                "CONDITIONS_DIFFER",
                "提取条件不同，裁剪查询不能改变已提取标量",
                context={"fields": differences or ["common_frequency_ghz"]},
            )
        shapes_a, shapes_b = (
            {_structure(row) for row in a["rows"]},
            {_structure(row) for row in b["rows"]},
        )
        excluded = {"a": 0, "b": 0}
        if shapes_a != shapes_b:
            differences.extend(
                [
                    field
                    for field, index in [("structure", slice(0, 3)), ("s_param_port", 3)]
                    if {shape[index] for shape in shapes_a} != {shape[index] for shape in shapes_b}
                ]
            )
            if comparison.condition_policy == "require_equal":
                raise api_error(
                    422,
                    "CONDITIONS_DIFFER",
                    "端口或结构不同，请明确核对共同子集",
                    context={"fields": ["structure", "s_param_port"]},
                )
            shared = shapes_a & shapes_b
            if not shared:
                raise api_error(422, "CONDITIONS_DIFFER", "没有可核验的共同条件子集")
            subset = {
                "op": "or",
                "children": [
                    {
                        "op": "and",
                        "children": [
                            {"field": field, "op": "eq", "value": value}
                            for field, value in zip(STRUCTURE_FIELDS, shape, strict=True)
                        ],
                    }
                    for shape in sorted(shared, key=repr)
                ],
            }
            for name, cohort in [("a", a), ("b", b)]:
                original = len(cohort["rows"])
                cohort["rows"] = [row for row in cohort["rows"] if _structure(row) in shared]
                excluded[name] = original - len(cohort["rows"])
                cohort["_filters"]["children"].append(subset)
        summarized = {
            name: _summarize(cohort, metrics, groups, rule) for name, cohort in [("a", a), ("b", b)]
        }
        indexed = [
            {repr(group["group"]): group for group in summarized[name]["groups"]}
            for name in ["a", "b"]
        ]
        deltas = []
        for key in sorted(indexed[0].keys() & indexed[1].keys()):
            ga, gb = indexed[0][key], indexed[1][key]
            for metric in metrics:
                sa, sb = ga["metrics"][metric], gb["metrics"][metric]
                deltas.append(
                    {
                        "group": ga["group"],
                        "metric": metric,
                        "a_valid_n": sa["valid_n"],
                        "b_valid_n": sb["valid_n"],
                        "mean_delta": (
                            finite_difference(sa["mean"], sb["mean"])
                        ),
                    }
                )
        result = {
            "schema_version": 1,
            "cohorts": summarized,
            "deltas": deltas,
            "units": {metric: NUMERIC_UNITS[metric] for metric in metrics},
            "conditions": {
                "a": a["_proof"],
                "b": b["_proof"],
                "differences": differences,
                "policy": comparison.condition_policy,
                "excluded_rows": excluded,
            },
        }
    with Session(engine) as db:
        if scope_version(db) != before:
            raise api_error(409, "QUERY_SOURCE_CHANGED", "比较期间来源变化，请重新读取")
    return result


def inspect_conditions(engine, request):
    """Read publication evidence and actual shape tuples, without computing statistics."""
    from fastapi import HTTPException

    from app.config import get_settings

    comparison, rule = request.comparison, request.quality_rule
    if set(comparison.metrics) - COMPARISON_METRICS or set(comparison.group_fields) - set(
        STRUCTURE_FIELDS
    ):
        raise api_error(422, "COMPARISON_FIELD_INVALID", "比较字段不支持")
    if rule:
        for predicate in rule.predicates:
            if NUMERIC_UNITS.get(predicate.field) != predicate.unit or not finite(predicate.value):
                raise api_error(422, "UNITS_DIFFER", "规则字段单位未知或不一致")
    metrics = list(dict.fromkeys([
        *comparison.metrics, *([p.field for p in rule.predicates] if rule else []),
    ]))
    endpoints = {}
    with source_snapshot(engine) as db:
        before = scope_version(db)
        for name in ["a", "b"]:
            endpoint = getattr(comparison, name)
            item = {"source": endpoint.source.model_dump(mode="json"),
                "query_spec": endpoint.query_spec.model_dump(mode="json"),
                "conditions": None, "structures": [], "matched_total": None, "issues": []}
            try:
                cohort = _cohort(db, endpoint, metrics, None, get_settings().COMPARISON_MAX_ROWS)
                item.update(conditions=cohort["_proof"], matched_total=len(cohort["rows"]),
                    structures=[list(shape) for shape in sorted(
                        {_structure(row) for row in cohort["rows"]}, key=repr)])
            except HTTPException as exc:
                detail = exc.detail
                if not isinstance(detail, dict) or detail.get("code") not in {
                    "CONDITIONS_UNKNOWN", "COMPARISON_ROW_LIMIT",
                }:
                    raise
                item["issues"] = [{"code": detail["code"], "message": detail["message"]}]
            endpoints[name] = item
    with Session(engine) as db:
        if scope_version(db) != before:
            raise api_error(409, "QUERY_SOURCE_CHANGED", "条件审查期间来源变化，请重新读取")
    return {"schema_version": 1, "endpoints": endpoints,
        "units": {metric: NUMERIC_UNITS[metric] for metric in comparison.metrics}}
