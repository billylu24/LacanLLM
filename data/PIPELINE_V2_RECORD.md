# LacanLLM Pipeline v2 — Rules and Execution Record

This is the single canonical record for data-production rules and execution
events. The configuration at `configs/data/pipeline_v2.json` is the
machine-readable source of thresholds. Every CLI command appends its timestamp,
status, configuration hash, counts, hashes, and errors to the **Execution log**
at the end of this file.

## Dataset status and limitations

- Pipeline: `pipeline_v2`; corpus: `corpus_v2`; SFT: `sft_v3`; benchmark: `benchmark_v2`.
- The benchmark is an automated **silver benchmark**, not an expert-reviewed gold benchmark.
- `source_file` is the isolation unit. `source_work` remains `null` because the corpus contains anonymous filenames.
- Anonymous files may belong to the same underlying book or seminar; file-level isolation cannot eliminate that risk.
- Existing `evaluation_v1` and `sft_v2` artifacts are archived under `legacy/pipeline_v1_20260819/` and cannot enter v2.

## Fixed source split

- Validation: `lacan_text_009.txt`, `012`, `014`, `026`, `036`.
- Test: `lacan_text_001.txt`, `010`, `027`, `033`, `043`.
- Train: the remaining 35 anonymous source files.
- Challenge uses Test source files but remains globally deduplicated from Test.
- Train, Validation, and Test source intersections must be empty.

## Corpus cleaning rules

Cleaning is conservative and never rewrites spelling, terminology, word order,
or claims. It only applies Unicode NFKC, removes BOM, and normalizes whitespace.

A paragraph is rejected with every applicable reason when any rule fails:

- length outside 240–1,800 characters;
- English alphabetic character ratio below 60%;
- known mojibake marker;
- publisher metadata, ISBN, URL, or table-of-contents marker;
- the same 8-token n-gram occurs at least three times;
- normalized exact duplicate;
- missing `source_file` or `paragraph_index`.

Every accepted and rejected row preserves raw text, cleaned text, raw SHA-256,
cleaning operations, provenance, flags, corpus version, and configuration hash.

## Generation rules

- Generator: `google/gemma-4-E2B-it`, NF4 4-bit.
- Temperature 0.25; top-p 0.9; max 420 new tokens; batch size 2.
- Every queue row is assigned a target type before generation.
- Questions must stand alone except for the intentional ambiguity challenge.
- Answers contain 2–5 concise sentences, use only supplied evidence, and do not copy a full paragraph.
- Ordinary candidates return one exact quote; cross-concept candidates return one quote from each of two contexts.
- Unanswerable and ambiguity challenges return no quote and a reference answer that diagnoses the limitation.
- Cross-concept contexts come from the same anonymous file, within 10 paragraph positions, and total at most 3,000 characters.
- JSONL generation is flushed per candidate and resumes by `candidate_id`.

Initial candidates are 2.2 times the final quotas: Train 1,100;
Validation 550; Test 550; Challenge 220. Deficient types are refilled in
batches until quotas are met or maximum attempts (2,000/1,000/1,000/500) are
reached.

## Final quotas

| Question type | Train | Validation | Test |
|---|---:|---:|---:|
| definition | 100 | 50 | 50 |
| explanation | 90 | 45 | 45 |
| comparison | 80 | 40 | 40 |
| textual_interpretation | 80 | 40 | 40 |
| cross_concept | 60 | 30 | 30 |
| clinical_application | 50 | 25 | 25 |
| other | 40 | 20 | 20 |

Challenge contains 25 each of `unanswerable`, `ambiguous`,
`concept_confusion`, and difficult `cross_concept`.

Candidate source caps are Train 50, Validation/Test 130, Challenge 100.
Final caps are Train 25, Validation/Test 60, Challenge 30; every Validation and
Test source must contribute at least 40 final examples.

## Hard-filter rules

- Question length 35–280 characters and final `?`.
- Reject meta questions, generic source references, and vague referents except the intentional ambiguity challenge.
- Answer length 120–900 characters; reject placeholders, refusals, meta wording, and repeated 8-grams.
- Evidence quotes are 30–500 characters and map to a continuous normalized token span with stored character offsets.
- Required final quote count is one, two for cross-concept, and zero for unanswerable/ambiguous.
- If E2B returns multiple valid quotes for an ordinary item, hard filter v2 retains the raw array, selects the quote
  with the highest substantive-answer coverage, and records every discarded extra quote; it does not discard an
  otherwise valid candidate merely for supplying extra genuine evidence.
- Substantive answer-token overlap with source must be at least 0.35 for grounded candidates.
- Answers of at least 40 tokens that occur as a direct normalized source copy are rejected.
- Candidate ID, split, and configuration hash must match the current queue.

Lexical overlap is only a cheap grounding filter and is never treated as proof
of semantic correctness.

## Deduplication rules

Before judging, each split applies normalized SHA-256 exact deduplication for
questions, answers, and context, followed by SimHash64 near deduplication:

- question Hamming distance at most 14;
- answer Hamming distance at most 8.

After judging, global deduplication uses precedence
`Challenge > Test > Validation > Train`. Each removed row retains its cluster
ID, representative ID and split, reason, distance, and algorithm version.

## Semantic Judge rules

- Judge: `google/gemma-4-E4B-it`, NF4 4-bit, batch size 1, temperature 0.
- Pass 1 (`rubric`) independently scores the explicit rubric.
- Pass 2 (`adversarial`) decomposes claims and searches for hidden assumptions, concept substitution, overclaim, and contradiction.
- Each pass produces strict JSON, raw output, model/prompt version, timestamp, and configuration hash.
- A malformed output is retried once; another failure rejects the candidate.

For ordinary QA, both passes must agree on canonical type and independently set
`answerable`, `faithful`, `evidence_supports_answer`, and `self_contained` true;
`overclaim` and `contradiction` false; and every dimension score at least 4/5.
Any disagreement rejects the candidate.

Challenge gates additionally require both passes to validate the intended
failure mode: evidence insufficiency, ambiguity requiring clarification,
correction of concept confusion, or evidence-backed cross-concept reasoning.

Final quality score:

- faithfulness: 30%;
- evidence support: 25%;
- answerability: 20%;
- self-contained quality: 15%;
- lexical grounding: 10%.

Selection ranks within canonical type and uses source-aware round-robin under
the fixed type quotas and source caps.

## Final artifacts and audit gates

- Train: 500 rows in `sft_v3`.
- Validation: 250 silver rows.
- Test: 250 sealed silver rows.
- Challenge: 100 silver rows.
- Test receives a separate manifest containing its SHA-256 and `sealed=true`.

The final audit fails unless counts and type quotas are exact, Train/Validation/
Test sources are disjoint, exact questions and answers are globally unique,
provenance and quote offsets are valid, both Judge results are present, all
benchmark labels are silver, the Test seal matches, and the split manifest is valid.

## Execution log

CLI events are appended below. Do not manually edit generated event blocks.

### 2026-08-20T09:01:41.618558+00:00 — `clean` — completed

```json
{
  "command": "clean",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "16ea0dc731f9780bc9fdf3a95d35d2bb5f678d419cecb9a82536a58585dd1dc8",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:01:41.618558+00:00"
}
```

### 2026-08-20T09:01:42.143983+00:00 — `split` — completed

```json
{
  "command": "split",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "16ea0dc731f9780bc9fdf3a95d35d2bb5f678d419cecb9a82536a58585dd1dc8",
    "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
    "created_at": "2026-08-20T09:01:42.067268+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:01:42.143983+00:00"
}
```

