"""GPU runtimes for Unsloth training, generation, and blind judging."""

from __future__ import annotations

import gc
import json
import math
import os
import platform
import time
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from lacanllm.training.config import ExperimentConfig, TrialParams, atomic_json, canonical_hash
from lacanllm.training.data import question_prompt, read_rows, render_rows
from lacanllm.training.evaluation import (
    judge_prompt,
    normalize_score_floor,
    normalize_swapped,
    parse_json_object,
    validate_judgment,
)


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def environment_snapshot() -> dict[str, Any]:
    import torch

    packages = ["torch", "transformers", "bitsandbytes", "unsloth", "trl", "peft", "datasets", "optuna"]
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {name: package_version(name) for name in packages},
        "cuda_available": torch.cuda.is_available(),
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_capability": torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None,
    }


def release_cuda(*objects: Any) -> None:
    for value in objects:
        del value
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except (ImportError, RuntimeError):
        pass


class _TrialCallback:
    def __init__(self, trial: Any | None):
        self.trial = trial

    def on_evaluate(self, args: Any, state: Any, control: Any, metrics: dict[str, Any] | None = None, **_: Any) -> Any:
        if self.trial is None or not metrics or "eval_loss" not in metrics:
            return control
        epoch = max(1, round(float(state.epoch or 0)))
        self.trial.report(float(metrics["eval_loss"]), step=epoch)
        is_anchor = bool(getattr(self.trial, "system_attrs", {}).get("fixed_params"))
        if not is_anchor and self.trial.should_prune():
            import optuna

            raise optuna.TrialPruned(f"Hyperband pruned at epoch {epoch}")
        return control


def _trainer_callback(trial: Any | None) -> Any:
    from transformers import TrainerCallback

    class TrialCallback(_TrialCallback, TrainerCallback):
        def __init__(self) -> None:
            _TrialCallback.__init__(self, trial)

    return TrialCallback()


