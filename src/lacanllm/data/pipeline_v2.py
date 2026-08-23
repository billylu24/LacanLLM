"""Unified CLI for the LacanLLM automated data and silver benchmark pipeline."""

from __future__ import annotations

import argparse
import gc
import json
import math
import sys
from pathlib import Path
from typing import Any

from lacanllm.data.io import read_jsonl
from lacanllm.data.pipeline_v2_core import (
    PROJECT_ROOT,
    SPLITS,
    PipelineConfig,
    audit_pipeline,
    build_consensus,
    build_generation_queue,
    build_split_manifest,
    clean_corpus,
    deduplicate_splits,
    hard_filter_split,
    load_split_manifest,
    pipeline_status,
    select_final_datasets,
    utc_now,
)
from lacanllm.data.pipeline_v2_models import TransformersBackend, run_generation, run_judge

DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "data" / "pipeline_v2.json"


def _generation_pending(config: PipelineConfig, split: str) -> bool:
    """Return whether a split has queued candidate IDs without generation records."""

    queue_path = config.interim("queues", split)
    output_path = config.interim("generations", split)
    queue_ids = {str(row["candidate_id"]) for _, row in read_jsonl(queue_path)}
    completed_rows = [row for _, row in read_jsonl(output_path)] if output_path.is_file() else []
    if any(row.get("config_hash") != config.config_hash for row in completed_rows):
        raise RuntimeError(f"Refusing to mix artifacts with a different config hash: {output_path}")
    completed_ids = {str(row["candidate_id"]) for row in completed_rows}
    return bool(queue_ids - completed_ids)


def _judge_pending(config: PipelineConfig, split: str, pass_name: str) -> bool:
    """Return whether deduplicated candidates still need one judge pass."""

    source_path = config.interim("deduplicated", split)
    output_path = config.interim(f"judge_{pass_name}", split)
    source_ids = {str(row["candidate_id"]) for _, row in read_jsonl(source_path)}
    completed_rows = [row for _, row in read_jsonl(output_path)] if output_path.is_file() else []
    if any(row.get("config_hash") != config.config_hash for row in completed_rows):
        raise RuntimeError(f"Refusing to mix artifacts with a different config hash: {output_path}")
    completed_ids = {str(row["candidate_id"]) for row in completed_rows}
    return bool(source_ids - completed_ids)


def append_record(config: PipelineConfig, command: str, status: str, payload: dict[str, Any]) -> None:
    """Append a machine-readable execution event to the single canonical record."""

    path = config.path("record_file")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# LacanLLM Pipeline v2 Record\n\n## Execution log\n", encoding="utf-8")
    event = {
        "timestamp": utc_now(),
        "command": command,
        "status": status,
        "pipeline_version": config.pipeline_version,
        "config_hash": config.config_hash,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"\n### {event['timestamp']} — `{command}` — {status}\n\n")
        handle.write("```json\n")
        handle.write(json.dumps(event, indent=2, ensure_ascii=False, sort_keys=True))
        handle.write("\n```\n")


