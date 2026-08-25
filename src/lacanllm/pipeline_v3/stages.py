from __future__ import annotations

import itertools
import json
import math
import platform
import random
import re
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .backend import make_backend
from .config import PipelineConfig, require_hash
from .io import append_jsonl, completed_ids, file_hash, object_hash, read_jsonl, write_json, write_jsonl
from .prompts import generation_prompt, judgment_prompt
from .records import hard_filter, judgment_passes, validate_generation, validate_judgment

SOURCE_ROWS = 32_028
SOURCE_SHA256 = "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da"
SPLIT_PRECEDENCE = {"Test": 0, "Validation": 1, "Train": 2}


def _rows(config: PipelineConfig, name: str) -> list[dict[str, Any]]:
    path = config.artifact(name)
    rows = list(read_jsonl(path))
    require_hash(rows, config.config_hash, path)
    return rows


def _source_path(config: PipelineConfig) -> Path:
    return Path(config.raw["source_path"])


def preflight(config: PipelineConfig, check_remote: bool = True) -> dict[str, Any]:
    source = _source_path(config)
    source_hash = file_hash(source)
    rows = sum(1 for _ in read_jsonl(source))
    if source_hash != SOURCE_SHA256 or rows != SOURCE_ROWS:
        raise ValueError(f"immutable source mismatch: rows={rows}, sha256={source_hash}")
    report: dict[str, Any] = {
        **config.stamp(),
        "source_rows": rows,
        "source_sha256": source_hash,
        "python": platform.python_version(),
        "profile": config.profile,
        "models": {},
    }
    try:
        import torch
        import transformers

        report.update(
            torch_version=torch.__version__,
            transformers_version=transformers.__version__,
            cuda_available=torch.cuda.is_available(),
            gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            gpu_memory_bytes=torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else 0,
        )
    except ImportError as exc:
        report["runtime_error"] = str(exc)
    if check_remote:
        from huggingface_hub import HfApi

        api = HfApi()
        for role in ("generator", "judge"):
            settings = config.raw[role]
            info = api.model_info(settings["model_id"], revision=settings["revision"])
            report["models"][role] = {"model_id": info.id, "revision": info.sha}
            if info.sha != settings["revision"]:
                raise ValueError(f"{role} revision resolved to {info.sha}, expected {settings['revision']}")
    write_json(config.artifact("reports/preflight.json"), report)
    return report


def clean(config: PipelineConfig) -> dict[str, Any]:
    output = config.artifact("01_clean/paragraphs.jsonl")
    rows: list[dict[str, Any]] = []
    changed = 0
    for source in read_jsonl(_source_path(config)):
        raw = source["raw_text"]
        operations = source.get("cleaning_operations", [])
        cleaned = unicodedata.normalize("NFKC", raw) if "unicode_nfkc" in operations else raw
        if cleaned != source["cleaned_text"]:
            raise ValueError(f"cleaning replay mismatch for {source['paragraph_id']}")
        changed += cleaned != raw
        rows.append({**source, "text": cleaned, "source_snapshot_sha256": SOURCE_SHA256, **config.stamp()})
    if len(rows) != SOURCE_ROWS:
        raise ValueError(f"expected {SOURCE_ROWS} rows after cleaning, found {len(rows)}")
    write_jsonl(output, rows)
    report = {**config.stamp(), "rows": len(rows), "unicode_nfkc_rows": changed, "artifact_sha256": file_hash(output)}
    write_json(config.artifact("reports/clean.json"), report)
    return report


def _choose_five(source_counts: Counter[str], sources: list[str], target: float) -> tuple[str, ...]:
    return min(
        itertools.combinations(sources, 5), key=lambda group: abs(sum(source_counts[name] for name in group) - target)
    )


