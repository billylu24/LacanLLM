# LacanLLM Data Card

## Dataset summary

The checked historical file contains 46,839 English SFT rows. A question was generated from a source passage; the passage itself became the target answer. The data is synthetic instruction data, not expert-annotated ground truth.

## Schema

| Field | Meaning |
|---|---|
| `schema_version` | Record format version |
| `instruction` | Synthetic user question |
| `input` | Optional extra context; currently empty |
| `output` | Source passage used as the answer |
| `source_file` | Provenance filename |
| `paragraph_index` | Position in the source file |
| `char_count` | Answer length in characters |

## Processing

1. Normalize text and reconstruct paragraphs;
2. Remove short/long fragments and obvious boilerplate;
3. Generate one question for a passage;
4. Validate schema and heuristic quality;
5. In v2, rank candidates, deduplicate normalized outputs, split, and audit leakage.

## Known issues

- All 46,839 historical checked rows currently have missing `source_file` and `paragraph_index` values;
- The historical checked file contains 5,284 repeated normalized outputs;
- The historical 5,000-row experiment split has 25 exact answers shared across train and validation;
- Some passages contain OCR word breaks not caught by the original heuristic filter;
- The text selection may overrepresent passages near the preferred character length.

## Required work before publishing v2 data

- Recover provenance from the original source-processing stage or regenerate the dataset;
- Record title, author/editor/translator, edition, publication year, source URL, and license/public-domain status;
- Confirm that redistribution of source passages and derivative SFT rows is permitted;
- Add OCR-quality sampling and a documented rejection threshold;
- Publish dataset hashes and a leakage-free split audit.

## License and access

No independent dataset license is granted by the repository's MIT code license. Treat the dataset as research-only until provenance and redistribution rights are completed.