### 2026-08-20T09:01:42.414886+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 0,
        "hard_filtered": 0,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 0,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 0,
        "hard_filtered": 0,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 0,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 0,
        "hard_filtered": 0,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 0,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 0,
        "hard_filtered": 0,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 0,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:01:42.414886+00:00"
}
```

### 2026-08-20T09:03:46.990006+00:00 — `generate --split train --limit 10` — completed

```json
{
  "command": "generate --split train --limit 10",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "already_completed": 0,
    "generated": 10,
    "parse_errors": 0,
    "remaining": 1090,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:03:46.990006+00:00"
}
```

### 2026-08-20T09:03:53.410043+00:00 — `hard-filter --split train` — completed

```json
{
  "command": "hard-filter --split train",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "accepted_rows": 3,
    "input_rows": 10,
    "rejected_rows": 7,
    "rejection_counts": {
      "answer_length": 2,
      "evidence_quote_count": 5,
      "generic_source_reference": 2
    },
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:03:53.410043+00:00"
}
```

### 2026-08-20T09:04:11.388863+00:00 — `deduplicate` — completed

```json
{
  "command": "deduplicate",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "train": {
      "duplicate_reasons": {},
      "duplicate_rows": 0,
      "input_rows": 3,
      "kept_rows": 3
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:04:11.388863+00:00"
}
```

### 2026-08-20T09:09:39.822655+00:00 — `judge --split train --pass rubric --limit 3` — completed

```json
{
  "command": "judge --split train --pass rubric --limit 3",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "already_completed": 0,
    "judged": 3,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:09:39.822655+00:00"
}
```

### 2026-08-20T09:11:10.051073+00:00 — `judge --split train --pass adversarial --limit 3` — completed

```json
{
  "command": "judge --split train --pass adversarial --limit 3",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "already_completed": 0,
    "judged": 3,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:11:10.051073+00:00"
}
```

### 2026-08-20T09:11:17.206169+00:00 — `select --allow-incomplete` — completed

```json
{
  "command": "select --allow-incomplete",
  "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
  "payload": {
    "consensus": {
      "train": {
        "accepted_rows": 1,
        "accepted_types": {
          "definition": 1
        },
        "input_rows": 3,
        "rejected_rows": 2,
        "rejection_counts": {
          "judge_evidence_not_supportive": 2,
          "judge_not_answerable": 2,
          "judge_not_faithful": 1,
          "judge_overclaim": 1,
          "judge_score_below_threshold": 2
        }
      }
    },
    "selection": {
      "challenge": {
        "deficits": {
          "ambiguous": 25,
          "concept_confusion": 25,
          "cross_concept": 25,
          "unanswerable": 25
        },
        "rows": 0,
        "sources": {},
        "target": 100,
        "types": {}
      },
      "global_duplicate_rows": 0,
      "test": {
        "deficits": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 30,
          "definition": 50,
          "explanation": 45,
          "other": 20,
          "source_floor": 5,
          "textual_interpretation": 40
        },
        "rows": 0,
        "sources": {},
        "target": 250,
        "types": {}
      },
      "test_seal": {
        "benchmark_grade": "silver",
        "config_hash": "7d415c1c9cdca7831e934eb113c3ff3dfedbd3a2772894359f11d614213d2ee0",
        "dataset_version": "benchmark_v2",
        "rows": 0,
        "sealed": false,
        "sealed_at": "2026-08-20T09:11:17.205172+00:00",
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
      },
      "train": {
        "deficits": {
          "clinical_application": 50,
          "comparison": 80,
          "cross_concept": 60,
          "definition": 99,
          "explanation": 90,
          "other": 40,
          "textual_interpretation": 80
        },
        "rows": 1,
        "sources": {
          "lacan_text_008.txt": 1
        },
        "target": 500,
        "types": {
          "definition": 1
        }
      },
      "validation": {
        "deficits": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 30,
          "definition": 50,
          "explanation": 45,
          "other": 20,
          "source_floor": 5,
          "textual_interpretation": 40
        },
        "rows": 0,
        "sources": {},
        "target": 250,
        "types": {}
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:11:17.206169+00:00"
}
```

### 2026-08-20T09:12:47.157237+00:00 — `clean` — completed

```json
{
  "command": "clean",
  "config_hash": "88defb2a8ea232bab582012804302aa77ccaf2f4a2974f0d5b68956cb6e70a2c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "d36dc602dffded294ea41453905d8c25ec4efbec4450aabbec807884783d4c54",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:12:47.157237+00:00"
}
```

### 2026-08-20T09:12:47.688762+00:00 — `split` — completed

```json
{
  "command": "split",
  "config_hash": "88defb2a8ea232bab582012804302aa77ccaf2f4a2974f0d5b68956cb6e70a2c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "d36dc602dffded294ea41453905d8c25ec4efbec4450aabbec807884783d4c54",
    "config_hash": "88defb2a8ea232bab582012804302aa77ccaf2f4a2974f0d5b68956cb6e70a2c",
    "created_at": "2026-08-20T09:12:47.602535+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:12:47.688762+00:00"
}
```

### 2026-08-20T09:15:15.387982+00:00 — `generate --split train --limit 10` — completed

```json
{
  "command": "generate --split train --limit 10",
  "config_hash": "88defb2a8ea232bab582012804302aa77ccaf2f4a2974f0d5b68956cb6e70a2c",
  "payload": {
    "already_completed": 0,
    "generated": 10,
    "parse_errors": 0,
    "remaining": 1090,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:15:15.387982+00:00"
}
```

### 2026-08-20T09:15:27.779308+00:00 — `hard-filter --split train` — completed

```json
{
  "command": "hard-filter --split train",
  "config_hash": "88defb2a8ea232bab582012804302aa77ccaf2f4a2974f0d5b68956cb6e70a2c",
  "payload": {
    "accepted_rows": 5,
    "input_rows": 10,
    "rejected_rows": 5,
    "rejection_counts": {
      "evidence_quote_count": 3,
      "low_answer_source_overlap": 2
    },
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:15:27.779308+00:00"
}
```

### 2026-08-20T09:15:56.513560+00:00 — `deduplicate` — completed

```json
{
  "command": "deduplicate",
  "config_hash": "88defb2a8ea232bab582012804302aa77ccaf2f4a2974f0d5b68956cb6e70a2c",
  "payload": {
    "train": {
      "duplicate_reasons": {},
      "duplicate_rows": 0,
      "input_rows": 5,
      "kept_rows": 5
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:15:56.513560+00:00"
}
```

### 2026-08-20T09:19:53.033255+00:00 — `smoke-judge-both train prompt-v2` — completed

```json
{
  "command": "smoke-judge-both train prompt-v2",
  "config_hash": "88defb2a8ea232bab582012804302aa77ccaf2f4a2974f0d5b68956cb6e70a2c",
  "payload": {
    "adversarial": {
      "already_completed": 0,
      "judged": 5,
      "parse_failures": 0,
      "pass": "adversarial",
      "remaining": 0,
      "split": "train"
    },
    "consensus": {
      "train": {
        "accepted_rows": 3,
        "accepted_types": {
          "definition": 3
        },
        "input_rows": 5,
        "rejected_rows": 2,
        "rejection_counts": {
          "judge_evidence_not_supportive": 2,
          "judge_not_answerable": 2,
          "judge_not_faithful": 2,
          "judge_overclaim": 1,
          "judge_score_below_threshold": 2
        }
      }
    },
    "rubric": {
      "already_completed": 0,
      "judged": 5,
      "parse_failures": 0,
      "pass": "rubric",
      "remaining": 0,
      "split": "train"
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:19:53.033255+00:00"
}
```

### 2026-08-20T09:22:22.670467+00:00 — `clean` — completed

```json
{
  "command": "clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:22:22.670467+00:00"
}
```

### 2026-08-20T09:22:23.271170+00:00 — `split` — completed

```json
{
  "command": "split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-20T09:22:23.192993+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:22:23.271170+00:00"
}
```

### 2026-08-20T09:24:14.477444+00:00 — `generate --split train --limit 10` — completed

```json
{
  "command": "generate --split train --limit 10",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "generated": 10,
    "parse_errors": 0,
    "remaining": 1090,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:24:14.477444+00:00"
}
```

### 2026-08-20T09:24:22.310302+00:00 — `hard-filter --split train` — completed

```json
{
  "command": "hard-filter --split train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 3,
    "input_rows": 10,
    "rejected_rows": 7,
    "rejection_counts": {
      "evidence_quote_count": 5,
      "low_answer_source_overlap": 2
    },
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:24:22.310302+00:00"
}
```

### 2026-08-20T09:25:00.092014+00:00 — `hard-filter --split train` — completed

```json
{
  "command": "hard-filter --split train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 8,
    "input_rows": 10,
    "rejected_rows": 2,
    "rejection_counts": {
      "low_answer_source_overlap": 2
    },
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:25:00.092014+00:00"
}
```

### 2026-08-20T09:26:12.641592+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:26:12.641592+00:00"
}
```

### 2026-08-20T09:26:12.641592+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-20T09:26:12.558245+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:26:12.641592+00:00"
}
```

### 2026-08-20T09:27:17.166851+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 0,
        "hard_filtered": 0,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 0,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 0,
        "hard_filtered": 0,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 0,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 12,
        "hard_filtered": 8,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 0,
        "final": 0,
        "generated": 0,
        "hard_filtered": 0,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 0,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T09:27:17.166851+00:00"
}
```

### 2026-08-20T12:21:03.445219+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 10,
    "generated": 1090,
    "parse_errors": 28,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T12:21:03.445219+00:00"
}
```

### 2026-08-20T13:45:35.162783+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "generated": 550,
    "parse_errors": 5,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T13:45:35.162783+00:00"
}
```

### 2026-08-20T15:10:57.532542+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "generated": 550,
    "parse_errors": 11,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T15:10:57.532542+00:00"
}
```

### 2026-08-20T15:47:27.761908+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "generated": 220,
    "parse_errors": 1,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T15:47:27.761908+00:00"
}
```

### 2026-08-20T15:47:31.742170+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 77,
        "kept_rows": 77
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 414,
        "kept_rows": 405
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 25
        },
        "duplicate_rows": 27,
        "input_rows": 838,
        "kept_rows": 811
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 415,
        "kept_rows": 408
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 77,
        "input_rows": 220,
        "rejected_rows": 143,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 114,
          "evidence_quote_length": 9,
          "evidence_quote_not_contiguous": 14,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 414,
        "input_rows": 550,
        "rejected_rows": 136,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 17,
          "evidence_quote_not_contiguous": 21,
          "generation_parse_error": 11,
          "generic_source_reference": 10,
          "low_answer_source_overlap": 83
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 838,
        "input_rows": 1100,
        "rejected_rows": 262,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "answer_length": 1,
          "evidence_quote_length": 47,
          "evidence_quote_not_contiguous": 32,
          "generation_parse_error": 28,
          "generic_source_reference": 18,
          "low_answer_source_overlap": 144,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 415,
        "input_rows": 550,
        "rejected_rows": 135,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 27,
          "evidence_quote_not_contiguous": 15,
          "generation_parse_error": 5,
          "generic_source_reference": 10,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 81,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T15:47:31.742170+00:00"
}
```

### 2026-08-20T16:55:16.852420+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 77,
        "final": 0,
        "generated": 220,
        "hard_filtered": 77,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 0,
        "judge_rubric": 194,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T16:55:16.852420+00:00"
}
```

### 2026-08-20T16:59:37.001180+00:00 — `hard-filter --split challenge` — completed

```json
{
  "command": "hard-filter --split challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 168,
    "input_rows": 220,
    "rejected_rows": 52,
    "rejection_counts": {
      "ambiguous_question_reference": 3,
      "evidence_quote_count": 5,
      "evidence_quote_length": 8,
      "evidence_quote_not_contiguous": 10,
      "generation_parse_error": 1,
      "generic_source_reference": 20,
      "low_answer_source_overlap": 13,
      "question_length": 1,
      "question_not_interrogative": 1
    },
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T16:59:37.001180+00:00"
}
```

### 2026-08-20T16:59:43.085619+00:00 — `deduplicate` — completed

```json
{
  "command": "deduplicate",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "challenge": {
      "duplicate_reasons": {},
      "duplicate_rows": 0,
      "input_rows": 168,
      "kept_rows": 168
    },
    "test": {
      "duplicate_reasons": {
        "exact_question": 1,
        "near_question": 8
      },
      "duplicate_rows": 9,
      "input_rows": 414,
      "kept_rows": 405
    },
    "train": {
      "duplicate_reasons": {
        "exact_question": 2,
        "near_question": 25
      },
      "duplicate_rows": 27,
      "input_rows": 838,
      "kept_rows": 811
    },
    "validation": {
      "duplicate_reasons": {
        "near_question": 7
      },
      "duplicate_rows": 7,
      "input_rows": 415,
      "kept_rows": 408
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T16:59:43.085619+00:00"
}
```

### 2026-08-20T16:59:56.1877661Z — `rule-correction:challenge-negative-evidence` — completed

```json
{
  "change": "pipeline_v2.hard_filter.2 -> pipeline_v2.hard_filter.3",
  "reason": "The prior hard filter required zero evidence quotes for unanswerable and ambiguous challenges, conflicting with the benchmark-wide continuous evidence-span contract and eliminating both categories.",
  "new_rule": "Every challenge keeps one continuous evidence-boundary quote, except cross_concept which keeps two. Extra quotes are deterministically reduced to the best-supported quote; when a compliant negative candidate has no quote, the closest continuous source sentence is derived and marked derived_evidence_quote=true.",
  "impact": {
    "challenge_hard_filtered_before": 77,
    "challenge_hard_filtered_after": 168,
    "challenge_deduplicated_after": 168,
    "category_counts_after": {
      "ambiguous": 43,
      "concept_confusion": 43,
      "cross_concept": 34,
      "unanswerable": 48
    }
  },
  "verification": {
    "pytest": "34 passed, 1 skipped",
    "ruff": "all checks passed"
  }
}
```

### 2026-08-20T17:00:57.070640+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:00:57.070640+00:00"
}
```

### 2026-08-20T17:00:57.071615+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-20T17:00:56.988979+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:00:57.071615+00:00"
}
```

### 2026-08-20T17:01:29.971427+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1100,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:01:29.971427+00:00"
}
```

### 2026-08-20T17:01:29.988449+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:01:29.988449+00:00"
}
```

### 2026-08-20T17:01:30.003959+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:01:30.003959+00:00"
}
```

### 2026-08-20T17:01:30.011064+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:01:30.011064+00:00"
}
```

### 2026-08-20T17:01:34.096159+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 414,
        "kept_rows": 405
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 25
        },
        "duplicate_rows": 27,
        "input_rows": 838,
        "kept_rows": 811
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 415,
        "kept_rows": 408
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 414,
        "input_rows": 550,
        "rejected_rows": 136,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 17,
          "evidence_quote_not_contiguous": 21,
          "generation_parse_error": 11,
          "generic_source_reference": 10,
          "low_answer_source_overlap": 83
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 838,
        "input_rows": 1100,
        "rejected_rows": 262,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "answer_length": 1,
          "evidence_quote_length": 47,
          "evidence_quote_not_contiguous": 32,
          "generation_parse_error": 28,
          "generic_source_reference": 18,
          "low_answer_source_overlap": 144,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 415,
        "input_rows": 550,
        "rejected_rows": 135,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 27,
          "evidence_quote_not_contiguous": 15,
          "generation_parse_error": 5,
          "generic_source_reference": 10,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 81,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:01:34.096159+00:00"
}
```

### 2026-08-20T17:02:45.160532+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 0,
        "judge_rubric": 200,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T17:02:45.160532+00:00"
}
```

### 2026-08-20T18:40:50.018004+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 0,
        "judge_rubric": 458,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T18:40:50.018004+00:00"
}
```

### 2026-08-20T22:04:07.758843+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:04:07.758843+00:00"
}
```

### 2026-08-20T22:04:07.759844+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-20T22:04:07.666742+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:04:07.759844+00:00"
}
```

### 2026-08-20T22:04:48.772224+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1100,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:04:48.772224+00:00"
}
```

### 2026-08-20T22:04:48.804765+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:04:48.804765+00:00"
}
```

### 2026-08-20T22:04:48.838895+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:04:48.838895+00:00"
}
```

### 2026-08-20T22:04:48.860418+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:04:48.860418+00:00"
}
```

### 2026-08-20T22:04:52.961560+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 414,
        "kept_rows": 405
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 25
        },
        "duplicate_rows": 27,
        "input_rows": 838,
        "kept_rows": 811
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 415,
        "kept_rows": 408
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 414,
        "input_rows": 550,
        "rejected_rows": 136,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 17,
          "evidence_quote_not_contiguous": 21,
          "generation_parse_error": 11,
          "generic_source_reference": 10,
          "low_answer_source_overlap": 83
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 838,
        "input_rows": 1100,
        "rejected_rows": 262,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "answer_length": 1,
          "evidence_quote_length": 47,
          "evidence_quote_not_contiguous": 32,
          "generation_parse_error": 28,
          "generic_source_reference": 18,
          "low_answer_source_overlap": 144,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 415,
        "input_rows": 550,
        "rejected_rows": 135,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 27,
          "evidence_quote_not_contiguous": 15,
          "generation_parse_error": 5,
          "generic_source_reference": 10,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 81,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:04:52.961560+00:00"
}
```

### 2026-08-20T22:05:59.310591+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 0,
        "judge_rubric": 459,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-20T22:05:59.310591+00:00"
}
```

### 2026-08-21T01:34:04.401648+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 0,
        "judge_rubric": 732,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:34:04.401648+00:00"
}
```

### 2026-08-21T01:34:46.367297+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:34:46.367297+00:00"
}
```

### 2026-08-21T01:34:46.368322+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-21T01:34:46.267436+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:34:46.368322+00:00"
}
```

### 2026-08-21T01:35:29.998521+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1100,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:35:29.998521+00:00"
}
```

### 2026-08-21T01:35:30.022602+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:35:30.022602+00:00"
}
```

### 2026-08-21T01:35:30.042625+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:35:30.042625+00:00"
}
```

### 2026-08-21T01:35:30.050643+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:35:30.050643+00:00"
}
```

### 2026-08-21T01:35:34.392299+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 414,
        "kept_rows": 405
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 25
        },
        "duplicate_rows": 27,
        "input_rows": 838,
        "kept_rows": 811
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 415,
        "kept_rows": 408
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 414,
        "input_rows": 550,
        "rejected_rows": 136,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 17,
          "evidence_quote_not_contiguous": 21,
          "generation_parse_error": 11,
          "generic_source_reference": 10,
          "low_answer_source_overlap": 83
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 838,
        "input_rows": 1100,
        "rejected_rows": 262,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "answer_length": 1,
          "evidence_quote_length": 47,
          "evidence_quote_not_contiguous": 32,
          "generation_parse_error": 28,
          "generic_source_reference": 18,
          "low_answer_source_overlap": 144,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 415,
        "input_rows": 550,
        "rejected_rows": 135,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 27,
          "evidence_quote_not_contiguous": 15,
          "generation_parse_error": 5,
          "generic_source_reference": 10,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 81,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:35:34.392299+00:00"
}
```

### 2026-08-21T01:36:12.645389+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 0,
        "judge_rubric": 732,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:36:12.645389+00:00"
}
```