def split(config: PipelineConfig) -> dict[str, Any]:
    rows = _rows(config, "01_clean/paragraphs.jsonl")
    counts = Counter(row["source_file"] for row in rows)
    sources = sorted(counts)
    random.Random(config.raw["seed"]).shuffle(sources)
    target = len(rows) * 0.10
    test_sources = set(_choose_five(counts, sources, target))
    remaining = [source for source in sources if source not in test_sources]
    validation_sources = set(_choose_five(counts, remaining, target))
    assignments = {
        source: "Test" if source in test_sources else "Validation" if source in validation_sources else "Train"
        for source in counts
    }
    output_rows = [{**row, "split": assignments[row["source_file"]]} for row in rows]
    output = config.artifact("02_split/paragraphs.jsonl")
    write_jsonl(output, output_rows)
    split_counts = Counter(row["split"] for row in output_rows)
    report = {
        **config.stamp(),
        "rows": dict(sorted(split_counts.items())),
        "sources": {
            name: sorted(source for source, assigned in assignments.items() if assigned == name)
            for name in SPLIT_PRECEDENCE
        },
        "artifact_sha256": file_hash(output),
    }
    write_json(config.artifact("reports/split.json"), report)
    return report


def _context(row: dict[str, Any], context_id: str) -> dict[str, Any]:
    return {
        "context_id": context_id,
        "paragraph_id": row["paragraph_id"],
        "paragraph_index": row["paragraph_index"],
        "source_file": row["source_file"],
        "text": row["text"],
    }


def queue(config: PipelineConfig) -> dict[str, Any]:
    rows = _rows(config, "02_split/paragraphs.jsonl")
    settings = config.raw["queue"]
    rng = random.Random(config.raw["seed"])
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_source: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_split[row["split"]].append(row)
        by_source[(row["split"], row["source_file"])].append(row)
    for split_rows in by_split.values():
        split_rows.sort(key=lambda row: (row["source_file"], row["paragraph_index"], row["paragraph_id"]))
        rng.shuffle(split_rows)
    targets = config.raw["targets"]
    total_target = sum(targets.values()) or settings["count"]
    candidates: list[dict[str, Any]] = []
    for split_name in ("Train", "Validation", "Test"):
        requested = (
            settings["count"]
            if config.profile == "smoke" and split_name == "Train"
            else math.ceil(settings["count"] * targets[split_name] / total_target)
        )
        if requested <= 0:
            continue
        pair_count = (
            settings["pair_count"]
            if config.profile == "smoke"
            else round(requested * settings["pair_count"] / settings["count"])
        )
        single_count = requested - pair_count
        used: set[str] = set()
        for row in by_split[split_name]:
            if (
                len([item for item in candidates if item["split"] == split_name and item["context_kind"] == "single"])
                >= single_count
            ):
                break
            contexts = [_context(row, "context_1")]
            candidate_id = object_hash(
                {"split": split_name, "contexts": [row["paragraph_id"]], "seed": config.raw["seed"]}
            )
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "split": split_name,
                    "context_kind": "single",
                    "contexts": contexts,
                    **config.stamp(),
                }
            )
            used.add(row["paragraph_id"])
        pair_added = 0
        source_keys = sorted(key for key in by_source if key[0] == split_name)
        rng.shuffle(source_keys)
        for key in source_keys:
            source_rows = sorted(by_source[key], key=lambda row: row["paragraph_index"])
            for left, right in zip(source_rows, source_rows[1:], strict=False):
                distance = right["paragraph_index"] - left["paragraph_index"]
                combined = len(left["text"]) + len(right["text"])
                if (
                    pair_added < pair_count
                    and 0 < distance <= settings["max_pair_distance"]
                    and combined <= settings["max_context_chars"]
                    and left["paragraph_id"] not in used
                    and right["paragraph_id"] not in used
                ):
                    contexts = [_context(left, "context_1"), _context(right, "context_2")]
                    candidate_id = object_hash(
                        {
                            "split": split_name,
                            "contexts": [item["paragraph_id"] for item in contexts],
                            "seed": config.raw["seed"],
                        }
                    )
                    candidates.append(
                        {
                            "candidate_id": candidate_id,
                            "split": split_name,
                            "context_kind": "pair",
                            "contexts": contexts,
                            **config.stamp(),
                        }
                    )
                    used.update(item["paragraph_id"] for item in contexts)
                    pair_added += 1
                if pair_added >= pair_count:
                    break
            if pair_added >= pair_count:
                break
        actual = len([item for item in candidates if item["split"] == split_name])
        if actual != requested:
            raise ValueError(f"unable to queue {requested} candidates for {split_name}; queued {actual}")
    output = config.artifact("03_queue/candidates.jsonl")
    write_jsonl(output, candidates)
    report = {
        **config.stamp(),
        "rows": len(candidates),
        "single": sum(item["context_kind"] == "single" for item in candidates),
        "pair": sum(item["context_kind"] == "pair" for item in candidates),
        "artifact_sha256": file_hash(output),
    }
    write_json(config.artifact("reports/queue.json"), report)
    return report


