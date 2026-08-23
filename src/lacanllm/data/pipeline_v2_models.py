"""Model prompts and resumable generation/judging for pipeline v2."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lacanllm.data.io import read_jsonl
from lacanllm.data.pipeline_v2_core import (
    CHALLENGE_TYPES,
    REGULAR_TYPES,
    PipelineConfig,
    _valid_judge_payload,
    build_generation_queue,
    clean_human_text,
    parse_json_payload,
    utc_now,
)

TYPE_GUIDANCE = {
    "definition": (
        "Ask only for the precise meaning or definition of one named Lacanian concept. "
        "Do not ask about a relationship, role, function, consequence, or historical circumstance."
    ),
    "explanation": "Ask how or why a named mechanism, relation, or claim operates.",
    "comparison": "Ask for a supported distinction or relationship between two explicitly named items.",
    "textual_interpretation": "Ask for interpretation of a specific named formulation without referring to a passage.",
    "cross_concept": "Require synthesis of two named concepts supported by both supplied contexts.",
    "clinical_application": "Ask a source-bounded clinical or analytic application without inventing a case history.",
    "other": (
        "Ask specifically about argumentative or methodological structure: for example, the role of an example, "
        "the status of a premise, or how a conclusion is positioned in the discourse. Do not ask for a definition, "
        "a how/why explanation, a comparison, interpretation of a named formulation, a cross-concept synthesis, "
        "or a clinical application."
    ),
}

CHALLENGE_GUIDANCE = {
    "unanswerable": (
        "Create a plausible scholarly question that the supplied context cannot answer. "
        "The reference answer must explicitly explain that the evidence is insufficient and must not invent an answer."
    ),
    "ambiguous": (
        "Create an intentionally underspecified question with a missing referent or required distinction. "
        "The reference answer must request the exact clarification needed."
    ),
    "concept_confusion": (
        "Create a question containing a plausible but false conflation of two concepts. "
        "The reference answer must identify and correct the false premise using the context."
    ),
    "cross_concept": (
        "Create a difficult but answerable question requiring both supplied contexts. "
        "The answer and two exact quotes must jointly support the synthesis."
    ),
}


def generation_prompt(task: dict[str, Any]) -> str:
    target_type = str(task["target_question_type"])
    split = str(task["split"])
    contexts = "\n\n".join(
        f"<context_{index}>{context['text']}</context_{index}>"
        for index, context in enumerate(task["contexts"], 1)
    )
    if split == "challenge":
        instruction = CHALLENGE_GUIDANCE[target_type]
        quote_rule = (
            "Return evidence_quotes as an empty JSON array."
            if target_type in {"unanswerable", "ambiguous"}
            else "Return two exact continuous quotes." if target_type == "cross_concept" else "Return one exact quote."
        )
    else:
        instruction = TYPE_GUIDANCE[target_type]
        quote_rule = (
            "Return two exact continuous quotes, one from each context."
            if target_type == "cross_concept"
            else "Return one exact continuous quote."
        )
    return f"""You are producing auditable supervised data for a scholarly assistant about Jacques Lacan.

Target split: {split}
Target question type: {target_type}
Task-specific instruction: {instruction}

Rules:
- Use only the supplied context. Do not introduce external facts.
- The question must be 35-280 characters and end with a question mark.
- Except for an ambiguity challenge, the question must stand alone and name every required concept or referent.
- The question and answer must not contain any of these phrases: according to, the text, this text, the passage,
  this passage, the source, the author, the speaker, this phenomenon.
- The answer must contain 2-5 complete sentences and at least 120 characters. A one-sentence answer is invalid.
- Do not copy a long source paragraph as the answer.
- Evidence quotes must be exact, continuous strings from the supplied contexts and each be 30-500 characters.
- {quote_rule}
- For an ordinary single-context task, evidence_quotes must contain exactly one item even when several claims are made.
- Return only one JSON object and no Markdown.

