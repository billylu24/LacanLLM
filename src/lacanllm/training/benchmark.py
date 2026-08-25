"""Controlled native-PEFT versus Unsloth backend benchmark."""

from __future__ import annotations

import json
import os
import time
from types import MethodType
from typing import Any

from lacanllm.training.config import ExperimentConfig, TrialParams, atomic_json
from lacanllm.training.data import multimodal_messages, read_rows
from lacanllm.training.runtime import environment_snapshot, release_cuda, train_unsloth_trial


def benchmark_params(config: ExperimentConfig) -> TrialParams:
    value = config.raw["benchmark"]
    return TrialParams(
        learning_rate=float(value["learning_rate"]),
        rank=int(value["rank"]),
        alpha_ratio=int(value["alpha"]) // int(value["rank"]),
        dropout=0.0,
        effective_batch_size=int(value["effective_batch_size"]),
        warmup_ratio=0.0,
        scheduler="linear",
        weight_decay=0.0,
    )


def run_benchmark(config: ExperimentConfig, *, backend: str) -> dict[str, Any]:
    value = config.raw["benchmark"]
    steps = int(value["warmup_micro_steps"]) + int(value["measure_micro_steps"])
    if backend == "unsloth":
        metadata = train_unsloth_trial(
            config,
            900,
            benchmark_params(config),
            max_steps=steps,
            row_limit=int(value["rows"]),
            benchmark_mode=True,
        )
        report = _from_training_metadata(metadata, backend="unsloth", config=config)
    elif backend == "native":
        report = _native_benchmark(config, steps=steps, row_limit=int(value["rows"]))
    else:
        raise ValueError(f"Unknown benchmark backend: {backend}")
    output = config.artifact_root / "benchmarks" / f"{backend}.json"
    atomic_json(output, report)
    return combine_reports(config)


def _from_training_metadata(metadata: dict[str, Any], *, backend: str, config: ExperimentConfig) -> dict[str, Any]:
    metrics = metadata["train_metrics"]
    step_rate = float(metrics.get("train_steps_per_second", 0.0))
    effective_batch = int(config.raw["benchmark"]["effective_batch_size"])
    mean_tokens = _mean_tokens(config, int(config.raw["benchmark"]["rows"]))
    return {
        "backend": backend,
        "valid": True,
        "environment": metadata["environment"],
        "elapsed_seconds": metadata["elapsed_seconds"],
        "train_runtime_seconds": metrics.get("train_runtime"),
        "optimizer_steps_per_second": step_rate,
        "estimated_tokens_per_second": step_rate * effective_batch * mean_tokens,
        "peak_allocated_bytes": metadata["peak_allocated_bytes"],
        "peak_reserved_bytes": metadata["peak_reserved_bytes"],
        "train_loss": metrics.get("train_loss"),
    }