def _run_model_stage(
    config: PipelineConfig,
    role: str,
    input_name: str,
    output_name: str,
    prompt_builder: Callable[[dict[str, Any], str | None], str],
    validator: Callable[[Any], dict[str, Any]] | Callable[[Any, dict[str, Any]], dict[str, Any]],
    backend_override: str | None = None,
) -> dict[str, Any]:
    inputs = _rows(config, input_name)
    output = config.artifact(output_name)
    existing = list(read_jsonl(output))
    require_hash(existing, config.config_hash, output)
    done = completed_ids(output)
    pending = [source for source in inputs if source["candidate_id"] not in done]
    appended = retries = 0
    started = time.monotonic()
    backend = None
    if pending:
        backend = make_backend(role, config.raw, config.artifact(f"offload/{role}"), backend_override)
    try:
        for source in pending:
            error: str | None = None
            for attempt in range(2):
                payload, metrics = backend.call(prompt_builder(source, error), source, repair=attempt == 1)
                try:
                    validated = validator(payload, source) if role == "generator" else validator(payload)  # type: ignore[call-arg]
                    break
                except ValueError as exc:
                    error = str(exc)
                    if attempt == 1:
                        append_jsonl(
                            config.artifact(f"errors/{role}.jsonl"),
                            {
                                "candidate_id": source["candidate_id"],
                                "attempt": attempt + 1,
                                "error": error,
                                "parsed_payload": payload,
                                **config.stamp(),
                            },
                        )
                        raise RuntimeError(
                            f"{role} failed structured output after one repair for {source['candidate_id']}: {error}"
                        ) from exc
                    retries += 1
            row = {
                **source,
                **validated,
                f"{role}_model_id": config.raw[role]["model_id"],
                f"{role}_model_revision": config.raw[role]["revision"],
                f"{role}_prompt_version": config.raw[role]["prompt_version"],
                f"{role}_metrics": metrics,
                f"{role}_repair_retries": int(error is not None),
            }
            if role == "judge":
                row["quality_pass"] = judgment_passes(validated)
            append_jsonl(output, row)
            appended += 1
    finally:
        if backend is not None:
            backend.close()
    all_rows = list(read_jsonl(output))
    report = {
        **config.stamp(),
        "role": role,
        "input_rows": len(inputs),
        "total_rows": len(all_rows),
        "appended_rows": appended,
        "backend_loaded": backend is not None,
        "repair_retries": retries,
        "wall_seconds": time.monotonic() - started,
        "artifact_sha256": file_hash(output) if output.exists() else None,
    }
    write_json(config.artifact(f"reports/{role}.json"), report)
    return report


def generate(config: PipelineConfig, backend: str | None = None) -> dict[str, Any]:
    return _run_model_stage(
        config,
        "generator",
        "03_queue/candidates.jsonl",
        "04_generate/records.jsonl",
        generation_prompt,
        validate_generation,
        backend,
    )