### 2026-08-21T01:38:18.636564+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:38:18.636564+00:00"
}
```

### 2026-08-21T01:38:18.637563+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-21T01:38:18.524815+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:38:18.637563+00:00"
}
```

### 2026-08-21T01:38:18.750196+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1100,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:38:18.750196+00:00"
}
```

### 2026-08-21T01:38:18.768766+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:38:18.768766+00:00"
}
```

### 2026-08-21T01:38:18.786792+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:38:18.786792+00:00"
}
```

### 2026-08-21T01:38:18.794303+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:38:18.794303+00:00"
}
```

### 2026-08-21T01:38:23.123929+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 414,
        "kept_rows": 405
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 25
        },
        "duplicate_rows": 27,
        "input_rows": 838,
        "kept_rows": 811
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 415,
        "kept_rows": 408
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 414,
        "input_rows": 550,
        "rejected_rows": 136,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 17,
          "evidence_quote_not_contiguous": 21,
          "generation_parse_error": 11,
          "generic_source_reference": 10,
          "low_answer_source_overlap": 83
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 838,
        "input_rows": 1100,
        "rejected_rows": 262,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "answer_length": 1,
          "evidence_quote_length": 47,
          "evidence_quote_not_contiguous": 32,
          "generation_parse_error": 28,
          "generic_source_reference": 18,
          "low_answer_source_overlap": 144,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 415,
        "input_rows": 550,
        "rejected_rows": 135,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 27,
          "evidence_quote_not_contiguous": 15,
          "generation_parse_error": 5,
          "generic_source_reference": 10,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 81,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:38:23.123929+00:00"
}
```

### 2026-08-21T01:39:35.008497+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 0,
        "judge_rubric": 733,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T01:39:35.008497+00:00"
}
```

### 2026-08-21T02:10:45.223504+00:00 — `run:judge:train:rubric:round-1` — completed

```json
{
  "command": "run:judge:train:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 732,
    "judged": 79,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T02:10:45.223504+00:00"
}
```

### 2026-08-21T05:53:03.029248+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 603,
        "judge_rubric": 811,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T05:53:03.029248+00:00"
}
```

### 2026-08-21T06:50:48.215438+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 736,
        "judge_rubric": 811,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 0,
        "judge_rubric": 0,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T06:50:48.215438+00:00"
}
```

### 2026-08-21T07:21:42.989325+00:00 — `run:judge:train:adversarial:round-1` — completed

```json
{
  "command": "run:judge:train:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "judged": 811,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T07:21:42.989325+00:00"
}
```

### 2026-08-21T09:57:02.510212+00:00 — `run:judge:validation:rubric:round-1` — completed

```json
{
  "command": "run:judge:validation:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "judged": 408,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T09:57:02.510212+00:00"
}
```

### 2026-08-21T12:27:58.113667+00:00 — `run:judge:validation:adversarial:round-1` — completed

```json
{
  "command": "run:judge:validation:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "judged": 408,
    "parse_failures": 1,
    "pass": "adversarial",
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T12:27:58.113667+00:00"
}
```

### 2026-08-21T14:54:16.685115+00:00 — `run:judge:test:rubric:round-1` — completed

```json
{
  "command": "run:judge:test:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "judged": 405,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T14:54:16.685115+00:00"
}
```

### 2026-08-21T17:23:18.991767+00:00 — `run:judge:test:adversarial:round-1` — completed

```json
{
  "command": "run:judge:test:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "judged": 405,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T17:23:18.991767+00:00"
}
```

### 2026-08-21T18:21:07.277487+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 0,
        "deduplicated": 168,
        "final": 0,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 0,
        "judge_rubric": 133,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 0,
        "deduplicated": 405,
        "final": 0,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 405,
        "judge_rubric": 405,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 0,
        "deduplicated": 811,
        "final": 0,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 811,
        "judge_rubric": 811,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 0,
        "deduplicated": 408,
        "final": 0,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 408,
        "judge_rubric": 408,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T18:21:07.277487+00:00"
}
```

### 2026-08-21T18:35:24.102015+00:00 — `run:judge:challenge:rubric:round-1` — completed

```json
{
  "command": "run:judge:challenge:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "judged": 168,
    "parse_failures": 1,
    "pass": "rubric",
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T18:35:24.102015+00:00"
}
```

### 2026-08-21T19:43:53.213948+00:00 — `run:judge:challenge:adversarial:round-1` — completed

```json
{
  "command": "run:judge:challenge:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 0,
    "judged": 168,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T19:43:53.213948+00:00"
}
```

### 2026-08-21T19:43:54.960940+00:00 — `run:consensus-select:round-1` — completed

```json
{
  "command": "run:consensus-select:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "consensus": {
      "challenge": {
        "accepted_rows": 36,
        "accepted_types": {
          "concept_confusion": 20,
          "cross_concept": 16
        },
        "input_rows": 168,
        "rejected_rows": 132,
        "rejection_counts": {
          "invalid_ambiguous_challenge": 36,
          "invalid_concept_confusion_challenge": 19,
          "invalid_judge_payload": 1,
          "invalid_unanswerable_challenge": 24,
          "judge_contradiction": 23,
          "judge_evidence_not_supportive": 16,
          "judge_not_answerable": 16,
          "judge_not_faithful": 16,
          "judge_overclaim": 47,
          "judge_score_below_threshold": 123
        }
      },
      "test": {
        "accepted_rows": 268,
        "accepted_types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 76,
          "explanation": 50,
          "textual_interpretation": 65
        },
        "input_rows": 405,
        "rejected_rows": 137,
        "rejection_counts": {
          "judge_contradiction": 29,
          "judge_evidence_not_supportive": 97,
          "judge_not_answerable": 97,
          "judge_not_faithful": 96,
          "judge_not_self_contained": 6,
          "judge_overclaim": 87,
          "judge_score_below_threshold": 132,
          "judge_type_disagreement": 9
        }
      },
      "train": {
        "accepted_rows": 504,
        "accepted_types": {
          "clinical_application": 40,
          "comparison": 67,
          "cross_concept": 46,
          "definition": 120,
          "explanation": 102,
          "textual_interpretation": 129
        },
        "input_rows": 811,
        "rejected_rows": 307,
        "rejection_counts": {
          "judge_contradiction": 67,
          "judge_evidence_not_supportive": 208,
          "judge_not_answerable": 208,
          "judge_not_faithful": 208,
          "judge_not_self_contained": 20,
          "judge_overclaim": 195,
          "judge_score_below_threshold": 297,
          "judge_type_disagreement": 25
        }
      },
      "validation": {
        "accepted_rows": 279,
        "accepted_types": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 25,
          "definition": 75,
          "explanation": 62,
          "textual_interpretation": 52
        },
        "input_rows": 408,
        "rejected_rows": 129,
        "rejection_counts": {
          "invalid_judge_payload": 1,
          "judge_contradiction": 27,
          "judge_evidence_not_supportive": 89,
          "judge_not_answerable": 89,
          "judge_not_faithful": 88,
          "judge_not_self_contained": 2,
          "judge_overclaim": 86,
          "judge_score_below_threshold": 122,
          "judge_type_disagreement": 12
        }
      }
    },
    "selection": {
      "challenge": {
        "deficits": {
          "ambiguous": 25,
          "concept_confusion": 5,
          "cross_concept": 9,
          "unanswerable": 25
        },
        "rows": 36,
        "sources": {
          "lacan_text_001.txt": 9,
          "lacan_text_027.txt": 9,
          "lacan_text_033.txt": 7,
          "lacan_text_043.txt": 11
        },
        "target": 100,
        "types": {
          "concept_confusion": 20,
          "cross_concept": 16
        }
      },
      "global_duplicate_rows": 38,
      "test": {
        "deficits": {
          "clinical_application": 4,
          "comparison": 4,
          "cross_concept": 10,
          "other": 20
        },
        "rows": 212,
        "sources": {
          "lacan_text_001.txt": 43,
          "lacan_text_010.txt": 42,
          "lacan_text_027.txt": 42,
          "lacan_text_033.txt": 42,
          "lacan_text_043.txt": 43
        },
        "target": 250,
        "types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      },
      "test_seal": {
        "benchmark_grade": "silver",
        "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
        "dataset_version": "benchmark_v2",
        "rows": 212,
        "sealed": false,
        "sealed_at": "2026-08-21T19:43:54.954942+00:00",
        "sha256": "3f7d4243a60969f4fd85a9f3944c55e728bd1a5474be70210f7b24a815834f37"
      },
      "train": {
        "deficits": {
          "clinical_application": 10,
          "comparison": 15,
          "cross_concept": 16,
          "other": 40
        },
        "rows": 419,
        "sources": {
          "lacan_text_000.txt": 12,
          "lacan_text_002.txt": 12,
          "lacan_text_003.txt": 12,
          "lacan_text_004.txt": 13,
          "lacan_text_005.txt": 2,
          "lacan_text_006.txt": 12,
          "lacan_text_007.txt": 13,
          "lacan_text_008.txt": 14,
          "lacan_text_011.txt": 12,
          "lacan_text_013.txt": 11,
          "lacan_text_015.txt": 13,
          "lacan_text_016.txt": 17,
          "lacan_text_017.txt": 12,
          "lacan_text_018.txt": 12,
          "lacan_text_019.txt": 15,
          "lacan_text_020.txt": 11,
          "lacan_text_021.txt": 15,
          "lacan_text_022.txt": 12,
          "lacan_text_023.txt": 13,
          "lacan_text_025.txt": 12,
          "lacan_text_028.txt": 11,
          "lacan_text_029.txt": 14,
          "lacan_text_030.txt": 11,
          "lacan_text_031.txt": 13,
          "lacan_text_032.txt": 11,
          "lacan_text_034.txt": 12,
          "lacan_text_035.txt": 15,
          "lacan_text_037.txt": 17,
          "lacan_text_038.txt": 12,
          "lacan_text_039.txt": 15,
          "lacan_text_040.txt": 10,
          "lacan_text_041.txt": 12,
          "lacan_text_042.txt": 11,
          "lacan_text_044.txt": 8,
          "lacan_text_045.txt": 2
        },
        "target": 500,
        "types": {
          "clinical_application": 40,
          "comparison": 65,
          "cross_concept": 44,
          "definition": 100,
          "explanation": 90,
          "textual_interpretation": 80
        }
      },
      "validation": {
        "deficits": {
          "cross_concept": 5,
          "other": 20,
          "source_floor": 1
        },
        "rows": 225,
        "sources": {
          "lacan_text_009.txt": 32,
          "lacan_text_012.txt": 47,
          "lacan_text_014.txt": 48,
          "lacan_text_026.txt": 50,
          "lacan_text_036.txt": 48
        },
        "target": 250,
        "types": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 25,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-21T19:43:54.960940+00:00"
}
```

### 2026-08-21T19:43:54.960940+00:00 — `run` — failed

```json
{
  "command": "run",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "error": "Final source floors could not be satisfied: {'validation': 1}",
    "error_type": "RuntimeError"
  },
  "pipeline_version": "pipeline_v2",
  "status": "failed",
  "timestamp": "2026-08-21T19:43:54.960940+00:00"
}
```

### 2026-08-22T03:50:24.607666+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 36,
        "deduplicated": 168,
        "final": 36,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 168,
        "judge_rubric": 168,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 268,
        "deduplicated": 405,
        "final": 212,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 405,
        "judge_rubric": 405,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 504,
        "deduplicated": 811,
        "final": 419,
        "generated": 1100,
        "hard_filtered": 838,
        "judge_adversarial": 811,
        "judge_rubric": 811,
        "queue": 1100,
        "target": 500
      },
      "validation": {
        "consensus": 279,
        "deduplicated": 408,
        "final": 225,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 408,
        "judge_rubric": 408,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:50:24.607666+00:00"
}
```

### 2026-08-22T03:55:55.652081+00:00 — `select --allow-incomplete` — completed

```json
{
  "command": "select --allow-incomplete",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "consensus": {
      "challenge": {
        "accepted_rows": 65,
        "accepted_types": {
          "ambiguous": 3,
          "concept_confusion": 24,
          "cross_concept": 16,
          "unanswerable": 22
        },
        "input_rows": 168,
        "rejected_rows": 103,
        "rejection_counts": {
          "invalid_ambiguous_challenge": 40,
          "invalid_concept_confusion_challenge": 19,
          "invalid_judge_payload": 1,
          "invalid_unanswerable_challenge": 26,
          "judge_contradiction": 23,
          "judge_evidence_not_supportive": 16,
          "judge_not_answerable": 16,
          "judge_not_faithful": 16,
          "judge_overclaim": 47,
          "judge_score_below_threshold": 17
        }
      },
      "test": {
        "accepted_rows": 268,
        "accepted_types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 76,
          "explanation": 50,
          "textual_interpretation": 65
        },
        "input_rows": 405,
        "rejected_rows": 137,
        "rejection_counts": {
          "judge_contradiction": 29,
          "judge_evidence_not_supportive": 97,
          "judge_not_answerable": 97,
          "judge_not_faithful": 96,
          "judge_not_self_contained": 6,
          "judge_overclaim": 87,
          "judge_score_below_threshold": 132,
          "judge_type_disagreement": 9
        }
      },
      "train": {
        "accepted_rows": 504,
        "accepted_types": {
          "clinical_application": 40,
          "comparison": 67,
          "cross_concept": 46,
          "definition": 120,
          "explanation": 102,
          "textual_interpretation": 129
        },
        "input_rows": 811,
        "rejected_rows": 307,
        "rejection_counts": {
          "judge_contradiction": 67,
          "judge_evidence_not_supportive": 208,
          "judge_not_answerable": 208,
          "judge_not_faithful": 208,
          "judge_not_self_contained": 20,
          "judge_overclaim": 195,
          "judge_score_below_threshold": 297,
          "judge_type_disagreement": 25
        }
      },
      "validation": {
        "accepted_rows": 279,
        "accepted_types": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 25,
          "definition": 75,
          "explanation": 62,
          "textual_interpretation": 52
        },
        "input_rows": 408,
        "rejected_rows": 129,
        "rejection_counts": {
          "invalid_judge_payload": 1,
          "judge_contradiction": 27,
          "judge_evidence_not_supportive": 89,
          "judge_not_answerable": 89,
          "judge_not_faithful": 88,
          "judge_not_self_contained": 2,
          "judge_overclaim": 86,
          "judge_score_below_threshold": 122,
          "judge_type_disagreement": 12
        }
      }
    },
    "selection": {
      "challenge": {
        "deficits": {
          "ambiguous": 22,
          "concept_confusion": 1,
          "cross_concept": 9,
          "unanswerable": 3
        },
        "rows": 65,
        "sources": {
          "lacan_text_001.txt": 15,
          "lacan_text_010.txt": 3,
          "lacan_text_027.txt": 18,
          "lacan_text_033.txt": 15,
          "lacan_text_043.txt": 14
        },
        "target": 100,
        "types": {
          "ambiguous": 3,
          "concept_confusion": 24,
          "cross_concept": 16,
          "unanswerable": 22
        }
      },
      "global_duplicate_rows": 38,
      "test": {
        "deficits": {
          "clinical_application": 4,
          "comparison": 4,
          "cross_concept": 10,
          "other": 20
        },
        "rows": 212,
        "sources": {
          "lacan_text_001.txt": 43,
          "lacan_text_010.txt": 42,
          "lacan_text_027.txt": 42,
          "lacan_text_033.txt": 42,
          "lacan_text_043.txt": 43
        },
        "target": 250,
        "types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      },
      "test_seal": {
        "benchmark_grade": "silver",
        "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
        "dataset_version": "benchmark_v2",
        "rows": 212,
        "sealed": false,
        "sealed_at": "2026-08-22T03:55:55.645141+00:00",
        "sha256": "e69f433cae210f051b8f80d55d44ec2e6b6337f63aab1ee378236548a4fb4fe2"
      },
      "train": {
        "deficits": {
          "clinical_application": 10,
          "comparison": 15,
          "cross_concept": 16,
          "other": 40
        },
        "rows": 419,
        "sources": {
          "lacan_text_000.txt": 12,
          "lacan_text_002.txt": 12,
          "lacan_text_003.txt": 12,
          "lacan_text_004.txt": 13,
          "lacan_text_005.txt": 2,
          "lacan_text_006.txt": 12,
          "lacan_text_007.txt": 13,
          "lacan_text_008.txt": 14,
          "lacan_text_011.txt": 12,
          "lacan_text_013.txt": 11,
          "lacan_text_015.txt": 13,
          "lacan_text_016.txt": 17,
          "lacan_text_017.txt": 12,
          "lacan_text_018.txt": 12,
          "lacan_text_019.txt": 15,
          "lacan_text_020.txt": 11,
          "lacan_text_021.txt": 15,
          "lacan_text_022.txt": 12,
          "lacan_text_023.txt": 13,
          "lacan_text_025.txt": 12,
          "lacan_text_028.txt": 11,
          "lacan_text_029.txt": 14,
          "lacan_text_030.txt": 11,
          "lacan_text_031.txt": 13,
          "lacan_text_032.txt": 11,
          "lacan_text_034.txt": 12,
          "lacan_text_035.txt": 15,
          "lacan_text_037.txt": 17,
          "lacan_text_038.txt": 12,
          "lacan_text_039.txt": 15,
          "lacan_text_040.txt": 10,
          "lacan_text_041.txt": 12,
          "lacan_text_042.txt": 11,
          "lacan_text_044.txt": 8,
          "lacan_text_045.txt": 2
        },
        "target": 500,
        "types": {
          "clinical_application": 40,
          "comparison": 65,
          "cross_concept": 44,
          "definition": 100,
          "explanation": 90,
          "textual_interpretation": 80
        }
      },
      "validation": {
        "deficits": {
          "cross_concept": 5,
          "other": 20,
          "source_floor": 1
        },
        "rows": 225,
        "sources": {
          "lacan_text_009.txt": 32,
          "lacan_text_012.txt": 47,
          "lacan_text_014.txt": 48,
          "lacan_text_026.txt": 50,
          "lacan_text_036.txt": 48
        },
        "target": 250,
        "types": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 25,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:55:55.652081+00:00"
}
```

### 2026-08-22T03:56:15.3153523Z — `rule-correction:adaptive-refill-v2` — completed

```json
{
  "changes": [
    "Treat validation/test source-floor shortfalls as recoverable, source-targeted refill requests instead of fatal errors.",
    "Use challenge-category contracts for negative and confusion examples instead of applying the ordinary-QA all-score>=4 gate.",
    "Define the other type as argumentative/methodological structure and version its refill generator/judge prompts independently."
  ],
  "versions": {
    "consensus": "pipeline_v2.consensus.2",
    "other_generation_prompt": "pipeline_v2.generate.other.1",
    "other_rubric_prompt": "pipeline_v2.judge.rubric.other.1",
    "other_adversarial_prompt": "pipeline_v2.judge.adversarial.other.1"
  },
  "recomputed_challenge_consensus": {
    "before": 36,
    "after": 65,
    "accepted_types": {
      "ambiguous": 3,
      "concept_confusion": 24,
      "cross_concept": 16,
      "unanswerable": 22
    }
  },
  "verification": {
    "pytest": "37 passed, 1 skipped because final artifacts remain partial",
    "ruff": "all checks passed"
  }
}
```

### 2026-08-22T03:57:25.605792+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:25.605792+00:00"
}
```

### 2026-08-22T03:57:25.606794+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T03:57:25.514649+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:25.606794+00:00"
}
```

