# Docs Version Navigator

An API question can have two correct answers depending on the release. Docs Version Navigator makes the release an explicit retrieval constraint and attaches the matching source section to each answer.

| In the demo | Version 1.0 | Version 2.0 |
|---|---|---|
| Client initialization | `Atlas.connect(url)` | `Client.open(endpoint=url)` |

**What the benchmark revealed.** The independent 24-question evaluation reached 97.1% Recall@5, yet answered none of its 17 supported questions. The system located the right passages and then rejected natural paraphrases during answer selection. The real `packaging.Version` reference evaluation scored 13/20 across two releases. This repository includes both the working examples and those failure cases.

**Explore the evidence:** [full QA review](QA-REVIEW.md) · [blind case ledger](qa/extended/blind-cases.json) · [real API reference](manual) · [mutation checks](mutation_check.py). Research prototype; release review remains open.

Ask a documentation question against one specific release, or compare several releases. Every answer includes the exact supporting passage, version, manual identifier, section and character offsets. Runs locally with Python 3.12+ and SQLite. The two included manuals describe Atlas SDK, a fictional library designed to make version mix-ups visible and testable.

## Run on macOS or Linux

From the extracted project directory:

```bash
python3 navigator.py sync demo --db atlas.sqlite
python3 navigator.py ask "Show the client initialization syntax" --version 1.0 --version 2.0 --db atlas.sqlite
python3 navigator.py ask "Resumable transfer retention window?" --version 2.0 --db atlas.sqlite
```

The first query returns `Atlas.connect(url)` for 1.0 and `Client.open(endpoint=url)` for 2.0. The second joins the resume-window passage to the release-specific cache lifetime. A question such as “Where is the billing office?” returns “I don't know.” Include each target version explicitly with `--version`. JSON output is suitable for piping to other programs. Errors go to stderr and return exit code 2.

## How retrieval and answers work

1. Sync validates UTF-8 JSON manuals, normalizes line endings and Unicode, splits long sections at paragraph boundaries, then atomically replaces a SQLite snapshot. Section text retains code, negations, units and formatting. Each passage has a content-derived ID.
2. A query opens one read transaction across all requested versions. Corpus and index checksums detect changed files or inconsistent records. Run sync after edits, moves or deletions. Keep the database outside the corpus directory.
3. Within each version, the retriever combines BM25 with cosine similarity over sparse normalized word counts, then boosts exact section titles. It can correct near-spellings and use author-supplied multilingual aliases. A clause needs evidence for all of its retained terms. Multiple independent clauses are joined only when each can be resolved.
4. The extractive generator assembles cited passages. It can follow one explicit same-version section reference. Ambiguity, conflicting versions of the same section within a release, or missing evidence trigger abstention. The output records the scoring model identifier, retrieved candidates and cited passages. Section offsets are Unicode character positions within the normalized section body.

## Bring your own manuals

Put a JSON file per manual/release in a dedicated local directory. Use the included files as templates. Each file needs a stable `source`, a `version`, an ISO calendar `date`, and a non-empty `sections` array. Each section contains `title`, `aliases` and `text`. Optional `see` names one related section. Keep section titles unique within a release. Each file has a 512 KiB limit. Files in the corpus should be trusted, public documentation. The ingestion patterns catch common contact details and secrets; they are not a general anonymizer.

## Reproduce the review

```bash
python3 -m unittest discover -s tests -v
python3 qa_coverage.py
python3 evaluate.py --out run --baseline qa/baseline.json
```

The suites use the standard library and can also be collected by pytest. Read **QA-REVIEW.md** for the checklist, evidence, thresholds and release decision. The evaluator writes JSON per case, summary metrics and a sample for a human reviewer. A zero evaluator exit status covers automated gates only. The saved run used Linux; the RSS field is measured in KiB there. On macOS, the OS reports that field in bytes.

## Scope

This is offline extractive RAG. It uses a transparent lexical model and original example manuals. Free-form reasoning, learned semantic embeddings, arbitrary file parsing, version discovery and translation are outside this implementation. Curated aliases influence retrieval quality. Malformed documents stop the entire sync. For this small-corpus design, queries check corpus bytes for freshness and score passages exhaustively; run a fresh benchmark before using it on large manuals.

## Frozen release candidate and expanded QA

The full review is in QA-REVIEW.md. Current decision: **NOT APPROVED**. The original toy-corpus regression scores and the later independent blind results measure different things. Use the latter to judge natural-language generalization.

```bash
python3 mutation_check.py
python3 extended_eval.py
python3 navigator.py sync manual --db packaging.sqlite
python3 navigator.py ask "Explain from_parts" --version 26.3 --db packaging.sqlite
```

The extended evaluator intentionally exits nonzero while an accuracy gate fails. See qa/extended/ for thresholds, the application freeze, per-question outputs, mutation evidence and the 2,106-passage latency run. Replays use only the standard library and included data. The gold set is disclosed with the release candidate and should be treated as regression data for later revisions. Labels for the blind set were written by an independent assistant instance; there was no independent human sign-off.

The real corpus contains the API reference docstrings for every public member of pypa/packaging's Version class in installed distributions 25.0 and 26.3. One method, from_parts, is present only in the newer snapshot. The included manual date is the capture date. To regenerate a snapshot, run tooling/export_manual.py in a Python environment with that exact packaging version installed. Attribution and upstream licenses are in third-party/. Upstream manual: https://packaging.pypa.io/en/stable/version.html