def apply_hard_filter(config: PipelineConfig) -> dict[str, Any]:
    rows = _rows(config, "04_generate/records.jsonl")
    output_rows = []
    for row in rows:
        reasons = hard_filter(row)
        output_rows.append({**row, "hard_filter_pass": not reasons, "hard_filter_reasons": reasons})
    output = config.artifact("05_hard_filter/records.jsonl")
    write_jsonl(output, output_rows)
    report = {
        **config.stamp(),
        "rows": len(output_rows),
        "passing": sum(row["hard_filter_pass"] for row in output_rows),
        "artifact_sha256": file_hash(output),
    }
    write_json(config.artifact("reports/hard_filter.json"), report)
    return report


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.casefold())).strip()


def _simhash64(text: str) -> int:
    tokens = _normalize(text).split()
    features = tokens if len(tokens) < 3 else [" ".join(tokens[index : index + 3]) for index in range(len(tokens) - 2)]
    vector = [0] * 64
    for feature in features:
        hashed = int(object_hash(feature)[:16], 16)
        for bit in range(64):
            vector[bit] += 1 if hashed & (1 << bit) else -1
    return sum((1 << bit) for bit, value in enumerate(vector) if value >= 0)


def _distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def deduplicate(config: PipelineConfig) -> dict[str, Any]:
    rows = [row for row in _rows(config, "05_hard_filter/records.jsonl") if row["hard_filter_pass"]]
    rows.sort(key=lambda row: (SPLIT_PRECEDENCE[row["split"]], row["candidate_id"]))
    thresholds = config.raw["dedup"]
    kept_signatures: list[tuple[str, str, str, int, int, int, str]] = []
    output_rows: list[dict[str, Any]] = []
    for row in rows:
        question = _normalize(row["question"])
        answer = _normalize(row["reference_answer"])
        context = _normalize("\n".join(item["text"] for item in row["contexts"]))
        signatures = (_simhash64(question), _simhash64(answer), _simhash64(context))
        duplicate_of = None
        for old_q, old_a, old_c, q_sig, a_sig, c_sig, candidate_id in kept_signatures:
            if (
                question == old_q
                or answer == old_a
                or context == old_c
                or (
                    _distance(signatures[0], q_sig) <= thresholds["question_distance"]
                    and _distance(signatures[1], a_sig) <= thresholds["answer_distance"]
                    and _distance(signatures[2], c_sig) <= thresholds["context_distance"]
                )
            ):
                duplicate_of = candidate_id
                break
        accepted = duplicate_of is None
        if accepted:
            kept_signatures.append((question, answer, context, *signatures, row["candidate_id"]))
        output_rows.append({**row, "dedup_pass": accepted, "duplicate_of": duplicate_of})
    output = config.artifact("06_deduplicate/records.jsonl")
    write_jsonl(output, output_rows)
    report = {
        **config.stamp(),
        "rows": len(output_rows),
        "passing": sum(row["dedup_pass"] for row in output_rows),
        "artifact_sha256": file_hash(output),
    }
    write_json(config.artifact("reports/deduplicate.json"), report)
    return report


def judge(config: PipelineConfig, backend: str | None = None) -> dict[str, Any]:
    source = _rows(config, "06_deduplicate/records.jsonl")
    accepted_path = config.artifact("06_deduplicate/passing.jsonl")
    write_jsonl(accepted_path, (row for row in source if row["dedup_pass"]))
    return _run_model_stage(
        config,
        "judge",
        "06_deduplicate/passing.jsonl",
        "07_judge/records.jsonl",
        judgment_prompt,
        validate_judgment,
        backend,
    )