### 2026-08-22T03:57:25.737932+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1100,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:25.737932+00:00"
}
```

### 2026-08-22T03:57:25.757035+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:25.757035+00:00"
}
```

### 2026-08-22T03:57:25.775051+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 550,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:25.775051+00:00"
}
```

### 2026-08-22T03:57:25.787066+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:25.787066+00:00"
}
```

### 2026-08-22T03:57:30.055298+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 414,
        "kept_rows": 405
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 25
        },
        "duplicate_rows": 27,
        "input_rows": 838,
        "kept_rows": 811
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 415,
        "kept_rows": 408
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 414,
        "input_rows": 550,
        "rejected_rows": 136,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 17,
          "evidence_quote_not_contiguous": 21,
          "generation_parse_error": 11,
          "generic_source_reference": 10,
          "low_answer_source_overlap": 83
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 838,
        "input_rows": 1100,
        "rejected_rows": 262,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "answer_length": 1,
          "evidence_quote_length": 47,
          "evidence_quote_not_contiguous": 32,
          "generation_parse_error": 28,
          "generic_source_reference": 18,
          "low_answer_source_overlap": 144,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 415,
        "input_rows": 550,
        "rejected_rows": 135,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 27,
          "evidence_quote_not_contiguous": 15,
          "generation_parse_error": 5,
          "generic_source_reference": 10,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 81,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.055298+00:00"
}
```

### 2026-08-22T03:57:30.247635+00:00 — `run:judge:train:rubric:round-1` — completed

```json
{
  "command": "run:judge:train:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 811,
    "judged": 0,
    "pass": "rubric",
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.247635+00:00"
}
```

### 2026-08-22T03:57:30.290296+00:00 — `run:judge:train:adversarial:round-1` — completed

```json
{
  "command": "run:judge:train:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 811,
    "judged": 0,
    "pass": "adversarial",
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.290296+00:00"
}
```

### 2026-08-22T03:57:30.307812+00:00 — `run:judge:validation:rubric:round-1` — completed

```json
{
  "command": "run:judge:validation:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 408,
    "judged": 0,
    "pass": "rubric",
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.307812+00:00"
}
```

### 2026-08-22T03:57:30.325484+00:00 — `run:judge:validation:adversarial:round-1` — completed

```json
{
  "command": "run:judge:validation:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 408,
    "judged": 0,
    "pass": "adversarial",
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.325484+00:00"
}
```

### 2026-08-22T03:57:30.347372+00:00 — `run:judge:test:rubric:round-1` — completed

```json
{
  "command": "run:judge:test:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 405,
    "judged": 0,
    "pass": "rubric",
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.347372+00:00"
}
```

### 2026-08-22T03:57:30.364739+00:00 — `run:judge:test:adversarial:round-1` — completed

```json
{
  "command": "run:judge:test:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 405,
    "judged": 0,
    "pass": "adversarial",
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.364739+00:00"
}
```

### 2026-08-22T03:57:30.372252+00:00 — `run:judge:challenge:rubric:round-1` — completed

```json
{
  "command": "run:judge:challenge:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 168,
    "judged": 0,
    "pass": "rubric",
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.372252+00:00"
}
```

### 2026-08-22T03:57:30.381755+00:00 — `run:judge:challenge:adversarial:round-1` — completed

```json
{
  "command": "run:judge:challenge:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 168,
    "judged": 0,
    "pass": "adversarial",
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:30.381755+00:00"
}
```

### 2026-08-22T03:57:32.006998+00:00 — `run:consensus-select:round-1` — completed

```json
{
  "command": "run:consensus-select:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "consensus": {
      "challenge": {
        "accepted_rows": 65,
        "accepted_types": {
          "ambiguous": 3,
          "concept_confusion": 24,
          "cross_concept": 16,
          "unanswerable": 22
        },
        "input_rows": 168,
        "rejected_rows": 103,
        "rejection_counts": {
          "invalid_ambiguous_challenge": 40,
          "invalid_concept_confusion_challenge": 19,
          "invalid_judge_payload": 1,
          "invalid_unanswerable_challenge": 26,
          "judge_contradiction": 23,
          "judge_evidence_not_supportive": 16,
          "judge_not_answerable": 16,
          "judge_not_faithful": 16,
          "judge_overclaim": 47,
          "judge_score_below_threshold": 17
        }
      },
      "test": {
        "accepted_rows": 268,
        "accepted_types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 76,
          "explanation": 50,
          "textual_interpretation": 65
        },
        "input_rows": 405,
        "rejected_rows": 137,
        "rejection_counts": {
          "judge_contradiction": 29,
          "judge_evidence_not_supportive": 97,
          "judge_not_answerable": 97,
          "judge_not_faithful": 96,
          "judge_not_self_contained": 6,
          "judge_overclaim": 87,
          "judge_score_below_threshold": 132,
          "judge_type_disagreement": 9
        }
      },
      "train": {
        "accepted_rows": 504,
        "accepted_types": {
          "clinical_application": 40,
          "comparison": 67,
          "cross_concept": 46,
          "definition": 120,
          "explanation": 102,
          "textual_interpretation": 129
        },
        "input_rows": 811,
        "rejected_rows": 307,
        "rejection_counts": {
          "judge_contradiction": 67,
          "judge_evidence_not_supportive": 208,
          "judge_not_answerable": 208,
          "judge_not_faithful": 208,
          "judge_not_self_contained": 20,
          "judge_overclaim": 195,
          "judge_score_below_threshold": 297,
          "judge_type_disagreement": 25
        }
      },
      "validation": {
        "accepted_rows": 279,
        "accepted_types": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 25,
          "definition": 75,
          "explanation": 62,
          "textual_interpretation": 52
        },
        "input_rows": 408,
        "rejected_rows": 129,
        "rejection_counts": {
          "invalid_judge_payload": 1,
          "judge_contradiction": 27,
          "judge_evidence_not_supportive": 89,
          "judge_not_answerable": 89,
          "judge_not_faithful": 88,
          "judge_not_self_contained": 2,
          "judge_overclaim": 86,
          "judge_score_below_threshold": 122,
          "judge_type_disagreement": 12
        }
      }
    },
    "selection": {
      "challenge": {
        "deficits": {
          "ambiguous": 22,
          "concept_confusion": 1,
          "cross_concept": 9,
          "unanswerable": 3
        },
        "rows": 65,
        "sources": {
          "lacan_text_001.txt": 15,
          "lacan_text_010.txt": 3,
          "lacan_text_027.txt": 18,
          "lacan_text_033.txt": 15,
          "lacan_text_043.txt": 14
        },
        "target": 100,
        "types": {
          "ambiguous": 3,
          "concept_confusion": 24,
          "cross_concept": 16,
          "unanswerable": 22
        }
      },
      "global_duplicate_rows": 38,
      "test": {
        "deficits": {
          "clinical_application": 4,
          "comparison": 4,
          "cross_concept": 10,
          "other": 20
        },
        "rows": 212,
        "sources": {
          "lacan_text_001.txt": 43,
          "lacan_text_010.txt": 42,
          "lacan_text_027.txt": 42,
          "lacan_text_033.txt": 42,
          "lacan_text_043.txt": 43
        },
        "target": 250,
        "types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      },
      "test_seal": {
        "benchmark_grade": "silver",
        "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
        "dataset_version": "benchmark_v2",
        "rows": 212,
        "sealed": false,
        "sealed_at": "2026-08-22T03:57:32.000994+00:00",
        "sha256": "f19d35aee06e1db2e5500080f322776bbec9a9f490aeedfee98fb5944be38e93"
      },
      "train": {
        "deficits": {
          "clinical_application": 10,
          "comparison": 15,
          "cross_concept": 16,
          "other": 40
        },
        "rows": 419,
        "sources": {
          "lacan_text_000.txt": 12,
          "lacan_text_002.txt": 12,
          "lacan_text_003.txt": 12,
          "lacan_text_004.txt": 13,
          "lacan_text_005.txt": 2,
          "lacan_text_006.txt": 12,
          "lacan_text_007.txt": 13,
          "lacan_text_008.txt": 14,
          "lacan_text_011.txt": 12,
          "lacan_text_013.txt": 11,
          "lacan_text_015.txt": 13,
          "lacan_text_016.txt": 17,
          "lacan_text_017.txt": 12,
          "lacan_text_018.txt": 12,
          "lacan_text_019.txt": 15,
          "lacan_text_020.txt": 11,
          "lacan_text_021.txt": 15,
          "lacan_text_022.txt": 12,
          "lacan_text_023.txt": 13,
          "lacan_text_025.txt": 12,
          "lacan_text_028.txt": 11,
          "lacan_text_029.txt": 14,
          "lacan_text_030.txt": 11,
          "lacan_text_031.txt": 13,
          "lacan_text_032.txt": 11,
          "lacan_text_034.txt": 12,
          "lacan_text_035.txt": 15,
          "lacan_text_037.txt": 17,
          "lacan_text_038.txt": 12,
          "lacan_text_039.txt": 15,
          "lacan_text_040.txt": 10,
          "lacan_text_041.txt": 12,
          "lacan_text_042.txt": 11,
          "lacan_text_044.txt": 8,
          "lacan_text_045.txt": 2
        },
        "target": 500,
        "types": {
          "clinical_application": 40,
          "comparison": 65,
          "cross_concept": 44,
          "definition": 100,
          "explanation": 90,
          "textual_interpretation": 80
        }
      },
      "validation": {
        "deficits": {
          "cross_concept": 5,
          "other": 20,
          "source_floor": 1
        },
        "rows": 225,
        "sources": {
          "lacan_text_009.txt": 32,
          "lacan_text_012.txt": 47,
          "lacan_text_014.txt": 48,
          "lacan_text_026.txt": 50,
          "lacan_text_036.txt": 48
        },
        "target": 250,
        "types": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 25,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:57:32.006998+00:00"
}
```

### 2026-08-22T03:58:46.348955+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 65,
        "deduplicated": 168,
        "final": 65,
        "generated": 220,
        "hard_filtered": 168,
        "judge_adversarial": 168,
        "judge_rubric": 168,
        "queue": 220,
        "target": 100
      },
      "test": {
        "consensus": 268,
        "deduplicated": 405,
        "final": 212,
        "generated": 550,
        "hard_filtered": 414,
        "judge_adversarial": 405,
        "judge_rubric": 405,
        "queue": 550,
        "target": 250
      },
      "train": {
        "consensus": 504,
        "deduplicated": 811,
        "final": 419,
        "generated": 1102,
        "hard_filtered": 838,
        "judge_adversarial": 811,
        "judge_rubric": 811,
        "queue": 1279,
        "target": 500
      },
      "validation": {
        "consensus": 279,
        "deduplicated": 408,
        "final": 225,
        "generated": 550,
        "hard_filtered": 415,
        "judge_adversarial": 408,
        "judge_rubric": 408,
        "queue": 550,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T03:58:46.348955+00:00"
}
```

### 2026-08-22T04:28:34.351010+00:00 — `run:refill-generate:train:round-1` — completed

```json
{
  "command": "run:refill-generate:train:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1100,
    "generated": 179,
    "parse_errors": 3,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T04:28:34.351010+00:00"
}
```

### 2026-08-22T04:28:34.817411+00:00 — `run` — failed

```json
{
  "command": "run",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "error": "Unable to allocate other: 23/44 for validation",
    "error_type": "RuntimeError"
  },
  "pipeline_version": "pipeline_v2",
  "status": "failed",
  "timestamp": "2026-08-22T04:28:34.817411+00:00"
}
```

### 2026-08-22T07:41:08.145114+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:41:08.145114+00:00"
}
```

### 2026-08-22T07:41:08.146113+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T07:41:08.062528+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:41:08.146113+00:00"
}
```

### 2026-08-22T07:41:08.289921+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1191,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:41:08.289921+00:00"
}
```

### 2026-08-22T07:41:08.311944+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:41:08.311944+00:00"
}
```

