from __future__ import annotations

import gc
import json
import resource
import time
from pathlib import Path
from typing import Any


def extract_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character == "{":
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("model output contains no parseable JSON object")


class FakeBackend:
    """Deterministic backend used to test orchestration, never formal data."""

    def __init__(self, role: str, *_: Any, **__: Any) -> None:
        self.role = role
        self.loaded_at = time.monotonic()

    def call(
        self, prompt: str, candidate: dict[str, Any], repair: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        del prompt, repair
        if self.role == "generator":
            evidence = []
            short_id = candidate["candidate_id"][:8]
            for context in candidate["contexts"]:
                quote = context["text"][: min(120, len(context["text"]))]
                evidence.append({"context_id": context["context_id"], "quote": quote})
            payload = {
                "question": f"How does the supplied passage frame its central claim for candidate {short_id}?",
                "reference_answer": f"The passage presents the central claim selected for record {short_id} "
                "directly in the cited wording. "
                + (
                    "The nearby passage supplies a second, related basis for this specific synthesis."
                    if len(evidence) == 2
                    else "That wording supplies the relevant basis for this specific answer."
                ),
                "evidence": evidence,
                "question_type": "interpretation",
            }
        else:
            payload = {
                "question_answerability": "answerable",
                "response_appropriate": True,
                "faithful": True,
                "evidence_supports_response": True,
                "self_contained": True,
                "overclaim": False,
                "contradiction": False,
                "answerability_score": 5,
                "faithfulness_score": 5,
                "evidence_score": 5,
                "self_containment_score": 5,
                "reason": "The response is fully supported by the supplied evidence.",
            }
        return payload, {"input_tokens": 1, "output_tokens": 1, "seconds": 0.001, "tokens_per_second": 1000.0}

    def close(self) -> None:
        return


class TransformersBackend:
    def __init__(self, role: str, config: dict[str, Any], offload_dir: Path) -> None:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

        self.role = role
        self.settings = config[role]
        self.seed = int(config["seed"])
        quant = config["quantization"]
        offload_dir.mkdir(parents=True, exist_ok=True)
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=quant["load_in_4bit"],
            bnb_4bit_quant_type=quant["quant_type"],
            bnb_4bit_use_double_quant=quant["double_quant"],
            bnb_4bit_compute_dtype=getattr(torch, quant["compute_dtype"]),
        )
        self.processor = AutoProcessor.from_pretrained(self.settings["model_id"], revision=self.settings["revision"])
        self.model = AutoModelForImageTextToText.from_pretrained(
            self.settings["model_id"],
            revision=self.settings["revision"],
            quantization_config=quantization_config,
            device_map=quant["device_map"],
            max_memory={0: quant["max_gpu_memory"], "cpu": quant["max_cpu_memory"]},
            offload_folder=str(offload_dir),
            dtype=getattr(torch, quant["compute_dtype"]),
            low_cpu_mem_usage=True,
        )
        self.model.eval()
        device_counts: dict[str, int] = {}
        for parameter in self.model.parameters():
            device = str(parameter.device)
            device_counts[device] = device_counts.get(device, 0) + parameter.numel()
        self.device_map = device_counts
        self.cpu_offload = "cpu" in self.device_map

    def call(
        self, prompt: str, candidate: dict[str, Any], repair: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        del repair
        import torch

        seed = self.seed ^ int(candidate["candidate_id"][:16], 16)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        rendered = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=False,
            enable_thinking=self.settings["thinking"],
        )
        inputs = self.processor(text=rendered, return_tensors="pt")
        target_device = next(
            parameter.device for parameter in self.model.parameters() if parameter.device.type != "meta"
        )
        inputs = {key: value.to(target_device) for key, value in inputs.items()}
        input_length = inputs["input_ids"].shape[-1]
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        started = time.monotonic()
        do_sample = self.settings["temperature"] > 0
        kwargs: dict[str, Any] = {
            "max_new_tokens": self.settings["max_new_tokens"],
            "do_sample": do_sample,
            "pad_token_id": self.processor.tokenizer.eos_token_id,
        }
        if do_sample:
            kwargs.update(temperature=self.settings["temperature"], top_p=self.settings["top_p"])
        with torch.inference_mode():
            output = self.model.generate(**inputs, **kwargs)
        elapsed = time.monotonic() - started
        generated = output[0, input_length:]
        text = self.processor.decode(generated, skip_special_tokens=True)
        output_tokens = int(generated.numel())
        metrics = {
            "input_tokens": int(input_length),
            "output_tokens": output_tokens,
            "seconds": elapsed,
            "tokens_per_second": output_tokens / elapsed if elapsed else 0.0,
            "peak_vram_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0,
            "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "cpu_offload": self.cpu_offload,
            "device_map": self.device_map,
        }
        return extract_json(text), metrics

    def close(self) -> None:
        import torch

        del self.model
        del self.processor
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()


def make_backend(role: str, config: dict[str, Any], offload_dir: Path, override: str | None = None) -> Any:
    backend_name = override or config[role]["backend"]
    if backend_name == "fake":
        return FakeBackend(role)
    if backend_name == "transformers":
        return TransformersBackend(role, config, offload_dir)
    raise ValueError(f"unsupported backend: {backend_name}")