def select(config: PipelineConfig) -> dict[str, Any]:
    rows = _rows(config, "07_judge/records.jsonl")
    passing = [row for row in rows if row["quality_pass"]]
    report: dict[str, Any] = {**config.stamp(), "available_passing": len(passing), "selected": {}}
    for split_name, target in config.raw["targets"].items():
        selected = sorted((row for row in passing if row["split"] == split_name), key=lambda row: row["candidate_id"])[
            :target
        ]
        if config.profile == "production" and len(selected) < target:
            raise ValueError(f"only {len(selected)} quality-passing rows for {split_name}; target is {target}")
        path = config.artifact(f"08_select/{split_name.lower()}.jsonl")
        write_jsonl(path, selected)
        report["selected"][split_name] = {"rows": len(selected), "sha256": file_hash(path)}
    write_json(config.artifact("reports/select.json"), report)
    return report


def seal(config: PipelineConfig) -> dict[str, Any]:
    test_path = config.artifact("08_select/test.jsonl")
    if not test_path.exists():
        raise ValueError("Test selection does not exist")
    seal_path = config.artifact("09_seal/test_seal.json")
    new_seal = {
        **config.stamp(),
        "test_sha256": file_hash(test_path),
        "test_rows": sum(1 for _ in read_jsonl(test_path)),
        "access_policy": "evaluation prohibited until winner_lock.json exists",
    }
    if seal_path.exists():
        old = json.loads(seal_path.read_text(encoding="utf-8"))
        if old["test_sha256"] != new_seal["test_sha256"]:
            raise ValueError("sealed Test content changed")
    write_json(seal_path, new_seal)
    return new_seal


def audit(config: PipelineConfig) -> dict[str, Any]:
    split_report = json.loads(config.artifact("reports/split.json").read_text(encoding="utf-8"))
    source_sets = {name: set(values) for name, values in split_report["sources"].items()}
    overlap = {
        f"{left}:{right}": sorted(source_sets[left] & source_sets[right])
        for left, right in itertools.combinations(("Train", "Validation", "Test"), 2)
    }
    selected: dict[str, Any] = {}
    for name in ("Train", "Validation"):
        path = config.artifact(f"08_select/{name.lower()}.jsonl")
        selected[name] = {"rows": sum(1 for _ in read_jsonl(path)), "sha256": file_hash(path)}
    seal_data = json.loads(config.artifact("09_seal/test_seal.json").read_text(encoding="utf-8"))
    test_path = config.artifact("08_select/test.jsonl")
    current_test_hash = file_hash(test_path)
    if current_test_hash != seal_data["test_sha256"]:
        raise ValueError("sealed Test content hash no longer matches its seal")
    report = {
        **config.stamp(),
        "source_overlap": overlap,
        "source_disjoint": not any(overlap.values()),
        "selected_unsealed": selected,
        "sealed_test": {"rows": seal_data["test_rows"], "sha256": current_test_hash},
        "generator": {key: config.raw["generator"][key] for key in ("model_id", "revision", "prompt_version")},
        "judge": {key: config.raw["judge"][key] for key in ("model_id", "revision", "prompt_version")},
    }
    if not report["source_disjoint"]:
        raise ValueError(f"source overlap detected: {overlap}")
    write_json(config.artifact("reports/audit.json"), report)
    return report


def status(config: PipelineConfig) -> dict[str, Any]:
    artifacts = [
        "01_clean/paragraphs.jsonl",
        "02_split/paragraphs.jsonl",
        "03_queue/candidates.jsonl",
        "04_generate/records.jsonl",
        "05_hard_filter/records.jsonl",
        "06_deduplicate/records.jsonl",
        "07_judge/records.jsonl",
    ]
    return {
        **config.stamp(),
        "profile": config.profile,
        "artifacts": {
            name: {
                "exists": config.artifact(name).exists(),
                "rows": sum(1 for _ in read_jsonl(config.artifact(name))),
                "sha256": file_hash(config.artifact(name)) if config.artifact(name).exists() else None,
            }
            for name in artifacts
        },
    }