### 2026-08-22T07:41:08.331966+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:41:08.331966+00:00"
}
```

### 2026-08-22T07:41:08.340478+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:41:08.340478+00:00"
}
```

### 2026-08-22T07:41:12.430601+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 391,
        "kept_rows": 382
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 30
        },
        "duplicate_rows": 32,
        "input_rows": 913,
        "kept_rows": 881
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 391,
        "kept_rows": 384
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 16,
          "evidence_quote_not_contiguous": 18,
          "generation_parse_error": 11,
          "generic_source_reference": 9,
          "low_answer_source_overlap": 65
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 913,
        "input_rows": 1191,
        "rejected_rows": 278,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 49,
          "evidence_quote_not_contiguous": 41,
          "generation_parse_error": 30,
          "generic_source_reference": 23,
          "low_answer_source_overlap": 140,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 23,
          "evidence_quote_not_contiguous": 12,
          "generation_parse_error": 5,
          "generic_source_reference": 9,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 67,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:41:12.430601+00:00"
}
```

### 2026-08-22T07:46:04.690654+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:46:04.690654+00:00"
}
```

### 2026-08-22T07:46:04.691683+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T07:46:04.593031+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:46:04.691683+00:00"
}
```

### 2026-08-22T07:46:04.848673+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1191,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:46:04.848673+00:00"
}
```

### 2026-08-22T07:46:04.870242+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:46:04.870242+00:00"
}
```

### 2026-08-22T07:46:04.891267+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:46:04.891267+00:00"
}
```

### 2026-08-22T07:46:04.902780+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:46:04.902780+00:00"
}
```

### 2026-08-22T07:46:09.292692+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 391,
        "kept_rows": 382
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 30
        },
        "duplicate_rows": 32,
        "input_rows": 913,
        "kept_rows": 881
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 391,
        "kept_rows": 384
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 16,
          "evidence_quote_not_contiguous": 18,
          "generation_parse_error": 11,
          "generic_source_reference": 9,
          "low_answer_source_overlap": 65
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 913,
        "input_rows": 1191,
        "rejected_rows": 278,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 49,
          "evidence_quote_not_contiguous": 41,
          "generation_parse_error": 30,
          "generic_source_reference": 23,
          "low_answer_source_overlap": 140,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 23,
          "evidence_quote_not_contiguous": 12,
          "generation_parse_error": 5,
          "generic_source_reference": 9,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 67,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:46:09.292692+00:00"
}
```

### 2026-08-22T07:47:53.702186+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:47:53.702186+00:00"
}
```

### 2026-08-22T07:47:53.703184+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T07:47:53.605037+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:47:53.703184+00:00"
}
```

### 2026-08-22T07:47:53.884008+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1191,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:47:53.884008+00:00"
}
```

### 2026-08-22T07:47:53.909545+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:47:53.909545+00:00"
}
```

### 2026-08-22T07:47:53.937085+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:47:53.937085+00:00"
}
```

### 2026-08-22T07:47:53.950109+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:47:53.950109+00:00"
}
```

### 2026-08-22T07:47:58.448624+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 391,
        "kept_rows": 382
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 30
        },
        "duplicate_rows": 32,
        "input_rows": 913,
        "kept_rows": 881
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 391,
        "kept_rows": 384
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 16,
          "evidence_quote_not_contiguous": 18,
          "generation_parse_error": 11,
          "generic_source_reference": 9,
          "low_answer_source_overlap": 65
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 913,
        "input_rows": 1191,
        "rejected_rows": 278,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 49,
          "evidence_quote_not_contiguous": 41,
          "generation_parse_error": 30,
          "generic_source_reference": 23,
          "low_answer_source_overlap": 140,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 23,
          "evidence_quote_not_contiguous": 12,
          "generation_parse_error": 5,
          "generic_source_reference": 9,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 67,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:47:58.448624+00:00"
}
```

### 2026-08-22T07:50:18.132776+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:50:18.132776+00:00"
}
```

### 2026-08-22T07:50:18.133716+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T07:50:18.045303+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:50:18.133716+00:00"
}
```

### 2026-08-22T07:50:18.292920+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1191,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:50:18.292920+00:00"
}
```

### 2026-08-22T07:50:18.318947+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:50:18.318947+00:00"
}
```

### 2026-08-22T07:50:18.343981+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:50:18.343981+00:00"
}
```

### 2026-08-22T07:50:18.353495+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:50:18.353495+00:00"
}
```

### 2026-08-22T07:50:22.720945+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 391,
        "kept_rows": 382
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 30
        },
        "duplicate_rows": 32,
        "input_rows": 913,
        "kept_rows": 881
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 391,
        "kept_rows": 384
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 16,
          "evidence_quote_not_contiguous": 18,
          "generation_parse_error": 11,
          "generic_source_reference": 9,
          "low_answer_source_overlap": 65
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 913,
        "input_rows": 1191,
        "rejected_rows": 278,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 49,
          "evidence_quote_not_contiguous": 41,
          "generation_parse_error": 30,
          "generic_source_reference": 23,
          "low_answer_source_overlap": 140,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 23,
          "evidence_quote_not_contiguous": 12,
          "generation_parse_error": 5,
          "generic_source_reference": 9,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 67,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:50:22.720945+00:00"
}
```

### 2026-08-22T07:51:26.002445+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:51:26.002445+00:00"
}
```

### 2026-08-22T07:51:26.003445+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T07:51:25.922146+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:51:26.003445+00:00"
}
```

### 2026-08-22T07:51:26.149457+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1191,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:51:26.149457+00:00"
}
```

### 2026-08-22T07:51:26.170742+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:51:26.170742+00:00"
}
```

### 2026-08-22T07:51:26.190760+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:51:26.190760+00:00"
}
```

### 2026-08-22T07:51:26.197272+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:51:26.197272+00:00"
}
```

### 2026-08-22T07:51:30.314259+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 391,
        "kept_rows": 382
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 30
        },
        "duplicate_rows": 32,
        "input_rows": 913,
        "kept_rows": 881
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 391,
        "kept_rows": 384
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 16,
          "evidence_quote_not_contiguous": 18,
          "generation_parse_error": 11,
          "generic_source_reference": 9,
          "low_answer_source_overlap": 65
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 913,
        "input_rows": 1191,
        "rejected_rows": 278,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 49,
          "evidence_quote_not_contiguous": 41,
          "generation_parse_error": 30,
          "generic_source_reference": 23,
          "low_answer_source_overlap": 140,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 23,
          "evidence_quote_not_contiguous": 12,
          "generation_parse_error": 5,
          "generic_source_reference": 9,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 67,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:51:30.314259+00:00"
}
```

### 2026-08-22T07:53:54.862069+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:53:54.862069+00:00"
}
```

### 2026-08-22T07:53:54.863676+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T07:53:54.768621+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:53:54.863676+00:00"
}
```

### 2026-08-22T07:53:55.015495+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1191,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:53:55.015495+00:00"
}
```

### 2026-08-22T07:53:55.039028+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:53:55.039028+00:00"
}
```

### 2026-08-22T07:53:55.059082+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:53:55.059082+00:00"
}
```

### 2026-08-22T07:53:55.068926+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:53:55.068926+00:00"
}
```

### 2026-08-22T07:53:59.653685+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 168,
        "kept_rows": 168
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 391,
        "kept_rows": 382
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 2,
          "near_question": 30
        },
        "duplicate_rows": 32,
        "input_rows": 913,
        "kept_rows": 881
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 7
        },
        "duplicate_rows": 7,
        "input_rows": 391,
        "kept_rows": 384
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 168,
        "input_rows": 220,
        "rejected_rows": 52,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 5,
          "evidence_quote_length": 8,
          "evidence_quote_not_contiguous": 10,
          "generation_parse_error": 1,
          "generic_source_reference": 20,
          "low_answer_source_overlap": 13,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_length": 16,
          "evidence_quote_not_contiguous": 18,
          "generation_parse_error": 11,
          "generic_source_reference": 9,
          "low_answer_source_overlap": 65
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 913,
        "input_rows": 1191,
        "rejected_rows": 278,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 49,
          "evidence_quote_not_contiguous": 41,
          "generation_parse_error": 30,
          "generic_source_reference": 23,
          "low_answer_source_overlap": 140,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 391,
        "input_rows": 506,
        "rejected_rows": 115,
        "rejection_counts": {
          "ambiguous_question_reference": 5,
          "evidence_quote_length": 23,
          "evidence_quote_not_contiguous": 12,
          "generation_parse_error": 5,
          "generic_source_reference": 9,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 67,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T07:53:59.653685+00:00"
}
```

### 2026-08-22T08:46:37.316945+00:00 — `run:judge:train:rubric:round-1` — completed

```json
{
  "command": "run:judge:train:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 761,
    "judged": 122,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": -2,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T08:46:37.316945+00:00"
}
```

### 2026-08-22T09:39:30.599366+00:00 — `run:judge:train:adversarial:round-1` — completed