def _run_all(config: PipelineConfig, *, smoke_limit: int | None = None) -> dict[str, Any]:
    """Run every stage, refilling deficient types until quotas or attempt caps."""

    result: dict[str, Any] = {"clean": clean_corpus(config), "split": build_split_manifest(config)}
    append_record(config, "run:clean", "completed", result["clean"])
    append_record(config, "run:split", "completed", result["split"])
    generation_settings = config.raw["generation"]
    judge_settings = config.raw["judge"]
    for split in SPLITS:
        if not config.interim("queues", split).is_file():
            build_generation_queue(config, split)
    generation_pending = any(_generation_pending(config, split) for split in SPLITS)
    generator = (
        TransformersBackend(
            str(generation_settings["model_id"]),
            load_in_4bit=bool(generation_settings["load_in_4bit"]),
        )
        if generation_pending
        else None
    )
    for split in SPLITS:
        result[f"generate_{split}"] = run_generation(config, split, limit=smoke_limit, backend=generator)
        append_record(config, f"run:generate:{split}", "completed", result[f"generate_{split}"])
    if generator is not None:
        del generator
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except ImportError:
            pass
    if smoke_limit is not None:
        return result
    round_number = 0
    while True:
        round_number += 1
        result[f"hard_filter_round_{round_number}"] = {
            split: hard_filter_split(config, split) for split in SPLITS
        }
        result[f"deduplicate_round_{round_number}"] = deduplicate_splits(config)
        append_record(
            config,
            f"run:hard-filter-deduplicate:round-{round_number}",
            "completed",
            {
                "hard_filter": result[f"hard_filter_round_{round_number}"],
                "deduplicate": result[f"deduplicate_round_{round_number}"],
            },
        )
        judge_pending = any(
            _judge_pending(config, split, pass_name)
            for split in SPLITS
            for pass_name in ("rubric", "adversarial")
        )
        judge = (
            TransformersBackend(
                str(judge_settings["model_id"]),
                load_in_4bit=bool(judge_settings["load_in_4bit"]),
            )
            if judge_pending
            else None
        )
        for split in SPLITS:
            for pass_name in ("rubric", "adversarial"):
                result[f"judge_{split}_{pass_name}_round_{round_number}"] = run_judge(
                    config,
                    split,
                    pass_name,
                    backend=judge,
                )
                append_record(
                    config,
                    f"run:judge:{split}:{pass_name}:round-{round_number}",
                    "completed",
                    result[f"judge_{split}_{pass_name}_round_{round_number}"],
                )
        if judge is not None:
            del judge
            gc.collect()
            try:
                import torch

                torch.cuda.empty_cache()
            except ImportError:
                pass
        result[f"consensus_round_{round_number}"] = build_consensus(config)
        selection = select_final_datasets(config, allow_incomplete=True)
        result[f"selection_round_{round_number}"] = selection
        append_record(
            config,
            f"run:consensus-select:round-{round_number}",
            "completed",
            {"consensus": result[f"consensus_round_{round_number}"], "selection": selection},
        )
        deficits = {
            split: {
                key: value
                for key, value in selection[split]["deficits"].items()
                if key != "source_floor" and value > 0
            }
            for split in SPLITS
        }
        source_shortfalls: dict[str, dict[str, int]] = {}
        manifest_splits = load_split_manifest(config)["splits"]
        for split in SPLITS:
            floor = int(config.raw.get("final_source_floors", {}).get(split, 0))
            if not floor:
                continue
            source_shortfalls[split] = {
                source: floor - int(selection[split]["sources"].get(source, 0))
                for source in manifest_splits[split]
                if int(selection[split]["sources"].get(source, 0)) < floor
            }
            if not source_shortfalls[split]:
                del source_shortfalls[split]
        if not any(deficits.values()) and not source_shortfalls:
            break
        generator: TransformersBackend | None = None
        refill_added = 0
        for split, missing in deficits.items():
            shortfalls = source_shortfalls.get(split, {})
            if not missing and not shortfalls:
                continue
            requested = {key: max(10, int(math.ceil(value * 2.2))) for key, value in missing.items()}
            source_refill_total = max(10, int(math.ceil(sum(shortfalls.values()) * 2.2))) if shortfalls else 0
            extra = max(0, source_refill_total - sum(requested.values()))
            refill_types = list(config.raw["final_quotas"][split])
            for index in range(extra):
                target_type = refill_types[index % len(refill_types)]
                requested[target_type] = requested.get(target_type, 0) + 1
            queue_result = build_generation_queue(
                config,
                split,
                requested=requested,
                append=True,
                preferred_sources=shortfalls,
                allow_partial=True,
            )
            result[f"refill_queue_{split}_round_{round_number}"] = queue_result
            refill_added += int(queue_result["added_rows"])
            if not queue_result["added_rows"]:
                continue
            if generator is None:
                generator = TransformersBackend(
                    str(generation_settings["model_id"]),
                    load_in_4bit=bool(generation_settings["load_in_4bit"]),
                )
            result[f"refill_generate_{split}_round_{round_number}"] = run_generation(
                config,
                split,
                backend=generator,
            )
            append_record(
                config,
                f"run:refill-generate:{split}:round-{round_number}",
                "completed",
                result[f"refill_generate_{split}_round_{round_number}"],
            )
        if generator is not None:
            del generator
            gc.collect()
            try:
                import torch

                torch.cuda.empty_cache()
            except ImportError:
                pass
        if refill_added == 0:
            raise RuntimeError(
                "Candidate sources and attempt caps are exhausted before final quotas were filled: "
                f"deficits={deficits}, source_shortfalls={source_shortfalls}"
            )
    result["audit"] = audit_pipeline(config)
    return result


def execute(args: argparse.Namespace, config: PipelineConfig) -> dict[str, Any]:
    if args.command == "clean":
        return clean_corpus(config)
    if args.command == "split":
        return build_split_manifest(config)
    if args.command == "generate":
        if not config.interim("queues", args.split).is_file():
            build_generation_queue(config, args.split)
        return run_generation(config, args.split, limit=args.limit)
    if args.command == "hard-filter":
        return hard_filter_split(config, args.split)
    if args.command == "deduplicate":
        return deduplicate_splits(config)
    if args.command == "judge":
        return run_judge(config, args.split, args.judge_pass, limit=args.limit)
    if args.command == "select":
        consensus = build_consensus(config)
        selection = select_final_datasets(config, allow_incomplete=args.allow_incomplete)
        return {"consensus": consensus, "selection": selection}
    if args.command == "audit":
        return audit_pipeline(config)
    if args.command == "status":
        return pipeline_status(config)
    if args.command == "run":
        return _run_all(config, smoke_limit=args.smoke_limit)
    raise ValueError(f"Unsupported command: {args.command}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "clean",
            "split",
            "generate",
            "hard-filter",
            "deduplicate",
            "judge",
            "select",
            "audit",
            "status",
            "run",
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--split", choices=SPLITS)
    parser.add_argument("--pass", dest="judge_pass", choices=("rubric", "adversarial"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--smoke-limit", type=int)
    args = parser.parse_args(argv)
    if args.command in {"generate", "hard-filter", "judge"} and not args.split:
        parser.error(f"{args.command} requires --split")
    if args.command == "judge" and not args.judge_pass:
        parser.error("judge requires --pass rubric|adversarial")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = PipelineConfig.load(args.config)
    command_text = " ".join(sys.argv[1:] if argv is None else argv)
    try:
        result = execute(args, config)
    except Exception as exc:
        append_record(config, command_text, "failed", {"error_type": type(exc).__name__, "error": str(exc)})
        raise
    append_record(config, command_text, "completed", result)
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