def run(config: PipelineConfig, backend: str | None = None, remote_preflight: bool = True) -> dict[str, Any]:
    if config.profile == "production":
        config.validate(production=True)
    preflight(config, check_remote=remote_preflight and backend != "fake")
    clean(config)
    split(config)
    queue(config)
    generation = generate(config, backend)
    hard = apply_hard_filter(config)
    dedup = deduplicate(config)
    judgment = judge(config, backend)
    selection = select(config)
    seal_data = seal(config)
    audit_data = audit(config)
    return {
        "generation": generation,
        "hard_filter": hard,
        "deduplicate": dedup,
        "judgment": judgment,
        "selection": selection,
        "seal": seal_data,
        "audit": audit_data,
    }


def smoke(config: PipelineConfig, backend: str | None = None, remote_preflight: bool = True) -> dict[str, Any]:
    if config.profile != "smoke":
        raise ValueError("smoke command requires a smoke profile")
    before = status(config)
    result = run(config, backend=backend, remote_preflight=remote_preflight)
    after_first = status(config)
    generation_second = generate(config, backend)
    hard_second = apply_hard_filter(config)
    dedup_second = deduplicate(config)
    judgment_second = judge(config, backend)
    after_second = status(config)
    stage_names = ("04_generate/records.jsonl", "07_judge/records.jsonl")
    unchanged = all(
        after_first["artifacts"][name]["sha256"] == after_second["artifacts"][name]["sha256"] for name in stage_names
    )
    generated_rows = after_first["artifacts"]["04_generate/records.jsonl"]["rows"]
    generated = _rows(config, "04_generate/records.jsonl")
    judged = _rows(config, "07_judge/records.jsonl")
    all_metrics = [row["generator_metrics"] for row in generated] + [row["judge_metrics"] for row in judged]
    total_tokens = sum(item.get("output_tokens", 0) for item in all_metrics)
    total_seconds = sum(item.get("seconds", 0.0) for item in all_metrics)
    report = {
        **config.stamp(),
        "backend": backend or config.raw["generator"]["backend"],
        "fresh_at_start": not before["artifacts"]["04_generate/records.jsonl"]["exists"],
        "generated_rows": generated_rows,
        "judged_rows": len(judged),
        "hard_filter_passing": result["hard_filter"]["passing"],
        "quality_passing": sum(row["quality_pass"] for row in judged),
        "quality_pass_rate": sum(row["quality_pass"] for row in judged) / len(judged) if judged else 0.0,
        "model_id": config.raw["generator"]["model_id"],
        "model_revision": config.raw["generator"]["revision"],
        "generator_repair_retries": sum(row["generator_repair_retries"] for row in generated),
        "judge_repair_retries": sum(row["judge_repair_retries"] for row in judged),
        "model_inference_seconds": total_seconds,
        "output_tokens": total_tokens,
        "aggregate_tokens_per_second": total_tokens / total_seconds if total_seconds else 0.0,
        "peak_vram_bytes": max((item.get("peak_vram_bytes", 0) for item in all_metrics), default=0),
        "peak_process_rss_kib": max((item.get("peak_process_rss_kib", 0) for item in all_metrics), default=0),
        "cpu_offload_used": any(item.get("cpu_offload", False) for item in all_metrics),
        "second_run_generation_appends": generation_second["appended_rows"],
        "second_run_judgment_appends": judgment_second["appended_rows"],
        "second_run_backend_loads": int(generation_second["backend_loaded"]) + int(judgment_second["backend_loaded"]),
        "stage_hashes_unchanged": unchanged,
        "hard_filter_second": hard_second["passing"],
        "dedup_second": dedup_second["passing"],
        "hard_gate_pass": generated_rows == 6
        and len(judged) == 6
        and result["hard_filter"]["passing"] == 6
        and generation_second["appended_rows"] == 0
        and judgment_second["appended_rows"] == 0
        and not generation_second["backend_loaded"]
        and not judgment_second["backend_loaded"]
        and unchanged,
    }
    write_json(config.artifact("reports/smoke.json"), report)
    if not report["hard_gate_pass"]:
        raise RuntimeError(f"smoke hard gate failed: {report}")
    return report