```json
{
  "command": "run:judge:train:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 752,
    "judged": 131,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": -2,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:30.599366+00:00"
}
```

### 2026-08-22T09:39:30.616950+00:00 — `run:judge:validation:rubric:round-1` — completed

```json
{
  "command": "run:judge:validation:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 384,
    "judged": 0,
    "pass": "rubric",
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:30.616950+00:00"
}
```

### 2026-08-22T09:39:30.632460+00:00 — `run:judge:validation:adversarial:round-1` — completed

```json
{
  "command": "run:judge:validation:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 384,
    "judged": 0,
    "pass": "adversarial",
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:30.632460+00:00"
}
```

### 2026-08-22T09:39:30.646502+00:00 — `run:judge:test:rubric:round-1` — completed

```json
{
  "command": "run:judge:test:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 382,
    "judged": 0,
    "pass": "rubric",
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:30.646502+00:00"
}
```

### 2026-08-22T09:39:30.660613+00:00 — `run:judge:test:adversarial:round-1` — completed

```json
{
  "command": "run:judge:test:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 382,
    "judged": 0,
    "pass": "adversarial",
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:30.660613+00:00"
}
```

### 2026-08-22T09:39:30.667969+00:00 — `run:judge:challenge:rubric:round-1` — completed

```json
{
  "command": "run:judge:challenge:rubric:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 168,
    "judged": 0,
    "pass": "rubric",
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:30.667969+00:00"
}
```

### 2026-08-22T09:39:30.673968+00:00 — `run:judge:challenge:adversarial:round-1` — completed

```json
{
  "command": "run:judge:challenge:adversarial:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 168,
    "judged": 0,
    "pass": "adversarial",
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:30.673968+00:00"
}
```

### 2026-08-22T09:39:32.668566+00:00 — `run:consensus-select:round-1` — completed

```json
{
  "command": "run:consensus-select:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "consensus": {
      "challenge": {
        "accepted_rows": 65,
        "accepted_types": {
          "ambiguous": 3,
          "concept_confusion": 24,
          "cross_concept": 16,
          "unanswerable": 22
        },
        "input_rows": 168,
        "rejected_rows": 103,
        "rejection_counts": {
          "invalid_ambiguous_challenge": 40,
          "invalid_concept_confusion_challenge": 19,
          "invalid_judge_payload": 1,
          "invalid_unanswerable_challenge": 26,
          "judge_contradiction": 23,
          "judge_evidence_not_supportive": 16,
          "judge_not_answerable": 16,
          "judge_not_faithful": 16,
          "judge_overclaim": 47,
          "judge_score_below_threshold": 17
        }
      },
      "test": {
        "accepted_rows": 260,
        "accepted_types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 76,
          "explanation": 49,
          "textual_interpretation": 58
        },
        "input_rows": 382,
        "rejected_rows": 122,
        "rejection_counts": {
          "judge_contradiction": 28,
          "judge_evidence_not_supportive": 92,
          "judge_not_answerable": 92,
          "judge_not_faithful": 91,
          "judge_not_self_contained": 6,
          "judge_overclaim": 82,
          "judge_score_below_threshold": 120,
          "judge_type_disagreement": 3
        }
      },
      "train": {
        "accepted_rows": 535,
        "accepted_types": {
          "clinical_application": 49,
          "comparison": 75,
          "cross_concept": 54,
          "definition": 120,
          "explanation": 104,
          "other": 2,
          "textual_interpretation": 131
        },
        "input_rows": 881,
        "rejected_rows": 346,
        "rejection_counts": {
          "judge_contradiction": 79,
          "judge_evidence_not_supportive": 233,
          "judge_not_answerable": 233,
          "judge_not_faithful": 233,
          "judge_not_self_contained": 24,
          "judge_overclaim": 218,
          "judge_score_below_threshold": 333,
          "judge_type_disagreement": 28
        }
      },
      "validation": {
        "accepted_rows": 272,
        "accepted_types": {
          "clinical_application": 25,
          "comparison": 39,
          "cross_concept": 25,
          "definition": 75,
          "explanation": 62,
          "textual_interpretation": 46
        },
        "input_rows": 384,
        "rejected_rows": 112,
        "rejection_counts": {
          "invalid_judge_payload": 1,
          "judge_contradiction": 26,
          "judge_evidence_not_supportive": 81,
          "judge_not_answerable": 81,
          "judge_not_faithful": 80,
          "judge_not_self_contained": 2,
          "judge_overclaim": 78,
          "judge_score_below_threshold": 105,
          "judge_type_disagreement": 7
        }
      }
    },
    "selection": {
      "challenge": {
        "deficits": {
          "ambiguous": 22,
          "concept_confusion": 1,
          "cross_concept": 9,
          "unanswerable": 3
        },
        "rows": 65,
        "sources": {
          "lacan_text_001.txt": 15,
          "lacan_text_010.txt": 3,
          "lacan_text_027.txt": 18,
          "lacan_text_033.txt": 15,
          "lacan_text_043.txt": 14
        },
        "target": 100,
        "types": {
          "ambiguous": 3,
          "concept_confusion": 24,
          "cross_concept": 16,
          "unanswerable": 22
        }
      },
      "global_duplicate_rows": 39,
      "test": {
        "deficits": {
          "clinical_application": 4,
          "comparison": 4,
          "cross_concept": 10,
          "other": 20
        },
        "rows": 212,
        "sources": {
          "lacan_text_001.txt": 42,
          "lacan_text_010.txt": 42,
          "lacan_text_027.txt": 43,
          "lacan_text_033.txt": 42,
          "lacan_text_043.txt": 43
        },
        "target": 250,
        "types": {
          "clinical_application": 21,
          "comparison": 36,
          "cross_concept": 20,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      },
      "test_seal": {
        "benchmark_grade": "silver",
        "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
        "dataset_version": "benchmark_v2",
        "rows": 212,
        "sealed": false,
        "sealed_at": "2026-08-22T09:39:32.662048+00:00",
        "sha256": "57f7703874f5a50b62b5d7607c71b94a11c74823c2e54868d271628d3052cadc"
      },
      "train": {
        "deficits": {
          "clinical_application": 1,
          "comparison": 8,
          "cross_concept": 8,
          "other": 38
        },
        "rows": 445,
        "sources": {
          "lacan_text_000.txt": 13,
          "lacan_text_002.txt": 13,
          "lacan_text_003.txt": 15,
          "lacan_text_004.txt": 13,
          "lacan_text_005.txt": 2,
          "lacan_text_006.txt": 12,
          "lacan_text_007.txt": 14,
          "lacan_text_008.txt": 13,
          "lacan_text_011.txt": 13,
          "lacan_text_013.txt": 11,
          "lacan_text_015.txt": 13,
          "lacan_text_016.txt": 17,
          "lacan_text_017.txt": 13,
          "lacan_text_018.txt": 14,
          "lacan_text_019.txt": 16,
          "lacan_text_020.txt": 11,
          "lacan_text_021.txt": 15,
          "lacan_text_022.txt": 12,
          "lacan_text_023.txt": 13,
          "lacan_text_025.txt": 13,
          "lacan_text_028.txt": 14,
          "lacan_text_029.txt": 14,
          "lacan_text_030.txt": 13,
          "lacan_text_031.txt": 14,
          "lacan_text_032.txt": 13,
          "lacan_text_034.txt": 13,
          "lacan_text_035.txt": 15,
          "lacan_text_037.txt": 19,
          "lacan_text_038.txt": 13,
          "lacan_text_039.txt": 15,
          "lacan_text_040.txt": 11,
          "lacan_text_041.txt": 13,
          "lacan_text_042.txt": 11,
          "lacan_text_044.txt": 9,
          "lacan_text_045.txt": 2
        },
        "target": 500,
        "types": {
          "clinical_application": 49,
          "comparison": 72,
          "cross_concept": 52,
          "definition": 100,
          "explanation": 90,
          "other": 2,
          "textual_interpretation": 80
        }
      },
      "validation": {
        "deficits": {
          "comparison": 1,
          "cross_concept": 5,
          "other": 20,
          "source_floor": 1
        },
        "rows": 224,
        "sources": {
          "lacan_text_009.txt": 32,
          "lacan_text_012.txt": 46,
          "lacan_text_014.txt": 49,
          "lacan_text_026.txt": 50,
          "lacan_text_036.txt": 47
        },
        "target": 250,
        "types": {
          "clinical_application": 25,
          "comparison": 39,
          "cross_concept": 25,
          "definition": 50,
          "explanation": 45,
          "textual_interpretation": 40
        }
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T09:39:32.668566+00:00"
}
```

### 2026-08-22T10:01:10.029467+00:00 — `run:refill-generate:train:round-1` — completed

```json
{
  "command": "run:refill-generate:train:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1191,
    "generated": 130,
    "parse_errors": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T10:01:10.029467+00:00"
}
```

### 2026-08-22T10:12:49.035424+00:00 — `run:refill-generate:validation:round-1` — completed

```json
{
  "command": "run:refill-generate:validation:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 65,
    "parse_errors": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T10:12:49.035424+00:00"
}
```

### 2026-08-22T10:26:30.586753+00:00 — `run:refill-generate:test:round-1` — completed

```json
{
  "command": "run:refill-generate:test:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 506,
    "generated": 86,
    "parse_errors": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T10:26:30.586753+00:00"
}
```

### 2026-08-22T10:41:05.565107+00:00 — `run:refill-generate:challenge:round-1` — completed

```json
{
  "command": "run:refill-generate:challenge:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 220,
    "generated": 89,
    "parse_errors": 1,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T10:41:05.565107+00:00"
}
```

### 2026-08-22T10:41:10.154356+00:00 — `run:hard-filter-deduplicate:round-2` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 237,
        "kept_rows": 237
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 451,
        "kept_rows": 442
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 3,
          "near_question": 34
        },
        "duplicate_rows": 37,
        "input_rows": 1023,
        "kept_rows": 986
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 8
        },
        "duplicate_rows": 8,
        "input_rows": 443,
        "kept_rows": 435
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 237,
        "input_rows": 309,
        "rejected_rows": 72,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 8,
          "evidence_quote_length": 9,
          "evidence_quote_not_contiguous": 19,
          "generation_parse_error": 2,
          "generic_source_reference": 26,
          "low_answer_source_overlap": 17,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 451,
        "input_rows": 592,
        "rejected_rows": 141,
        "rejection_counts": {
          "ambiguous_question_reference": 4,
          "evidence_quote_length": 19,
          "evidence_quote_not_contiguous": 20,
          "generation_parse_error": 11,
          "generic_source_reference": 15,
          "low_answer_source_overlap": 82
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 1023,
        "input_rows": 1321,
        "rejected_rows": 298,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 57,
          "evidence_quote_not_contiguous": 45,
          "generation_parse_error": 30,
          "generic_source_reference": 24,
          "low_answer_source_overlap": 148,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 443,
        "input_rows": 571,
        "rejected_rows": 128,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "evidence_quote_length": 26,
          "evidence_quote_not_contiguous": 13,
          "generation_parse_error": 5,
          "generic_source_reference": 12,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 73,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T10:41:10.154356+00:00"
}
```

### 2026-08-22T19:58:02.011915+00:00 — `run:judge:train:rubric:round-2` — completed

```json
{
  "command": "run:judge:train:rubric:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 883,
    "judged": 108,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": -5,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T19:58:02.011915+00:00"
}
```

### 2026-08-22T20:41:18.743203+00:00 — `run:judge:train:adversarial:round-2` — completed

```json
{
  "command": "run:judge:train:adversarial:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 883,
    "judged": 108,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": -5,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:41:18.743203+00:00"
}
```

### 2026-08-22T20:47:00.547895+00:00 — `status` — completed

```json
{
  "command": "status",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "clean_corpus_rows": 32028,
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "pipeline_version": "pipeline_v2",
    "split_manifest_ready": true,
    "splits": {
      "challenge": {
        "consensus": 65,
        "deduplicated": 237,
        "final": 65,
        "generated": 309,
        "hard_filtered": 237,
        "judge_adversarial": 168,
        "judge_rubric": 168,
        "queue": 309,
        "target": 100
      },
      "test": {
        "consensus": 260,
        "deduplicated": 442,
        "final": 212,
        "generated": 592,
        "hard_filtered": 451,
        "judge_adversarial": 382,
        "judge_rubric": 382,
        "queue": 592,
        "target": 250
      },
      "train": {
        "consensus": 535,
        "deduplicated": 986,
        "final": 445,
        "generated": 1321,
        "hard_filtered": 1023,
        "judge_adversarial": 991,
        "judge_rubric": 991,
        "queue": 1321,
        "target": 500
      },
      "validation": {
        "consensus": 272,
        "deduplicated": 435,
        "final": 224,
        "generated": 571,
        "hard_filtered": 443,
        "judge_adversarial": 384,
        "judge_rubric": 397,
        "queue": 571,
        "target": 250
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:47:00.547895+00:00"
}
```

### 2026-08-22T20:49:21.972922+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:49:21.972922+00:00"
}
```

### 2026-08-22T20:49:21.974922+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T20:49:21.871412+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:49:21.974922+00:00"
}
```

### 2026-08-22T20:49:22.121441+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1321,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:49:22.121441+00:00"
}
```

### 2026-08-22T20:49:22.143952+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 571,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:49:22.143952+00:00"
}
```

### 2026-08-22T20:49:22.165952+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 592,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:49:22.165952+00:00"
}
```

