import importlib.util
import sys
from pathlib import Path

import pytest


def load_training_module():
    path = Path(__file__).parents[1] / "scripts" / "train_qlora.py"
    spec = importlib.util.spec_from_file_location("train_qlora", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeTokenizer:
    def apply_chat_template(self, messages, *, add_generation_prompt, **kwargs):
        assert kwargs == {"tokenize": True, "enable_thinking": False, "preserve_thinking": False}
        prompt = [10, 11, 12]
        if add_generation_prompt:
            return {"input_ids": prompt}
        assert messages[-1]["role"] == "assistant"
        return {"input_ids": prompt + [20, 21]}


def sample_row():
    return {
        "candidate_id": "sample-1",
        "contexts": [{"text": "A source passage."}],
        "question": "What does it say?",
        "answer": "An answer.",
    }


def test_completion_only_encoding_masks_prompt_tokens():
    training = load_training_module()
    encoded = training.encode_completion_only(FakeTokenizer(), sample_row(), max_length=8)
    assert encoded == {
        "input_ids": [10, 11, 12, 20, 21],
        "attention_mask": [1, 1, 1, 1, 1],
        "labels": [-100, -100, -100, 20, 21],
    }


def test_completion_only_encoding_rejects_silent_truncation():
    training = load_training_module()
    with pytest.raises(ValueError, match="silently truncating"):
        training.encode_completion_only(FakeTokenizer(), sample_row(), max_length=4)


def test_lora_targets_are_limited_to_language_model_scope():
    training = load_training_module()

    class FakeModel:
        def named_modules(self):
            names = [
                "model.language_model.layers.0.self_attn.q_proj",
                "model.language_model.layers.0.linear_attn.in_proj_qkv",
                "model.language_model.layers.0.mlp.down_proj",
                "model.visual.blocks.0.attn.q_proj",
                "model.language_model.lm_head",
            ]
            return [(name, object()) for name in names]

    targets, counts = training.find_lora_targets(
        FakeModel(),
        "model.language_model.layers.",
        ["q_proj", "in_proj_qkv", "down_proj"],
    )
    assert targets == [
        "model.language_model.layers.0.self_attn.q_proj",
        "model.language_model.layers.0.linear_attn.in_proj_qkv",
        "model.language_model.layers.0.mlp.down_proj",
    ]
    assert counts == {"q_proj": 1, "in_proj_qkv": 1, "down_proj": 1}


def test_latest_checkpoint_uses_highest_step(tmp_path):
    training = load_training_module()
    (tmp_path / "checkpoint-2").mkdir()
    (tmp_path / "checkpoint-10").mkdir()
    (tmp_path / "checkpoint-invalid").mkdir()
    assert training.latest_checkpoint(tmp_path) == tmp_path / "checkpoint-10"


def test_checkpoint_global_step_reads_saved_state(tmp_path):
    training = load_training_module()
    checkpoint = tmp_path / "checkpoint-7"
    checkpoint.mkdir()
    (checkpoint / "trainer_state.json").write_text('{"global_step": 7}')
    assert training.checkpoint_global_step(checkpoint) == 7


def test_training_loss_summary_uses_all_logged_steps_across_resume():
    training = load_training_module()
    history = [
        {"step": 1, "loss": 0.8, "grad_norm": 1.0},
        {"step": 2, "eval_loss": 0.7},
        {"step": 2, "loss": 0.4, "grad_norm": 0.9},
        {"step": 3, "loss": 0.2, "grad_norm": 0.8},
    ]
    summary = training.summarize_training_losses(history, window_size=2)
    assert summary == {
        "logged_steps": 3,
        "first_step": 1,
        "last_step": 3,
        "mean": pytest.approx(1.4 / 3),
        "minimum": 0.2,
        "maximum": 0.8,
        "window_size": 2,
        "first_window_mean": pytest.approx(0.6),
        "last_window_mean": pytest.approx(0.3),
    }


def test_validation_token_f1_is_transparent_and_bounded():
    path = Path(__file__).parents[1] / "scripts" / "evaluate_validation.py"
    spec = importlib.util.spec_from_file_location("evaluate_validation", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    assert module.token_f1("The symbolic order.", "symbolic order") == pytest.approx(0.8)
    assert module.token_f1("unrelated", "symbolic order") == 0.0
    assert module.token_f1("", "") == 1.0