def _native_benchmark(config: ExperimentConfig, *, steps: int, row_limit: int) -> dict[str, Any]:
    import torch
    from datasets import Dataset
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForMultimodalLM,
        AutoProcessor,
        BitsAndBytesConfig,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for backend benchmarking")
    params = benchmark_params(config)
    fixed = config.raw["fixed_training"]
    token = os.environ.get("HF_TOKEN") or None
    started = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    processor = AutoProcessor.from_pretrained(str(config.raw["model_id"]), token=token)
    tokenizer = processor.tokenizer
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForMultimodalLM.from_pretrained(
        str(config.raw["model_id"]),
        token=token,
        device_map="auto",
        quantization_config=quantization,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    parity = _logit_parity(model, processor)
    if not parity["valid"]:
        report = {
            "backend": "native",
            "valid": False,
            "reason": "Gemma 4 use_cache parity gate failed",
            "logit_parity": parity,
            "environment": environment_snapshot(),
            "elapsed_seconds": time.monotonic() - started,
        }
        release_cuda(model, processor)
        return report
    _enable_training_shared_kv(model)
    model.config.use_cache = False
    _prepare_native_kbit_training(model)
    suffixes = tuple(f".{name}" for name in fixed["target_modules"])
    targets = [
        name
        for name, _ in model.named_modules()
        if name.startswith("model.language_model.layers.") and name.endswith(suffixes)
    ]
    if not targets:
        raise RuntimeError("Could not locate Gemma 4 language LoRA targets")
    model = get_peft_model(
        model,
        LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=params.rank,
            lora_alpha=params.alpha,
            lora_dropout=0.0,
            bias="none",
            target_modules=targets,
        ),
    )
    rows = read_rows(config.path("train"))[:row_limit]
    dataset = Dataset.from_list([_tokenize_native(row, processor, int(fixed["max_seq_length"])) for row in rows])
    output_dir = config.artifact_root / "benchmarks" / "native-checkpoints"
    trainer = Trainer(
        model=model,
        train_dataset=dataset,
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=-100),
        args=TrainingArguments(
            output_dir=str(output_dir),
            per_device_train_batch_size=1,
            gradient_accumulation_steps=params.effective_batch_size,
            max_steps=steps,
            learning_rate=params.learning_rate,
            warmup_ratio=0.0,
            weight_decay=0.0,
            lr_scheduler_type="linear",
            optim="paged_adamw_8bit",
            gradient_checkpointing=False,
            max_grad_norm=1.0,
            logging_steps=1,
            save_strategy="no",
            eval_strategy="no",
            bf16=True,
            report_to="none",
            seed=int(config.raw["seed"]),
            data_seed=int(config.raw["seed"]),
        ),
    )
    # Gemma 4 exposes **kwargs but does not implement Transformers' optional
    # num_items_in_batch loss contract. Passing it inflates response-only loss.
    trainer.model_accepts_loss_kwargs = False
    result = trainer.train()
    elapsed = time.monotonic() - started
    rate = float(result.metrics.get("train_steps_per_second", 0.0))
    report = {
        "backend": "native",
        "valid": True,
        "environment": environment_snapshot(),
        "logit_parity": parity,
        "elapsed_seconds": elapsed,
        "train_runtime_seconds": result.metrics.get("train_runtime"),
        "optimizer_steps_per_second": rate,
        "estimated_tokens_per_second": rate * params.effective_batch_size * _mean_tokens(config, row_limit),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "train_loss": result.metrics.get("train_loss"),
    }
    release_cuda(trainer, model, processor)
    return report


def _input_ids(value: Any) -> list[int]:
    if isinstance(value, dict):
        value = value["input_ids"]
    if value and isinstance(value[0], list):
        value = value[0]
    return list(value)