### 2026-08-22T20:49:22.175952+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 309,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:49:22.175952+00:00"
}
```

### 2026-08-22T20:49:26.886804+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 237,
        "kept_rows": 237
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 451,
        "kept_rows": 442
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 3,
          "near_question": 34
        },
        "duplicate_rows": 37,
        "input_rows": 1023,
        "kept_rows": 986
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 8
        },
        "duplicate_rows": 8,
        "input_rows": 443,
        "kept_rows": 435
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 237,
        "input_rows": 309,
        "rejected_rows": 72,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 8,
          "evidence_quote_length": 9,
          "evidence_quote_not_contiguous": 19,
          "generation_parse_error": 2,
          "generic_source_reference": 26,
          "low_answer_source_overlap": 17,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 451,
        "input_rows": 592,
        "rejected_rows": 141,
        "rejection_counts": {
          "ambiguous_question_reference": 4,
          "evidence_quote_length": 19,
          "evidence_quote_not_contiguous": 20,
          "generation_parse_error": 11,
          "generic_source_reference": 15,
          "low_answer_source_overlap": 82
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 1023,
        "input_rows": 1321,
        "rejected_rows": 298,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 57,
          "evidence_quote_not_contiguous": 45,
          "generation_parse_error": 30,
          "generic_source_reference": 24,
          "low_answer_source_overlap": 148,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 443,
        "input_rows": 571,
        "rejected_rows": 128,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "evidence_quote_length": 26,
          "evidence_quote_not_contiguous": 13,
          "generation_parse_error": 5,
          "generic_source_reference": 12,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 73,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:49:26.886804+00:00"
}
```

### 2026-08-22T20:49:54.393833+00:00 — `run` — failed

```json
{
  "command": "run",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "error": "Can't load processor for 'google/gemma-4-E4B-it'. If you were trying to load it from 'https://huggingface.co/models', make sure you don't have a local directory with the same name. Otherwise, make sure 'google/gemma-4-E4B-it' is the correct path to a directory containing a processor_config.json file",
    "error_type": "OSError"
  },
  "pipeline_version": "pipeline_v2",
  "status": "failed",
  "timestamp": "2026-08-22T20:49:54.393833+00:00"
}
```

### 2026-08-22T20:50:40.374119+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:50:40.374119+00:00"
}
```

### 2026-08-22T20:50:40.375119+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-22T20:50:40.283506+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:50:40.375119+00:00"
}
```

### 2026-08-22T20:50:40.531155+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1321,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:50:40.531155+00:00"
}
```

### 2026-08-22T20:50:40.557236+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 571,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:50:40.557236+00:00"
}
```

### 2026-08-22T20:50:40.582234+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 592,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:50:40.582234+00:00"
}
```

### 2026-08-22T20:50:40.594241+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 309,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:50:40.594241+00:00"
}
```

### 2026-08-22T20:50:45.229440+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 237,
        "kept_rows": 237
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 451,
        "kept_rows": 442
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 3,
          "near_question": 34
        },
        "duplicate_rows": 37,
        "input_rows": 1023,
        "kept_rows": 986
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 8
        },
        "duplicate_rows": 8,
        "input_rows": 443,
        "kept_rows": 435
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 237,
        "input_rows": 309,
        "rejected_rows": 72,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 8,
          "evidence_quote_length": 9,
          "evidence_quote_not_contiguous": 19,
          "generation_parse_error": 2,
          "generic_source_reference": 26,
          "low_answer_source_overlap": 17,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 451,
        "input_rows": 592,
        "rejected_rows": 141,
        "rejection_counts": {
          "ambiguous_question_reference": 4,
          "evidence_quote_length": 19,
          "evidence_quote_not_contiguous": 20,
          "generation_parse_error": 11,
          "generic_source_reference": 15,
          "low_answer_source_overlap": 82
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 1023,
        "input_rows": 1321,
        "rejected_rows": 298,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 57,
          "evidence_quote_not_contiguous": 45,
          "generation_parse_error": 30,
          "generic_source_reference": 24,
          "low_answer_source_overlap": 148,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 443,
        "input_rows": 571,
        "rejected_rows": 128,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "evidence_quote_length": 26,
          "evidence_quote_not_contiguous": 13,
          "generation_parse_error": 5,
          "generic_source_reference": 12,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 73,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T20:50:45.229440+00:00"
}
```

### 2026-08-22T21:01:15.583342+00:00 — `run:judge:validation:rubric:round-2` — completed

```json
{
  "command": "run:judge:validation:rubric:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 384,
    "judged": 51,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T21:01:15.583342+00:00"
}
```

### 2026-08-22T21:20:21.807836+00:00 — `run:judge:validation:adversarial:round-2` — completed

```json
{
  "command": "run:judge:validation:adversarial:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 384,
    "judged": 51,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T21:20:21.807836+00:00"
}
```

### 2026-08-22T21:45:06.645486+00:00 — `run:judge:test:rubric:round-2` — completed

```json
{
  "command": "run:judge:test:rubric:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 382,
    "judged": 60,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T21:45:06.645486+00:00"
}
```

### 2026-08-22T22:10:39.179984+00:00 — `run:judge:test:adversarial:round-2` — completed

```json
{
  "command": "run:judge:test:adversarial:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 382,
    "judged": 60,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T22:10:39.179984+00:00"
}
```

### 2026-08-22T22:42:38.397824+00:00 — `run:judge:challenge:rubric:round-2` — completed

```json
{
  "command": "run:judge:challenge:rubric:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 168,
    "judged": 69,
    "parse_failures": 0,
    "pass": "rubric",
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T22:42:38.397824+00:00"
}
```

### 2026-08-22T23:13:10.135725+00:00 — `run:judge:challenge:adversarial:round-2` — completed

```json
{
  "command": "run:judge:challenge:adversarial:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 168,
    "judged": 69,
    "parse_failures": 0,
    "pass": "adversarial",
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T23:13:10.135725+00:00"
}
```

### 2026-08-22T23:13:12.754430+00:00 — `run:consensus-select:round-2` — completed

```json
{
  "command": "run:consensus-select:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "consensus": {
      "challenge": {
        "accepted_rows": 79,
        "accepted_types": {
          "ambiguous": 5,
          "concept_confusion": 27,
          "cross_concept": 20,
          "unanswerable": 27
        },
        "input_rows": 237,
        "rejected_rows": 158,
        "rejection_counts": {
          "invalid_ambiguous_challenge": 81,
          "invalid_concept_confusion_challenge": 22,
          "invalid_judge_payload": 1,
          "invalid_unanswerable_challenge": 30,
          "judge_contradiction": 32,
          "judge_evidence_not_supportive": 22,
          "judge_not_answerable": 22,
          "judge_not_faithful": 22,
          "judge_overclaim": 70,
          "judge_score_below_threshold": 24
        }
      },
      "test": {
        "accepted_rows": 283,
        "accepted_types": {
          "clinical_application": 23,
          "comparison": 39,
          "cross_concept": 27,
          "definition": 76,
          "explanation": 49,
          "other": 1,
          "textual_interpretation": 68
        },
        "input_rows": 442,
        "rejected_rows": 159,
        "rejection_counts": {
          "judge_contradiction": 34,
          "judge_evidence_not_supportive": 117,
          "judge_not_answerable": 117,
          "judge_not_faithful": 116,
          "judge_not_self_contained": 8,
          "judge_overclaim": 102,
          "judge_score_below_threshold": 155,
          "judge_type_disagreement": 10
        }
      },
      "train": {
        "accepted_rows": 573,
        "accepted_types": {
          "clinical_application": 54,
          "comparison": 83,
          "cross_concept": 60,
          "definition": 120,
          "explanation": 104,
          "other": 4,
          "textual_interpretation": 148
        },
        "input_rows": 986,
        "rejected_rows": 413,
        "rejection_counts": {
          "judge_contradiction": 88,
          "judge_evidence_not_supportive": 265,
          "judge_not_answerable": 265,
          "judge_not_faithful": 265,
          "judge_not_self_contained": 26,
          "judge_overclaim": 250,
          "judge_score_below_threshold": 396,
          "judge_type_disagreement": 48
        }
      },
      "validation": {
        "accepted_rows": 289,
        "accepted_types": {
          "clinical_application": 25,
          "comparison": 44,
          "cross_concept": 28,
          "definition": 75,
          "explanation": 62,
          "other": 1,
          "textual_interpretation": 54
        },
        "input_rows": 435,
        "rejected_rows": 146,
        "rejection_counts": {
          "invalid_judge_payload": 1,
          "judge_contradiction": 28,
          "judge_evidence_not_supportive": 94,
          "judge_not_answerable": 94,
          "judge_not_faithful": 93,
          "judge_not_self_contained": 3,
          "judge_overclaim": 91,
          "judge_score_below_threshold": 137,
          "judge_type_disagreement": 14
        }
      }
    },
    "selection": {
      "challenge": {
        "deficits": {
          "ambiguous": 20,
          "cross_concept": 5
        },
        "rows": 75,
        "sources": {
          "lacan_text_001.txt": 16,
          "lacan_text_010.txt": 3,
          "lacan_text_027.txt": 17,
          "lacan_text_033.txt": 21,
          "lacan_text_043.txt": 18
        },
        "target": 100,
        "types": {
          "ambiguous": 5,
          "concept_confusion": 25,
          "cross_concept": 20,
          "unanswerable": 25
        }
      },
      "global_duplicate_rows": 42,
      "test": {
        "deficits": {
          "clinical_application": 2,
          "comparison": 1,
          "cross_concept": 3,
          "other": 19
        },
        "rows": 225,
        "sources": {
          "lacan_text_001.txt": 45,
          "lacan_text_010.txt": 44,
          "lacan_text_027.txt": 44,
          "lacan_text_033.txt": 45,
          "lacan_text_043.txt": 47
        },
        "target": 250,
        "types": {
          "clinical_application": 23,
          "comparison": 39,
          "cross_concept": 27,
          "definition": 50,
          "explanation": 45,
          "other": 1,
          "textual_interpretation": 40
        }
      },
      "test_seal": {
        "benchmark_grade": "silver",
        "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
        "dataset_version": "benchmark_v2",
        "rows": 225,
        "sealed": false,
        "sealed_at": "2026-08-22T23:13:12.746921+00:00",
        "sha256": "0ea8d87f7ee600c3b640e08aea32987c2f86b780ccdfcbe196ddc21b8149753a"
      },
      "train": {
        "deficits": {
          "comparison": 1,
          "cross_concept": 2,
          "other": 36
        },
        "rows": 461,
        "sources": {
          "lacan_text_000.txt": 13,
          "lacan_text_002.txt": 12,
          "lacan_text_003.txt": 14,
          "lacan_text_004.txt": 14,
          "lacan_text_005.txt": 2,
          "lacan_text_006.txt": 12,
          "lacan_text_007.txt": 15,
          "lacan_text_008.txt": 13,
          "lacan_text_011.txt": 13,
          "lacan_text_013.txt": 11,
          "lacan_text_015.txt": 13,
          "lacan_text_016.txt": 17,
          "lacan_text_017.txt": 14,
          "lacan_text_018.txt": 15,
          "lacan_text_019.txt": 16,
          "lacan_text_020.txt": 12,
          "lacan_text_021.txt": 16,
          "lacan_text_022.txt": 13,
          "lacan_text_023.txt": 14,
          "lacan_text_025.txt": 14,
          "lacan_text_028.txt": 15,
          "lacan_text_029.txt": 16,
          "lacan_text_030.txt": 14,
          "lacan_text_031.txt": 14,
          "lacan_text_032.txt": 11,
          "lacan_text_034.txt": 14,
          "lacan_text_035.txt": 16,
          "lacan_text_037.txt": 18,
          "lacan_text_038.txt": 15,
          "lacan_text_039.txt": 15,
          "lacan_text_040.txt": 11,
          "lacan_text_041.txt": 14,
          "lacan_text_042.txt": 12,
          "lacan_text_044.txt": 11,
          "lacan_text_045.txt": 2
        },
        "target": 500,
        "types": {
          "clinical_application": 50,
          "comparison": 79,
          "cross_concept": 58,
          "definition": 100,
          "explanation": 90,
          "other": 4,
          "textual_interpretation": 80
        }
      },
      "validation": {
        "deficits": {
          "cross_concept": 2,
          "other": 19,
          "source_floor": 1
        },
        "rows": 229,
        "sources": {
          "lacan_text_009.txt": 32,
          "lacan_text_012.txt": 48,
          "lacan_text_014.txt": 49,
          "lacan_text_026.txt": 51,
          "lacan_text_036.txt": 49
        },
        "target": 250,
        "types": {
          "clinical_application": 25,
          "comparison": 40,
          "cross_concept": 28,
          "definition": 50,
          "explanation": 45,
          "other": 1,
          "textual_interpretation": 40
        }
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T23:13:12.754430+00:00"
}
```

### 2026-08-22T23:30:30.534350+00:00 — `run:refill-generate:train:round-2` — completed

```json
{
  "command": "run:refill-generate:train:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1321,
    "generated": 100,
    "parse_errors": 2,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T23:30:30.534350+00:00"
}
```

### 2026-08-22T23:32:08.457927+00:00 — `run:refill-generate:validation:round-2` — completed

```json
{
  "command": "run:refill-generate:validation:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 571,
    "generated": 10,
    "parse_errors": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T23:32:08.457927+00:00"
}
```

### 2026-08-22T23:38:39.685067+00:00 — `run:refill-generate:test:round-2` — completed

```json
{
  "command": "run:refill-generate:test:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 592,
    "generated": 37,
    "parse_errors": 2,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T23:38:39.685067+00:00"
}
```

### 2026-08-22T23:48:29.955867+00:00 — `run:refill-generate:challenge:round-2` — completed

```json
{
  "command": "run:refill-generate:challenge:round-2",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 309,
    "generated": 55,
    "parse_errors": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T23:48:29.955867+00:00"
}
```

### 2026-08-22T23:48:35.486624+00:00 — `run:hard-filter-deduplicate:round-3` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-3",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 282,
        "kept_rows": 282
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 474,
        "kept_rows": 465
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 3,
          "near_question": 38
        },
        "duplicate_rows": 41,
        "input_rows": 1103,
        "kept_rows": 1062
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 8
        },
        "duplicate_rows": 8,
        "input_rows": 453,
        "kept_rows": 445
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 282,
        "input_rows": 364,
        "rejected_rows": 82,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 10,
          "evidence_quote_length": 12,
          "evidence_quote_not_contiguous": 22,
          "generation_parse_error": 2,
          "generic_source_reference": 27,
          "low_answer_source_overlap": 21,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 474,
        "input_rows": 629,
        "rejected_rows": 155,
        "rejection_counts": {
          "ambiguous_question_reference": 4,
          "evidence_quote_length": 19,
          "evidence_quote_not_contiguous": 23,
          "generation_parse_error": 13,
          "generic_source_reference": 17,
          "low_answer_source_overlap": 91
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 1103,
        "input_rows": 1421,
        "rejected_rows": 318,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 65,
          "evidence_quote_not_contiguous": 45,
          "generation_parse_error": 32,
          "generic_source_reference": 28,
          "low_answer_source_overlap": 157,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 453,
        "input_rows": 581,
        "rejected_rows": 128,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "evidence_quote_length": 26,
          "evidence_quote_not_contiguous": 13,
          "generation_parse_error": 5,
          "generic_source_reference": 12,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 73,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-22T23:48:35.486624+00:00"
}
```

### 2026-08-23T00:40:49.530664+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T00:40:49.530664+00:00"
}
```

### 2026-08-23T00:40:49.532168+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-23T00:40:49.442414+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T00:40:49.532168+00:00"
}
```