def train_unsloth_trial(
    config: ExperimentConfig,
    number: int,
    params: TrialParams,
    *,
    trial: Any | None = None,
    max_steps: int = -1,
    row_limit: int | None = None,
    benchmark_mode: bool = False,
) -> dict[str, Any]:
    # Unsloth must patch Transformers/TRL before either package is imported.
    from unsloth import FastModel  # noqa: I001 - patch order is required by Unsloth
    from unsloth.chat_templates import get_chat_template, train_on_responses_only

    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for Unsloth QLoRA training")
    config.assert_in_space(params)
    trial_dir = config.trial_dir(number)
    adapter_dir = trial_dir / "adapter"
    metadata_path = trial_dir / "training_metadata.json"
    execution_contract = {
        "max_steps": max_steps,
        "row_limit": row_limit,
        "benchmark_mode": benchmark_mode,
    }
    run_hash = (
        config.run_hash(params)
        if execution_contract == {"max_steps": -1, "row_limit": None, "benchmark_mode": False}
        else canonical_hash({"training": config.run_contract(params), "execution": execution_contract})
    )
    if metadata_path.is_file():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        if existing.get("run_hash") != run_hash:
            raise RuntimeError(f"Refusing to reuse trial {number} with a different run contract")
        if existing.get("status") == "completed" and (adapter_dir / "adapter_model.safetensors").is_file():
            _repair_eval_loss(existing, trial_dir)
            atomic_json(metadata_path, existing)
            return existing

    rows = read_rows(config.path("train"))
    validation_rows = read_rows(config.path("validation"))
    if row_limit is not None:
        rows = rows[:row_limit]
        validation_rows = validation_rows[: max(2, min(row_limit, len(validation_rows)))]
    fixed = config.raw["fixed_training"]
    token = os.environ.get("HF_TOKEN") or None
    started = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    model, processor = FastModel.from_pretrained(
        model_name=config.raw["model_id"],
        token=token,
        dtype=None,
        max_seq_length=int(fixed["max_seq_length"]),
        load_in_4bit=True,
        full_finetuning=False,
    )
    processor = get_chat_template(processor, chat_template="gemma-4")
    model = FastModel.get_peft_model(
        model,
        finetune_vision_layers=False,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=params.rank,
        lora_alpha=params.alpha,
        lora_dropout=params.dropout,
        bias="none",
        random_state=int(config.raw["seed"]),
        use_gradient_checkpointing="unsloth",
    )
    train_dataset = Dataset.from_list(render_rows(rows, processor, thinking=False))
    eval_dataset = None
    if not benchmark_mode:
        eval_dataset = Dataset.from_list(render_rows(validation_rows, processor, thinking=False))
    trial_dir.mkdir(parents=True, exist_ok=True)
    trainer = SFTTrainer(
        model=model,
        processing_class=getattr(processor, "tokenizer", processor),
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        args=SFTConfig(
            output_dir=str(trial_dir / "checkpoints"),
            dataset_text_field="text",
            max_length=int(fixed["max_seq_length"]),
            per_device_train_batch_size=int(fixed["per_device_train_batch_size"]),
            per_device_eval_batch_size=int(fixed["per_device_eval_batch_size"]),
            gradient_accumulation_steps=params.effective_batch_size,
            num_train_epochs=float(fixed["max_epochs"]),
            max_steps=max_steps,
            learning_rate=params.learning_rate,
            warmup_ratio=params.warmup_ratio,
            weight_decay=params.weight_decay,
            lr_scheduler_type=params.scheduler,
            optim=str(fixed["optimizer"]),
            max_grad_norm=float(fixed["max_grad_norm"]),
            eval_strategy="no" if benchmark_mode else "epoch",
            save_strategy="no" if benchmark_mode else "epoch",
            save_total_limit=2,
            load_best_model_at_end=not benchmark_mode,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            logging_steps=1,
            seed=int(config.raw["seed"]),
            data_seed=int(config.raw["seed"]),
            report_to="none",
            run_name=f"{config.raw['experiment_version']}-trial-{number:03d}",
        ),
        callbacks=[_trainer_callback(trial)],
    )
    trainer = train_on_responses_only(
        trainer,
        instruction_part="<|turn>user\n",
        response_part="<|turn>model\n",
    )
    checkpoint = None if benchmark_mode else _latest_checkpoint(trial_dir / "checkpoints")
    train_result = trainer.train(resume_from_checkpoint=checkpoint)
    eval_metrics = {} if benchmark_mode else trainer.evaluate()
    eval_loss = eval_metrics.get("eval_loss")
    if not isinstance(eval_loss, int | float) or not math.isfinite(float(eval_loss)):
        # Some Unsloth/Gemma 4 builds return NaN on a redundant post-training
        # evaluation even though the epoch evaluation and best checkpoint are
        # valid. Preserve the checkpoint-selection metric in the run record.
        best_metric = trainer.state.best_metric
        if isinstance(best_metric, int | float) and math.isfinite(float(best_metric)):
            eval_metrics["eval_loss"] = float(best_metric)
    adapter_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(adapter_dir))
    processor.save_pretrained(adapter_dir)
    elapsed = time.monotonic() - started
    metadata_value = {
        "status": "completed",
        "trial": number,
        "run_hash": run_hash,
        "contract": config.run_contract(params),
        "execution_contract": execution_contract,
        "environment": environment_snapshot(),
        "train_rows": len(rows),
        "validation_rows": len(validation_rows),
        "train_metrics": train_result.metrics,
        "eval_metrics": eval_metrics,
        "best_checkpoint": trainer.state.best_model_checkpoint if not benchmark_mode else None,
        "elapsed_seconds": elapsed,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "optimized_dropout_path": params.dropout == 0.0,
        "adapter_dir": str(adapter_dir),
    }
    atomic_json(metadata_path, metadata_value)
    del trainer, model, processor
    release_cuda()
    return metadata_value


def _repair_eval_loss(metadata_value: dict[str, Any], trial_dir: Path) -> None:
    metrics = metadata_value.get("eval_metrics", {})
    loss = metrics.get("eval_loss")
    if isinstance(loss, int | float) and math.isfinite(float(loss)):
        return
    checkpoint = metadata_value.get("best_checkpoint")
    state_path = Path(checkpoint) / "trainer_state.json" if checkpoint else None
    if state_path is None or not state_path.is_file():
        checkpoints = sorted((trial_dir / "checkpoints").glob("checkpoint-*/trainer_state.json"))
        state_path = checkpoints[-1] if checkpoints else None
    if state_path is not None and state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        best_metric = state.get("best_metric")
        if isinstance(best_metric, int | float) and math.isfinite(float(best_metric)):
            metrics["eval_loss"] = float(best_metric)


