# LacanLLM Simplified Data Pipeline v3 — Decision Record

Status: implemented, smoke-tested, and revised for an explicitly self-judged initial production run on the isolated `v3` branch
Decision date: 2026-08-24

## Purpose

Pipeline v3 will expand the source-grounded QA dataset while removing the
category quotas, category-driven generation/refill, and separate Challenge
split that made Pipeline v2 expensive and difficult to reason about.

Pipeline v2 and all of its artifacts remain immutable historical evidence.
Pipeline v3 must use new configuration, artifact, manifest, and report paths.

## What “generate QA from the source” means

The input is one source paragraph, or two nearby paragraphs when a synthesis is
appropriate. The generator receives the source text and writes one complete
record containing:

- a question that can be understood on its own;
- an answer based only on the supplied source.

The generator must not use external facts to complete the answer. The source
paragraphs are original corpus material, not pre-existing QA pairs. Pipeline v2
currently uses the untuned `google/gemma-4-E2B-it` model for this generation.
The revised initial Pipeline v3 production run instead uses the pinned
instruction-tuned **Gemma 4 12B** model as its QA generator.

Unlike Pipeline v2, Pipeline v3 does not ask the generator to classify the
question or extract exact evidence spans. Context and source provenance are
owned by the pipeline and joined to the generated question and answer.

## Dataset topology and initial scale

Only three source-disjoint splits will be produced:

| Split | Initial target | Purpose |
|---|---:|---|
| Train | 2,000 | QLoRA supervised training |
| Validation | 250 | model and hyperparameter selection |
| Test | 250 | one-time final verification |

After the initial scale experiment, the total dataset may be expanded toward
approximately 5,000 examples while holding the training configuration fixed.
Dataset scale and training hyperparameters must not be changed in the same
comparison.

Train, Validation, and Test must be separated by source file before QA
generation. Test remains sealed until a model has been selected using
Validation. There is no separate Challenge split.

Unanswerable questions, ambiguity requiring clarification, concept confusion,
and difficult synthesis may occur within the ordinary splits when they form a
valid training example. They are metadata categories, not independently sized
or privileged datasets.

## Classification policy

Generation has no classification task. If diagnostic question types are useful
later, they must be derived in a separate downstream analysis and must never
affect acceptance, sampling, refill, ranking, or split size.

## Model roles

The revised initial production run assigns the pinned Gemma model to both roles:

| Role | Planned model | Responsibility |
|---|---|---|
| QA generator | Gemma 4 12B, instruction-tuned | Write only a grounded question and answer from source context |
| Semantic judge | Gemma 4 12B, instruction-tuned | Check the context, question, and answer for answerability, faithfulness, support, self-containment, overclaim, and contradiction |
| Fine-tuning base | Qwen3.5-9B or Gemma 4 12B | To be selected by a controlled Validation comparison rather than assumed in advance |

The canonical model repository IDs, supported quantization mode, context
format, and chat template must be verified in a model-loading preflight before
implementation writes them into the executable v3 configuration. The intended
model identities and sizes above are fixed by this decision; the preflight only
resolves their exact technical identifiers and compatibility settings.

Using different model families would reduce the risk that one model approves
artifacts caused by its own prompting or inductive biases. This initial run
accepts that limitation explicitly. The judge remains a filter and does not
rewrite failed answers in place.

### Gemma self-judge validation and production override

The first hardware and orchestration smoke test may use the exact ungated model
`google/gemma-4-12B-it` at revision
`707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7` for both generation and judging.
This exception exists only to test 4-bit NF4 loading, CPU offload, structured
output, lifecycle unload/reload, and resumability on the
available 12 GB GPU. Thinking is disabled for both roles. Smoke artifacts live
under a dedicated smoke path and can never enter a formal dataset.

On 2026-08-25 the initial production decision was revised: the exact pinned
Gemma model may perform both roles when the production configuration contains
the explicit `allow_self_judge` override. This preserves an auditable record of
the reduced reviewer independence instead of silently weakening validation.
The resulting dataset must be described as self-judged, not cross-model
reviewed; a later independent audit remains recommended before external use.

## Future fine-tuning base selection

The fine-tuning base is no longer permanently fixed to Gemma 4 E2B. At minimum,
the project should compare Qwen3.5-9B and Gemma 4 12B as candidate bases, subject
to hardware feasibility.

The comparison must:

- use the same accepted Train data and the same Validation data;
- use matched QLoRA search budgets and generation settings where architecture
  differences permit;
- report quality, contradiction/overclaim rates, inference speed, and VRAM;
- select the base using Validation only;
- keep sealed Test closed until the base, adapter, and inference settings are
  all locked.

If Gemma 4 12B cannot be trained under the available hardware budget, that is a
reported feasibility result rather than permission to give it a smaller or
otherwise incomparable experiment budget. A smaller Gemma variant may be run
as a separately named experiment, not silently substituted for Gemma 4 12B.

## Simplified production flow

1. Conservatively clean the original corpus without rewriting its claims.
2. Assign source files to Train, Validation, and Test with no source overlap.
3. Sample eligible source context independently of question category.
4. Generate only a question and answer using that context; the pipeline joins
   them back to the complete context and source provenance.
5. Apply deterministic validity checks: exact two-field schema, usable lengths,
   and no malformed or meta output. Invalid rows are rejected without retry and
   do not stop the batch.
6. Remove exact and near-duplicate questions, answers, and contexts globally.
7. Use Gemma 4 12B for semantic quality review of answerability, faithfulness,
   context support, self-containment where appropriate, overclaim, and
   contradiction.
8. Retain every quality-passing item up to the split-size target, independently
   of classification. Source diversity may be used as a tie-breaker, not as a
   rigid category topology.
9. Write provenance manifests and audit Train and Validation. Seal Test with a
   content hash and open it only for the final selected model.

## Quality policy

“Classification does not filter” does not mean “quality does not filter.” The
following protections remain essential:

- every ordinary answer is grounded in its supplied source;
- every question and answer retains its complete source context and provenance;
- unsupported claims, contradictions, and material overclaims are rejected;
- malformed output and long verbatim copying are rejected;
- exact and near duplicates are removed;
- split provenance and source separation are auditable;
- Test is source-disjoint, sealed, and used only for final verification.

The exact number of Gemma 4 12B semantic-judge passes and their thresholds are
not fixed by this decision record. They should be chosen with a small
calibration set so the new pipeline reduces cost without silently accepting
low-quality examples. A single reliable semantic pass is the preferred
starting point; a second pass should be added only if calibration demonstrates
a material benefit.

## Removed Pipeline v2 mechanisms

Pipeline v3 removes:

- the separate Challenge split;
- fixed question-type quotas;
- category-conditioned source routing;
- category-driven candidate refill;
- category-based final selection;
- rejection caused only by canonical-type disagreement;
- rigid source caps or floors that exist only to satisfy category quotas.

## Acceptance criteria for implementation

The implementation is complete only when:

- it writes to versioned v3 paths and leaves v2 untouched;
- its only splits are Train, Validation, and sealed Test;
- generated records preserve source provenance and complete context;
- Gemma 4 12B is recorded as both generator and semantic judge in every
  generated or judged row, and the audit identifies the run as self-judged;
- changing a classification label cannot change acceptance or selection;
- source intersections between the three splits are empty;
- duplicate checks operate across all three splits;
- target counts and Test seal are independently audited;
- tests demonstrate that categories are metadata-only.