### 2026-08-23T00:40:49.698610+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1421,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T00:40:49.698610+00:00"
}
```

### 2026-08-23T00:40:49.720469+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 581,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T00:40:49.720469+00:00"
}
```

### 2026-08-23T00:40:49.748000+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 629,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T00:40:49.748000+00:00"
}
```

### 2026-08-23T00:40:49.763014+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 364,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T00:40:49.763014+00:00"
}
```

### 2026-08-23T00:40:54.768864+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 282,
        "kept_rows": 282
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 474,
        "kept_rows": 465
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 3,
          "near_question": 38
        },
        "duplicate_rows": 41,
        "input_rows": 1103,
        "kept_rows": 1062
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 8
        },
        "duplicate_rows": 8,
        "input_rows": 453,
        "kept_rows": 445
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 282,
        "input_rows": 364,
        "rejected_rows": 82,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 10,
          "evidence_quote_length": 12,
          "evidence_quote_not_contiguous": 22,
          "generation_parse_error": 2,
          "generic_source_reference": 27,
          "low_answer_source_overlap": 21,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 474,
        "input_rows": 629,
        "rejected_rows": 155,
        "rejection_counts": {
          "ambiguous_question_reference": 4,
          "evidence_quote_length": 19,
          "evidence_quote_not_contiguous": 23,
          "generation_parse_error": 13,
          "generic_source_reference": 17,
          "low_answer_source_overlap": 91
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 1103,
        "input_rows": 1421,
        "rejected_rows": 318,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 65,
          "evidence_quote_not_contiguous": 45,
          "generation_parse_error": 32,
          "generic_source_reference": 28,
          "low_answer_source_overlap": 157,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 453,
        "input_rows": 581,
        "rejected_rows": 128,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "evidence_quote_length": 26,
          "evidence_quote_not_contiguous": 13,
          "generation_parse_error": 5,
          "generic_source_reference": 12,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 73,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T00:40:54.768864+00:00"
}
```

### 2026-08-23T00:41:25.370984+00:00 — `run` — failed

```json
{
  "command": "run",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "error": "页面文件太小，无法完成操作。 (os error 1455)",
    "error_type": "OSError"
  },
  "pipeline_version": "pipeline_v2",
  "status": "failed",
  "timestamp": "2026-08-23T00:41:25.370984+00:00"
}
```

### 2026-08-23T01:55:16.985620+00:00 — `run:clean` — completed

```json
{
  "command": "run:clean",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "accepted_rows": 32028,
    "input_rows": 51427,
    "output_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "rejected_rows": 19399,
    "rejection_counts": {
      "exact_duplicate": 4356,
      "length": 14837,
      "low_alphabetic_ratio": 324,
      "repeated_ngram": 10
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T01:55:16.985620+00:00"
}
```

### 2026-08-23T01:55:16.985620+00:00 — `run:split` — completed

```json
{
  "command": "run:split",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "checks": {
      "source_count_35_5_5": true,
      "train_test_disjoint": true,
      "train_validation_disjoint": true,
      "validation_test_disjoint": true
    },
    "clean_corpus_sha256": "721050bef287bdd68846bfb0842ca4b917b56c37fbe45b8926f629f0b92744da",
    "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
    "created_at": "2026-08-23T01:55:16.903410+00:00",
    "known_limitation": "Anonymous files may belong to the same underlying work.",
    "pipeline_version": "pipeline_v2",
    "source_row_counts": {
      "lacan_text_000.txt": 268,
      "lacan_text_001.txt": 1314,
      "lacan_text_002.txt": 1522,
      "lacan_text_003.txt": 1844,
      "lacan_text_004.txt": 385,
      "lacan_text_005.txt": 7,
      "lacan_text_006.txt": 106,
      "lacan_text_007.txt": 149,
      "lacan_text_008.txt": 34,
      "lacan_text_009.txt": 68,
      "lacan_text_010.txt": 142,
      "lacan_text_011.txt": 102,
      "lacan_text_012.txt": 142,
      "lacan_text_013.txt": 32,
      "lacan_text_014.txt": 1010,
      "lacan_text_015.txt": 1626,
      "lacan_text_016.txt": 1220,
      "lacan_text_017.txt": 227,
      "lacan_text_018.txt": 1265,
      "lacan_text_019.txt": 3826,
      "lacan_text_020.txt": 1351,
      "lacan_text_021.txt": 1432,
      "lacan_text_022.txt": 562,
      "lacan_text_023.txt": 483,
      "lacan_text_025.txt": 1481,
      "lacan_text_026.txt": 1787,
      "lacan_text_027.txt": 382,
      "lacan_text_028.txt": 451,
      "lacan_text_029.txt": 568,
      "lacan_text_030.txt": 185,
      "lacan_text_031.txt": 284,
      "lacan_text_032.txt": 205,
      "lacan_text_033.txt": 649,
      "lacan_text_034.txt": 177,
      "lacan_text_035.txt": 166,
      "lacan_text_036.txt": 1284,
      "lacan_text_037.txt": 891,
      "lacan_text_038.txt": 366,
      "lacan_text_039.txt": 857,
      "lacan_text_040.txt": 30,
      "lacan_text_041.txt": 1351,
      "lacan_text_042.txt": 749,
      "lacan_text_043.txt": 818,
      "lacan_text_044.txt": 224,
      "lacan_text_045.txt": 6
    },
    "source_unit": "anonymous_source_file",
    "source_work_available": false,
    "splits": {
      "challenge": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "test": [
        "lacan_text_001.txt",
        "lacan_text_010.txt",
        "lacan_text_027.txt",
        "lacan_text_033.txt",
        "lacan_text_043.txt"
      ],
      "train": [
        "lacan_text_000.txt",
        "lacan_text_002.txt",
        "lacan_text_003.txt",
        "lacan_text_004.txt",
        "lacan_text_005.txt",
        "lacan_text_006.txt",
        "lacan_text_007.txt",
        "lacan_text_008.txt",
        "lacan_text_011.txt",
        "lacan_text_013.txt",
        "lacan_text_015.txt",
        "lacan_text_016.txt",
        "lacan_text_017.txt",
        "lacan_text_018.txt",
        "lacan_text_019.txt",
        "lacan_text_020.txt",
        "lacan_text_021.txt",
        "lacan_text_022.txt",
        "lacan_text_023.txt",
        "lacan_text_025.txt",
        "lacan_text_028.txt",
        "lacan_text_029.txt",
        "lacan_text_030.txt",
        "lacan_text_031.txt",
        "lacan_text_032.txt",
        "lacan_text_034.txt",
        "lacan_text_035.txt",
        "lacan_text_037.txt",
        "lacan_text_038.txt",
        "lacan_text_039.txt",
        "lacan_text_040.txt",
        "lacan_text_041.txt",
        "lacan_text_042.txt",
        "lacan_text_044.txt",
        "lacan_text_045.txt"
      ],
      "validation": [
        "lacan_text_009.txt",
        "lacan_text_012.txt",
        "lacan_text_014.txt",
        "lacan_text_026.txt",
        "lacan_text_036.txt"
      ]
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T01:55:16.985620+00:00"
}
```

### 2026-08-23T01:55:17.203230+00:00 — `run:generate:train` — completed

```json
{
  "command": "run:generate:train",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 1421,
    "generated": 0,
    "remaining": 0,
    "split": "train"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T01:55:17.203230+00:00"
}
```

### 2026-08-23T01:55:17.227799+00:00 — `run:generate:validation` — completed

```json
{
  "command": "run:generate:validation",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 581,
    "generated": 0,
    "remaining": 0,
    "split": "validation"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T01:55:17.227799+00:00"
}
```

### 2026-08-23T01:55:17.249334+00:00 — `run:generate:test` — completed

```json
{
  "command": "run:generate:test",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 629,
    "generated": 0,
    "remaining": 0,
    "split": "test"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T01:55:17.249334+00:00"
}
```

### 2026-08-23T01:55:17.260955+00:00 — `run:generate:challenge` — completed

```json
{
  "command": "run:generate:challenge",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "already_completed": 364,
    "generated": 0,
    "remaining": 0,
    "split": "challenge"
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T01:55:17.260955+00:00"
}
```

### 2026-08-23T01:55:21.861219+00:00 — `run:hard-filter-deduplicate:round-1` — completed

```json
{
  "command": "run:hard-filter-deduplicate:round-1",
  "config_hash": "e82fe780b55a748de6a243e0c05d3620bbd62345e02f5c0b3e9b8fee17ff895c",
  "payload": {
    "deduplicate": {
      "challenge": {
        "duplicate_reasons": {},
        "duplicate_rows": 0,
        "input_rows": 282,
        "kept_rows": 282
      },
      "test": {
        "duplicate_reasons": {
          "exact_question": 1,
          "near_question": 8
        },
        "duplicate_rows": 9,
        "input_rows": 474,
        "kept_rows": 465
      },
      "train": {
        "duplicate_reasons": {
          "exact_question": 3,
          "near_question": 38
        },
        "duplicate_rows": 41,
        "input_rows": 1103,
        "kept_rows": 1062
      },
      "validation": {
        "duplicate_reasons": {
          "near_question": 8
        },
        "duplicate_rows": 8,
        "input_rows": 453,
        "kept_rows": 445
      }
    },
    "hard_filter": {
      "challenge": {
        "accepted_rows": 282,
        "input_rows": 364,
        "rejected_rows": 82,
        "rejection_counts": {
          "ambiguous_question_reference": 3,
          "evidence_quote_count": 10,
          "evidence_quote_length": 12,
          "evidence_quote_not_contiguous": 22,
          "generation_parse_error": 2,
          "generic_source_reference": 27,
          "low_answer_source_overlap": 21,
          "question_length": 1,
          "question_not_interrogative": 1
        },
        "split": "challenge"
      },
      "test": {
        "accepted_rows": 474,
        "input_rows": 629,
        "rejected_rows": 155,
        "rejection_counts": {
          "ambiguous_question_reference": 4,
          "evidence_quote_length": 19,
          "evidence_quote_not_contiguous": 23,
          "generation_parse_error": 13,
          "generic_source_reference": 17,
          "low_answer_source_overlap": 91
        },
        "split": "test"
      },
      "train": {
        "accepted_rows": 1103,
        "input_rows": 1421,
        "rejected_rows": 318,
        "rejection_counts": {
          "ambiguous_question_reference": 8,
          "answer_length": 1,
          "evidence_quote_length": 65,
          "evidence_quote_not_contiguous": 45,
          "generation_parse_error": 32,
          "generic_source_reference": 28,
          "low_answer_source_overlap": 157,
          "question_length": 5,
          "question_not_interrogative": 3,
          "repetitive_answer": 1
        },
        "split": "train"
      },
      "validation": {
        "accepted_rows": 453,
        "input_rows": 581,
        "rejected_rows": 128,
        "rejection_counts": {
          "ambiguous_question_reference": 6,
          "evidence_quote_length": 26,
          "evidence_quote_not_contiguous": 13,
          "generation_parse_error": 5,
          "generic_source_reference": 12,
          "long_direct_copy": 1,
          "low_answer_source_overlap": 73,
          "question_length": 1
        },
        "split": "validation"
      }
    }
  },
  "pipeline_version": "pipeline_v2",
  "status": "completed",
  "timestamp": "2026-08-23T01:55:21.861219+00:00"
}
```
