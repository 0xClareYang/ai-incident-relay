# Interfaces and run procedure

All commands execute from the project root with Python 3.12+, standard library only. Examples below use Windows `py -3`; on macOS/Linux replace it with `python3`.

```
py -3 relay.py capture --url https://example.org/article --out runs/my-live/snapshots --source-id S1 --title "Article title" --publisher "Publisher"
py -3 relay.py quote runs/my-live/snapshots/S1.json --claim-id C1 --evidence-id E1 --start 0 --end 30 --relation supports --extent "Only supports the named event, not all users."
py -3 relay.py validate runs/my-live/ledger.json
py -3 relay.py export runs/my-live/ledger.json --out runs/my-live/export
```

Do not reuse output directories: each export is immutable by convention and refuses an existing directory. Capture also refuses an existing source ID. Save source.snapshot paths relative to the ledger's folder (prefix `snapshots/` if appropriate).

## Ledger v1

Top-level: schema_version `1.0`, run object, claims array, sources array, evidence array, answer_items array.

- run: mode (`live` or `replay`), input, started_at (UTC ISO timestamp), as_of (snapshot cutoff), case_nature (`reported_real_incident`, `hypothetical`, `exercise`, `unverified`), scope, scope_match (`within`, `partial`, `outside`), model, limitations (strings), human_review and semantic_review (each status pending/passed/failed, reviewer, notes).
- claim: claim_id, text, entities (strings), time (string or null), scope, status (`supported`, `contradicted`, `disputed`, `insufficient_evidence`, `inference`), evidence_ids, reason, limitations, statement_attribution (string or null), correction (status `none_known`/`updated`/`corrected`/`unverified`, source_ids, notes).
- source: source_id, title, url, publisher, published_at/updated_at/event_time (nullable), retrieved_at, time_notes, source_type, source_chain_id, derived_from (source IDs), relationship, interests, access_status (`read`, `partial`, `failed`, `restricted`), read_scope, snapshot (path, sha256, extractor; optionally raw_path/raw_sha256). Missing dates are null with a reason; retrieval is not publication. Host excerpts must be partial with tool provenance and read scope. A republication must name its parent in `derived_from` and keep the parent's `source_chain_id`; reports count cited documents and declared chains separately. This depends on correct provenance metadata and does not automatically discover hidden source dependencies.
- evidence: evidence_id, claim_id, source_id, quote, locator (zero-based Unicode character start inclusive/end exclusive), relation supports/contradicts/context, extent. Offsets refer to the stored extracted UTF-8 text, not the original HTML. reciprocal claim.evidence_ids are required.
- answer item: text, claim_ids (nonempty), evidence_ids. A statement of insufficient evidence may lack evidence but must cite a claim documenting the gap.

Read `cases/hf-demo-public/manifest.json` for the bounded public demo case. The shipped snapshots are exact short excerpts, NOT full pages; do not assume they cover every claim. If unavailable, report unavailable rather than constructing facts from memory. `cases/hf-demo-public/ledger.json` is a developer-authored demo output, not a universal answer or held-out scoring key. For a user-provided case, use that manifest and source directory instead.

## Semantic review checklist

For each answer, document whether the quote entails that exact scope, who is making the statement, whether it preserves “may”/“according to”, and whether conflicting or newer material changes it. A historical source saying unknown followed by new attribution is an update, not automatically a contradiction or correction. An institutional account of motive is an interpretation. Multiple publishers can share the same underlying evidence; source chains may need per-evidence analysis.

The collector captures the response body and extracted text; it does not prove the server returned the full article or bypass challenges. Inspect for login/anti-bot/truncation pages before adding evidence. No source means insufficient evidence, not false.