Before returning, silently verify all of the following: the question has no banned phrase; the answer has at least two
sentences and 120 characters; the evidence array has the exact required number of items; every quote is copied exactly.

Required JSON shape:
{{
  "question": "...",
  "answer": "...",
  "evidence_quotes": ["..."],
  "question_type": "{target_type}"
}}

{contexts}
"""


def judge_prompt(row: dict[str, Any], pass_name: str) -> str:
    contexts = "\n\n".join(
        f"<context_{index}>{context['text']}</context_{index}>"
        for index, context in enumerate(row["contexts"], 1)
    )
    challenge = str(row.get("challenge_category") or "none")
    other_type_rule = (
        "- For this candidate, canonical_question_type is other only when it asks about argumentative or "
        "methodological structure (such as the role of an example, premise, or conclusion) and does not fit "
        "definition, explanation, comparison, textual_interpretation, cross_concept, or clinical_application.\n"
        if row.get("generation_prompt_version") == "pipeline_v2.generate.other.1"
        else ""
    )
    if pass_name == "rubric":
        role = (
            "Apply the rubric independently. Check whether every material claim is licensed by the contexts, "
            "then score each dimension."
        )
    elif pass_name == "adversarial":
        role = (
            "Act as an adversarial verifier. Decompose the answer into minimal claims, search for hidden premises, "
            "concept substitutions, unsupported strengthening, and contradictions before scoring."
        )
    else:
        raise ValueError(f"Unknown judge pass: {pass_name}")
    return f"""You are the strict semantic judge for an automated Lacan QA dataset.

Judge pass: {pass_name}
Candidate split: {row['split']}
Requested type: {row['target_question_type']}
Challenge category: {challenge}

{role}

Interpretation rules:
- answerable: the question can be answered from the contexts. It must be false for a valid unanswerable challenge.
- faithful: the reference answer does not distort the contexts.
- evidence_supports_answer: the saved evidence genuinely supports the reference answer, not merely shared words.
- self_contained: the question is independently understandable. It must be false for a valid ambiguity challenge.
- overclaim: the answer states a stronger or broader conclusion than the contexts license.
- contradiction: any material answer claim conflicts with the contexts.
- challenge_valid: true only when the named challenge category exhibits its intended behavior; use true for a sound
  ordinary QA as well.
- Scores are integers 1-5. For unanswerable and ambiguity challenges, score answerability/evidence support according
  to whether the reference answer correctly diagnoses the insufficiency or ambiguity.
- canonical_question_type must be one of: {', '.join(REGULAR_TYPES if challenge == 'none' else CHALLENGE_TYPES)}.
{other_type_rule}

Return only strict JSON with this exact shape:
{{
  "answerable": true,
  "faithful": true,
  "evidence_supports_answer": true,
  "self_contained": true,
  "overclaim": false,
  "contradiction": false,
  "challenge_valid": true,
  "scores": {{
    "answerability": 1,
    "faithfulness": 1,
    "evidence_support": 1,
    "self_contained": 1
  }},
  "canonical_question_type": "{row['target_question_type']}",
  "unsupported_claims": [],
  "reason_codes": [],
  "reason": "..."
}}