def _latest_checkpoint(path: Path) -> str | None:
    checkpoints = [
        item
        for item in path.glob("checkpoint-*")
        if item.is_dir() and (item / "trainer_state.json").is_file()
    ]
    return str(max(checkpoints, key=lambda item: int(item.name.split("-")[-1]))) if checkpoints else None


@dataclass
class LocalGenerator:
    model_id: str
    adapter_dir: Path | None = None

    def __post_init__(self) -> None:
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig

        token = os.environ.get("HF_TOKEN") or None
        self.processor = AutoProcessor.from_pretrained(self.model_id, token=token)
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            # E4B exceeds the 12 GB card by a small margin. Keep overflow
            # modules in FP32 on CPU instead of silently changing judge model.
            llm_int8_enable_fp32_cpu_offload=True,
        )
        text_only_device_map: str | dict[str, int | str] = "auto"
        if "e4b" in self.model_id.lower():
            text_only_device_map = {
                "model.language_model": 0,
                "lm_head": 0,
                "model.vision_tower": "cpu",
                "model.embed_vision": "cpu",
                "model.audio_tower": "cpu",
                "model.embed_audio": "cpu",
            }
        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.model_id,
            token=token,
            device_map=text_only_device_map,
            quantization_config=quantization,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
        )
        if self.adapter_dir is not None:
            from peft import PeftModel

            self.model = PeftModel.from_pretrained(self.model, self.adapter_dir)
        self.model.eval()

    def generate(self, rows: list[dict[str, Any]], *, max_new_tokens: int) -> list[dict[str, Any]]:
        import torch

        results = []
        for row in rows:
            inputs = self.processor.apply_chat_template(
                question_prompt(row),
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
                add_generation_prompt=True,
                enable_thinking=False,
            ).to(self.model.device)
            with torch.inference_mode():
                output = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    use_cache=True,
                    pad_token_id=self.processor.tokenizer.eos_token_id,
                )
            prediction = self.processor.decode(
                output[0, inputs["input_ids"].shape[-1] :],
                skip_special_tokens=True,
            ).strip()
            results.append({"id": row["id"], "question_type": row["question_type"], "prediction": prediction})
        return results

    def close(self) -> None:
        model, processor = self.model, self.processor
        self.model = None
        self.processor = None
        del model, processor
        release_cuda()


@dataclass
class PairJudge:
    model_id: str

    def __post_init__(self) -> None:
        self.backend = LocalGenerator(self.model_id)

    def evaluate(
        self,
        rows: list[dict[str, Any]],
        candidate: dict[str, str],
        baseline: dict[str, str],
        *,
        output_path: Path,
        max_new_tokens: int,
    ) -> list[dict[str, Any]]:
        existing = {}
        if output_path.is_file():
            existing = {str(row["id"]): row for row in read_rows(output_path)}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                row_id = str(row["id"])
                if row_id in existing:
                    continue
                first = self._one(judge_prompt(row, candidate[row_id], baseline[row_id]), max_new_tokens)
                swapped = self._one(judge_prompt(row, baseline[row_id], candidate[row_id]), max_new_tokens)
                record = {
                    "id": row_id,
                    "question_type": row["question_type"],
                    "first": first,
                    "second": normalize_swapped(swapped),
                }
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                existing[row_id] = record
        return [existing[str(row["id"])] for row in rows]

    def _one(self, prompt: str, max_new_tokens: int) -> dict[str, Any]:
        active_prompt = prompt
        last_error: ValueError | None = None
        raw = ""
        for attempt in range(1, 4):
            row = {"id": "judge", "question": active_prompt, "question_type": "judge"}
            raw = self.backend.generate([row], max_new_tokens=max_new_tokens)[0]["prediction"]
            try:
                return validate_judgment(normalize_score_floor(parse_json_object(raw)))
            except (json.JSONDecodeError, ValueError) as error:
                last_error = error
                active_prompt = f"""{prompt}

Your previous response was rejected: {error}.
Return a corrected JSON object only. All scores must be integer literals from 1 through 5.
Do not use decimals, markdown, comments, or any text outside the JSON object.
Keep reason to one sentence of at most 40 words.
<attempt>{attempt + 1}</attempt>"""
        raise ValueError(f"Judge failed strict schema after three attempts: {last_error}; output={raw[:1000]!r}")

    def close(self) -> None:
        self.backend.close()