def _tokenize_native(row: dict[str, Any], processor: Any, max_length: int) -> dict[str, list[int]]:
    full = _input_ids(
        processor.apply_chat_template(
            multimodal_messages(row["messages"]),
            tokenize=True,
            add_generation_prompt=False,
            enable_thinking=False,
        )
    )[:max_length]
    prompt = _input_ids(
        processor.apply_chat_template(
            multimodal_messages(row["messages"][:1]),
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    )
    if len(prompt) >= len(full):
        raise RuntimeError(f"Response-only mask is empty for row {row['id']}")
    return {
        "input_ids": full,
        "attention_mask": [1] * len(full),
        "labels": [-100] * len(prompt) + full[len(prompt) :],
    }


def _logit_parity(model: Any, processor: Any) -> dict[str, Any]:
    import torch

    inputs = processor.apply_chat_template(
        multimodal_messages([{"role": "user", "content": "What is the symbolic order?"}]),
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
        enable_thinking=False,
    ).to(model.device)
    with torch.inference_mode():
        cached = model(**inputs, use_cache=True).logits[:, -1].float()
        training = model(
            **inputs,
            past_key_values=_training_shared_kv_cache(model.config),
            use_cache=False,
        ).logits[:, -1].float()
    difference = float((cached - training).abs().max().item())
    same_top_token = int(cached.argmax(-1).item()) == int(training.argmax(-1).item())
    return {
        "max_abs_difference": difference,
        "same_top_token": same_top_token,
        "valid": same_top_token and difference <= 1e-3,
    }


def _training_shared_kv_cache(config: Any) -> Any:
    """Keep Gemma 4 shared K/V within one forward without retaining batch history."""
    from transformers import DynamicCache

    class TrainingSharedKVCache(DynamicCache):
        def update(
            self,
            key_states: Any,
            value_states: Any,
            layer_idx: int,
            cache_kwargs: dict[str, Any] | None = None,
        ) -> tuple[Any, Any]:
            return key_states, value_states

    return TrainingSharedKVCache(config=config)


def _enable_training_shared_kv(model: Any) -> None:
    """Inject a fresh forward-scoped KV store for native Gemma 4 training."""
    text_model = model.model.language_model
    original_forward = text_model.forward

    def forward_with_shared_kv(
        self: Any,
        *args: Any,
        past_key_values: Any = None,
        use_cache: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        if past_key_values is None:
            past_key_values = _training_shared_kv_cache(self.config)
        return original_forward(
            *args,
            past_key_values=past_key_values,
            use_cache=False,
            **kwargs,
        )

    text_model.forward = MethodType(forward_with_shared_kv, text_model)


def _prepare_native_kbit_training(model: Any) -> None:
    """Freeze the quantized base without PEFT's memory-heavy BF16-to-FP32 cast."""
    for parameter in model.parameters():
        parameter.requires_grad = False
    _enable_shared_kv_gradient_checkpointing(model)


def _enable_shared_kv_gradient_checkpointing(model: Any) -> None:
    """Checkpoint decoder forwards without Transformers discarding shared K/V."""
    from torch.utils.checkpoint import checkpoint

    for layer in model.model.language_model.layers:
        original_forward = layer.forward

        def checkpointed_forward(
            *args: Any,
            _layer: Any = layer,
            _original_forward: Any = original_forward,
            **kwargs: Any,
        ) -> Any:
            if not _layer.training:
                return _original_forward(*args, **kwargs)

            def run(*active_args: Any) -> Any:
                return _original_forward(*active_args, **kwargs)

            return checkpoint(run, *args, use_reentrant=False)

        layer.forward = checkpointed_forward


def _mean_tokens(config: ExperimentConfig, row_limit: int) -> float:
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(str(config.raw["model_id"]), local_files_only=True)
    lengths = [
        len(
            _input_ids(
                processor.apply_chat_template(
                    multimodal_messages(row["messages"]),
                    tokenize=True,
                    add_generation_prompt=False,
                    enable_thinking=False,
                )
            )
        )
        for row in read_rows(config.path("train"))[:row_limit]
    ]
    return sum(lengths) / len(lengths)


def combine_reports(config: ExperimentConfig) -> dict[str, Any]:
    root = config.artifact_root / "benchmarks"
    reports = {}
    for backend in ("native", "unsloth"):
        path = root / f"{backend}.json"
        if path.is_file():
            reports[backend] = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"reports": reports, "comparison": None}
    native, unsloth = reports.get("native"), reports.get("unsloth")
    if native and unsloth and native.get("valid") and unsloth.get("valid"):
        result["comparison"] = {
            "speedup": unsloth["estimated_tokens_per_second"] / native["estimated_tokens_per_second"],
            "training_time_reduction": 1 - unsloth["train_runtime_seconds"] / native["train_runtime_seconds"],
            "allocated_vram_reduction": 1 - unsloth["peak_allocated_bytes"] / native["peak_allocated_bytes"],
            "reserved_vram_reduction": 1 - unsloth["peak_reserved_bytes"] / native["peak_reserved_bytes"],
        }
    atomic_json(config.artifact_root / "backend_benchmark.json", result)
    return result