<question>{row['question']}</question>
<answer>{row['answer']}</answer>
<evidence_quotes>{json.dumps(row['evidence_quotes'], ensure_ascii=False)}</evidence_quotes>
{contexts}
"""


class TransformersBackend:
    """Lazy local Gemma backend with 4-bit loading and CPU offload fallback."""

    def __init__(self, model_id: str, *, load_in_4bit: bool):
        # PyTorch's threaded CPU weight loader can access-violate in
        # torch_cpu.dll on Windows. One loading thread is stable, and model
        # inference remains GPU-backed. Explicit user settings still win.
        if not os.environ.get("OMP_NUM_THREADS"):
            os.environ["OMP_NUM_THREADS"] = "1"
        if not os.environ.get("MKL_NUM_THREADS"):
            os.environ["MKL_NUM_THREADS"] = "1"
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for the configured local model pipeline")
        token = os.environ.get("HF_TOKEN") or None
        quantization = (
            BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
            if load_in_4bit
            else None
        )
        self.processor = AutoProcessor.from_pretrained(model_id, token=token)
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        self.model = AutoModelForMultimodalLM.from_pretrained(
            model_id,
            token=token,
            device_map="auto",
            quantization_config=quantization,
            dtype=dtype,
            attn_implementation="sdpa",
        )
        self.model.eval()

    def __call__(
        self,
        prompts: list[str],
        *,
        max_new_tokens: int,
        temperature: float,
        top_p: float,
    ) -> list[str]:
        import torch

        conversations = [[{"role": "user", "content": prompt}] for prompt in prompts]
        inputs = self.processor.apply_chat_template(
            conversations,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            padding=True,
            add_generation_prompt=True,
            enable_thinking=False,
        ).to(self.model.device)
        generate_kwargs: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": temperature > 0,
            "pad_token_id": self.processor.tokenizer.eos_token_id,
        }
        if temperature > 0:
            generate_kwargs.update({"temperature": temperature, "top_p": top_p})
        with torch.inference_mode():
            output = self.model.generate(**inputs, **generate_kwargs)
        generated = output[:, inputs["input_ids"].shape[-1] :]
        return [value.strip() for value in self.processor.batch_decode(generated, skip_special_tokens=True)]


Backend = Callable[..., list[str]]


def _validate_existing_config(path: Path, config_hash: str) -> list[dict[str, Any]]:
    rows = [row for _, row in read_jsonl(path)] if path.is_file() else []
    if any(row.get("config_hash") != config_hash for row in rows):
        raise RuntimeError(f"Refusing to mix artifacts with a different config hash: {path}")
    return rows


def _parsed_generation(raw: str) -> tuple[dict[str, Any], str | None]:
    try:
        payload = parse_json_payload(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return {}, str(exc)
    question = clean_human_text(payload.get("question"))
    answer = clean_human_text(payload.get("answer"))
    quotes = payload.get("evidence_quotes")
    if not question or not answer or not isinstance(quotes, list):
        return {}, "Missing question, answer, or evidence_quotes array"
    return {
        "question": question,
        "answer": answer,
        "evidence_quotes": [clean_human_text(value) for value in quotes],
        "generated_question_type": clean_human_text(payload.get("question_type")),
    }, None


def run_generation(
    config: PipelineConfig,
    split: str,
    *,
    limit: int | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    queue_path = config.interim("queues", split)
    if not queue_path.is_file():
        build_generation_queue(config, split)
    queue = [row for _, row in read_jsonl(queue_path)]
    output_path = config.interim("generations", split)
    existing = _validate_existing_config(output_path, config.config_hash)
    completed = {str(row["candidate_id"]) for row in existing}
    pending = [row for row in queue if str(row["candidate_id"]) not in completed]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return {"split": split, "generated": 0, "already_completed": len(completed), "remaining": 0}
    settings = config.raw["generation"]
    active_backend = backend or TransformersBackend(
        str(settings["model_id"]),
        load_in_4bit=bool(settings["load_in_4bit"]),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generated_count = 0
    parse_errors = 0
    batch_size = int(settings["batch_size"])
    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            prompts = [generation_prompt(task) for task in batch]
            try:
                outputs = active_backend(
                    prompts,
                    max_new_tokens=int(settings["max_new_tokens"]),
                    temperature=float(settings["temperature"]),
                    top_p=float(settings["top_p"]),
                )
            except RuntimeError:
                if len(batch) == 1:
                    raise
                outputs = []
                for prompt in prompts:
                    outputs.extend(
                        active_backend(
                            [prompt],
                            max_new_tokens=int(settings["max_new_tokens"]),
                            temperature=float(settings["temperature"]),
                            top_p=float(settings["top_p"]),
                        )
                    )
            for task, raw in zip(batch, outputs, strict=True):
                parsed, error = _parsed_generation(raw)
                record = {
                    **task,
                    **parsed,
                    "raw_generation": raw,
                    "generation_status": "parsed" if error is None else "parse_error",
                    "generation_error": error,
                    "generator_model": settings["model_id"],
                "generator_prompt_version": task.get("generation_prompt_version", settings["prompt_version"]),
                    "generated_at": utc_now(),
                }
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                generated_count += 1
                parse_errors += int(error is not None)
    return {
        "split": split,
        "generated": generated_count,
        "parse_errors": parse_errors,
        "already_completed": len(completed),
        "remaining": len(queue) - len(completed) - generated_count,
    }


def _parse_judge_result(raw: str) -> tuple[dict[str, Any], str | None]:
    try:
        payload = parse_json_payload(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return {}, str(exc)
    if not _valid_judge_payload(payload):
        return {}, "Judge output failed strict schema validation"
    payload["unsupported_claims"] = [clean_human_text(value) for value in payload["unsupported_claims"]]
    payload["reason_codes"] = [clean_human_text(value) for value in payload["reason_codes"]]
    payload["reason"] = clean_human_text(payload["reason"])
    return payload, None


def run_judge(
    config: PipelineConfig,
    split: str,
    pass_name: str,
    *,
    limit: int | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    if pass_name not in {"rubric", "adversarial"}:
        raise ValueError("Judge pass must be rubric or adversarial")
    input_path = config.interim("deduplicated", split)
    if not input_path.is_file():
        raise FileNotFoundError(f"Run hard-filter and deduplicate first: {input_path}")
    source_rows = [row for _, row in read_jsonl(input_path)]
    output_path = config.interim(f"judge_{pass_name}", split)
    existing = _validate_existing_config(output_path, config.config_hash)
    completed = {str(row["candidate_id"]) for row in existing}
    pending = [row for row in source_rows if str(row["candidate_id"]) not in completed]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return {"split": split, "pass": pass_name, "judged": 0, "already_completed": len(completed)}
    settings = config.raw["judge"]
    active_backend = backend or TransformersBackend(
        str(settings["model_id"]),
        load_in_4bit=bool(settings["load_in_4bit"]),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    judged = 0
    failures = 0
    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in pending:
            prompt = judge_prompt(row, pass_name)
            raw_outputs: list[str] = []
            result: dict[str, Any] = {}
            error: str | None = None
            for _attempt in range(int(settings["parse_retries"]) + 1):
                raw = active_backend(
                    [prompt],
                    max_new_tokens=int(settings["max_new_tokens"]),
                    temperature=float(settings["temperature"]),
                    top_p=1.0,
                )[0]
                raw_outputs.append(raw)
                result, error = _parse_judge_result(raw)
                if error is None:
                    break
            record = {
                "candidate_id": row["candidate_id"],
                "split": split,
                "judge_pass": pass_name,
                "judge_status": "parsed" if error is None else "parse_error",
                "judge_result": result,
                "judge_error": error,
                "raw_judge_outputs": raw_outputs,
                "judge_model": settings["model_id"],
                "judge_prompt_version": (
                    f"pipeline_v2.judge.{pass_name}.other.1"
                    if row.get("generation_prompt_version") == "pipeline_v2.generate.other.1"
                    else settings[f"{pass_name}_prompt_version"]
                ),
                "judged_at": utc_now(),
                "pipeline_version": config.pipeline_version,
                "config_hash": config.config_hash,
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            judged += 1
            failures += int(error is not None)
    return {
        "split": split,
        "pass": pass_name,
        "judged": judged,
        "parse_failures": failures,
        "already_completed": len(completed),
        "remaining": len(source_rows) - len(completed) - judged,
    }
