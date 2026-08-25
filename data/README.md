# Data layout

`source/cleaned_corpus/paragraphs.jsonl` is the only retained input from the
earlier project. It contains 32,028 cleaned source paragraphs and is immutable.
Its expected hash and row count are recorded beside it in `manifest.json`.

All Pipeline v3 derived artifacts must use new versioned paths. They are ignored
until the implementation defines their schemas and audit policy.
