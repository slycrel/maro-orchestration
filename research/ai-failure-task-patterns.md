# AI Failure Task Patterns — Catalog

> Produced by a Maro research run (692bd96f-brisk-lichen, 2026-07-11, ~$0.57,
> goal_achieved=True, closure complete=True 0.82). Raw source data
> (`filtered_failures.json`, `analysis.json`, `hn_results.json`,
> `reddit_results.json`) lives in
> `~/.maro/workspace/projects/ai-failure-task-patterns/`. Consumed by
> `docs/CAPABILITIES.md` as a test-goal source.

Sourced from real Reddit and Hacker News posts where users describe a concrete task an AI assistant got wrong, with enough evidence in the quote to diagnose *why*. Built for orchestration test-goal design: each entry maps a real failure to the concrete capability an orchestrated (multi-step, tool-using, verifying) run would need that a single chat turn doesn't have.

**18 entries, 5 pattern families.** See [Research Audit Trail](#research-audit-trail) for methodology, sources, and exclusions.

---

## Family 1: Live data + verification (11 entries)

Model answers from static/parametric memory when the task requires retrieving and grounding against a current, external, checkable source — and fabricates plausible-looking specifics (citations, quotes, links) when it doesn't actually have the data.

### 1.1 — Explain a remembered technical concept
> "I just asked ChatGPT 4o to explain irreducible control flow graphs to me... It gave me a couple of great definitions, with illustrative examples and counterexamples. I puzzled through one of the irreducible examples, and eventually realized it wasn't irreducible. I pointed out the error, and it gave a more complex example, also incorrect." — HN, sfink (2025-02-28)

- **Failure mode:** Confidently wrong technical explanation; correction produced a second wrong example instead of a grounded one.
- **Root cause:** no_verification (primary), no_self_check_after_correction (secondary) — no grounding against an authoritative reference at generation or correction time.
- **Orchestration needs:** Ground technical claims against an authoritative reference (textbook/spec) before presenting; on user-flagged correction, re-verify against that same reference rather than just regenerating; surface uncertainty when no source was checked.
- Source: [HN #43200146](https://news.ycombinator.com/item?id=43200146)

### 1.2 — Model a statically typed language (type theory)
> "over Christmas, I used chat gpt to model a statically typed language I was working on... it was subtly incorrect, and gave inconsistent evaluations / overviews. not knowing a bit about type theory, I wouldn't actually be able to evaluate how good the information I got out of it was." — HN, potsandpans (2023-04-19)

- **Failure mode:** Subtly/deceptively wrong output the user (non-expert) had no way to catch.
- **Root cause:** no_verification (primary), stale_knowledge (secondary).
- **Orchestration needs:** Route type-theory claims through a type-checker/formal verification tool instead of free-text assertion; run consistency checks across repeated evaluations and flag divergence; explicitly flag domains with no available self-check.
- Source: [HN #35632530](https://news.ycombinator.com/item?id=35632530)

### 1.3 — Verify a historical claim with a source
> "The process of chlorinating water was first done illegally. I tried to find a source on this but it doesn't seem to be true?... I asked ChatGPT to find a source for the claim, and it reported the claim was false... I can't find anything claiming it was illegal?" — HN, maxbond (2026-02-10)

- **Failure mode:** Asked to find a source for a claim; task never actually resolved.
- **Root cause:** no_live_data (primary), no_verification (secondary).
- **Orchestration needs:** Live web search scoped to primary/authoritative sources; cross-reference against 2-3 independent sources before asserting true/false; output "unable to verify" when sources conflict or are absent.
- Source: [HN #46968034](https://news.ycombinator.com/item?id=46968034)

### 1.4 — Quote a specific technical doc (Qt6)
> "Once ChatGPT gave me a 'quote' from the Qt6 docs to support a particular claim; however, I was sceptical and looked at the link. ChatGPT not only made up the quote, it actually said the opposite of the linked docs." — HN, spacechild1 (2025-10-30)

- **Failure mode:** Fabricated a verbatim-looking quote that contradicted the real linked source.
- **Root cause:** no_live_data (primary), no_verification (secondary) — presented generated text as a citation without fetching it.
- **Orchestration needs:** Fetch the actual document before quoting; diff the generated quote against fetched source text and reject non-matches; show the fetched snippet alongside any quote.
- Source: [HN #45754959](https://news.ycombinator.com/item?id=45754959)

### 1.5 — Recommend restaurants in a specific city, with real links
> "I asked ChatGPT last year to give me the top five breakfast restaurants in Houston according to Reddit, sources included, no fake links. It gave me three or four real restaurants and one fake one. All (all!) of the links were fake. I do this every time I'm in a different city." — HN, nunez (2025-01-14)

- **Failure mode:** Mostly-real recommendations, but a fabricated restaurant and 100% fake links.
- **Root cause:** local_geographic_specificity + no_live_data + no_verification — also tagged in Family 2.
- **Orchestration needs:** Live local search via a maps/business-listing API scoped to the named city; HTTP-validate every generated URL before including it; cross-reference against a real aggregator (Reddit search, Yelp/Google) instead of generating from training data.
- Source: [HN #42694330](https://news.ycombinator.com/item?id=42694330)

### 1.6 — Describe a specific obscure book
> "Right now, 30 seconds ago, I asked ChatGPT to tell me about a book I found that was written in the 60s. It made up the entire description. When I pointed this out, it apologized and then made up another description." — HN, keiferski (2026-03-05)

- **Failure mode:** Entire book description fabricated; correction produced a second fabrication.
- **Root cause:** long_tail_knowledge_gap (primary), no_live_data + no_self_check_after_correction (secondary).
- **Orchestration needs:** Live search against library catalogs (WorldCat/ISBN databases) for long-tail titles before describing; on zero-confidence retrieval, say "insufficient information found"; on correction, re-run retrieval instead of regenerating from memory again.
- Source: [HN #47264291](https://news.ycombinator.com/item?id=47264291)

### 1.7 — Fact-check a biology claim with citations
> "I asked ChatGPT what turned on a gene, and it said Protein X turns on Gene Y as per -fake citation-. Asking today if Protein X turns on Gene Y ChatGPT said there is no evidence, and showed 2 real citations of factors that may turn on Gene Y." — HN, stanford_labrat (2024-12-06)

- **Failure mode:** First answer had a fabricated supporting citation; a later re-run gave the correct opposite answer with real citations.
- **Root cause:** no_live_data (primary), no_verification (secondary) — first pass wasn't grounded in literature search at all.
- **Orchestration needs:** Live literature search (PubMed/Scholar API) before asserting a biological claim with citations; verify cited sources actually exist and support the claim (fetch + read, don't name-drop); flag answer instability across repeated queries as a verification signal.
- Source: [HN #42342642](https://news.ycombinator.com/item?id=42342642)

### 1.8 — Summarize a specific known book
> "I once asked ChatGPT to give me a summary of a book which I have read, and it provided 5 points, which is correct since the book indeed has 5 main principles (it was Getting things done book), but 2 out of 5 were just incorrect." — HN, greyman (2023-03-29)

- **Failure mode:** Correct structure (5 points), 2 of 5 factually wrong.
- **Root cause:** no_verification (primary), stale_knowledge (secondary) — generated from imprecise recall, not the actual text.
- **Orchestration needs:** Retrieve actual source text (or reliable structured summary) for the named book instead of summarizing from memory; verify each extracted point against the retrieved source; flag as unverified when source text isn't available.
- Source: [HN #35359458](https://news.ycombinator.com/item?id=35359458)

### 1.9 — Check a product's privacy claims against its FAQ
> "GPT hallucinated: 'To alleviate concerns, Comet is designed so that much data is stored locally by default...' From its citation: 'Comet may process some local data using Perplexity's servers to fulfill your queries...' chatGPT is farrrr more generous than what Comet's FAQ states." — HN, AlexErrant (2025-10-06)

- **Failure mode:** Cited a real source but the paraphrase contradicted what that source actually said.
- **Root cause:** no_verification (primary), no_live_data (secondary) — citation attached but not actually used to ground the answer.
- **Orchestration needs:** Fetch the cited source and generate the answer FROM the fetched text; automated diff/consistency check between claim and fetched content before presenting; reject citations that don't support the paired claim.
- Source: [HN #45491232](https://news.ycombinator.com/item?id=45491232)

### 1.10 — Describe an obscure author's books
> "I asked it to describe the books of an obscure author. GPT-4o hallucinated books. GPT-4 knew it needed to do an internet search." — HN, Seattle3503 (2024-05-14)

- **Failure mode:** One model version hallucinated titles from memory; a different version recognized it should search instead.
- **Root cause:** tool_routing_failure (primary), long_tail_knowledge_gap (secondary) — partly a routing decision, not just a knowledge gap.
- **Orchestration needs:** Confidence/familiarity check on the entity before answering — low-frequency entities force a live search step; make the search-trigger decision explicit and auditable, not left to implicit model judgment.
- Source: [HN #40351302](https://news.ycombinator.com/item?id=40351302)

### 1.11 — Extract SEC filing data into structured JSON
> "Valid JSON, Wrong Answer: A boy and his LLM. A saga with SEC filings, a 90% android and a 30% zombie so far..." — Reddit r/LocalLLaMA, /u/Skiata (2026-06-20)

- **Failure mode:** Output was schema-valid JSON but with incorrect extracted content.
- **Root cause:** no_verification (primary) — format compliance checked, content accuracy against the source filing was not.
- **Orchestration needs:** Retrieve the actual SEC filing (EDGAR API/full-text search) as the extraction source; field-level verification against the source document; treat "schema-valid" and "content-verified" as separate checks — passing one must not imply the other.
- Source: [Reddit r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1uajebu/valid_json_wrong_answer_a_boy_and_his_llm_a_saga/) *(title-only evidence — see audit trail)*

---

## Family 2: Local/geographic + real-time specificity (2 entries)

Task is scoped to a specific real-world instance (this fridge model, this city's restaurants) that generic training-data knowledge can't resolve; requires live, instance-specific lookup rather than category-level answers.

### 2.1 — Diagnose a specific fridge model's mechanical problem
> "I talked to GPT yesterday about a fairly simple problem I'm having with my fridge... It new the spec, but was convinced the components were different (single compressor, for example, whereas mine has 2 separate systems) and was hypothesizing the problem as being something that doesn't exist on this model." — HN, blobbers (2025-08-17)

- **Failure mode:** Diagnosed against the majority/common fridge configuration instead of this model's actual (dual-compressor) configuration.
- **Root cause:** local_geographic_specificity (primary), stale_knowledge + no_verification (secondary).
- **Orchestration needs:** Capture the exact model number and perform a live lookup of that model's service manual/spec sheet; ground diagnosis steps in the retrieved document, not general appliance-category knowledge; flag when the model's assumption is a "majority case" guess and confirm against source.
- Source: [HN #44929293](https://news.ycombinator.com/item?id=44929293)

### 2.2 — Restaurant recommendations for a specific city
*(see Family 1.5 — cross-tagged: this entry is as much a local/geo lookup failure as a fabrication-verification failure)*
- Source: [HN #42694330](https://news.ycombinator.com/item?id=42694330)

---

## Family 3: Tool-use + execution verification (3 entries)

Model asserts an output (code result, API existence, extracted data) without actually running or checking it — or the failure originates in a non-LLM pipeline stage (parsing, ingestion) that then feeds confidently-wrong content into generation.

### 3.1 — CLI function usage / code example lookup
> "It has made up tags for cli functions, suggested nonexistent functions with usage instructions, it's given me operations in the wrong order, and my personal favorite it gave me a code example in the wrong language (think replying Visual Basic for C)." — HN, asciimov (2025-06-24)

- **Failure mode:** Fabricated nonexistent CLI functions with usage instructions; wrong language entirely for a code example.
- **Root cause:** tool_routing_failure (primary), no_verification (secondary).
- **Orchestration needs:** Look up real API/CLI documentation before citing function signatures; validate generated code against the requested language/toolchain (syntax check, linter); reject or flag function names not found in retrieved docs instead of inventing plausible usage.
- Source: [HN #44371652](https://news.ycombinator.com/item?id=44371652) *(Google AI search, not ChatGPT/Claude directly — kept as adjacent LLM-assistant evidence)*

### 3.2 — Iteratively write address-parsing code
> "It updated the code, and gave me both the sample code and sample output. Sample output was correct, but the code wasn't producing correct output." — HN, narmiouh (2025-10-04)

- **Failure mode:** Claimed-correct sample output didn't match what the actual generated code produced when run.
- **Root cause:** no_execution_grounding (primary) — the claimed result was never actually verified by execution.
- **Orchestration needs:** Execute generated code in a sandbox against the actual input data at each iteration, not just present hand-written sample output; diff the sandbox-produced output against the claimed output before calling the iteration successful; loop automatically on mismatch instead of surfacing an unverified "this should work."
- Source: [HN #45474995](https://news.ycombinator.com/item?id=45474995)

### 3.3 — Debug a RAG pipeline returning wrong answers
> "Spent a week debugging why my RAG answers were wrong. Turned out it was the PDF parser." — Reddit r/LocalLLaMA, /u/Mountain-Positive274 (2026-03-05)

- **Failure mode:** Wrong answers were blamed on model reasoning; root cause was actually a broken ingestion-stage parser.
- **Root cause:** pipeline_ingestion_failure (primary) — a non-LLM pipeline stage silently corrupted content the model then confidently answered from.
- **Orchestration needs:** Ingestion-stage QA that validates parsed content against the source before indexing; track provenance per retrieved chunk so wrong answers trace back to a specific ingestion step; treat pipeline components (parsers, chunkers, embedders) as their own verification surface, separate from LLM output verification.
- Source: [Reddit r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1rlgilp/spent_a_week_debugging_why_my_rag_answers_were/) *(title-only evidence — see audit trail)*

---

## Family 4: State/session management (2 entries)

Failure isn't a knowledge or reasoning gap at all — it's loss of continuity/state across a multi-step or long-running interaction that the product layer failed to persist or index.

### 4.1 — Retrieve yesterday's chat
> "Claude AI can't find our chat although last active was 1 day ago" — Reddit r/ClaudeAI, /u/LinguisticsEngineer (2026-04-15)

- **Failure mode:** Assistant could not locate/retrieve a chat from one day prior.
- **Root cause:** state_session_loss (primary) — a session persistence/indexing failure in the product layer, unrelated to model knowledge or reasoning.
- **Orchestration needs:** Persist conversation state in an external, reliably indexed store, not solely the in-product session recall; provide an explicit, queryable session/history API an orchestrator can check; surface a clear failure signal (not a silent "can't find it") so the orchestrator can fall back to alternate state sources.
- Source: [Reddit r/ClaudeAI](https://www.reddit.com/r/ClaudeAI/comments/1smbmi1/claude_ai_cant_find_our_chat_although_last_active/) *(title-only evidence — see audit trail)*

### 4.2 — Continue a multi-hour coding session across a day boundary
> "I am using claude to build software and apps and it's so great, but then it forgets and can't find what I spent two hours taking about yesterday. Please please help." — Reddit r/ClaudeAI, /u/thesupersoap33 (2026-02-05)

- **Failure mode:** Two hours of build decisions became unrecoverable the next day, forcing a manual context restart.
- **Root cause:** state_session_loss (primary) — long-running multi-step work lost its state entirely.
- **Orchestration needs:** Maintain an external, file-based or DB-backed work log/checkpoint of decisions and progress independent of chat session memory; auto-checkpoint at defined step boundaries; on resume, reload from the external checkpoint rather than depending on the chat product's own memory.
- Source: [Reddit r/ClaudeAI](https://www.reddit.com/r/ClaudeAI/comments/1qx20m7/i_am_using_claude_to_build_software_and_apps_and/) *(title-only evidence — see audit trail)*

---

## Family 5: Verification + self-correction gap (3 entries, overlapping with Family 1)

Model produces a wrong technical claim and, even when the user points out the error, produces a *new* wrong answer rather than checking against ground truth — an absence of any grounded self-verification loop, not just a one-off wrong guess.

### 5.1 — Irreducible control flow graphs (see 1.1)
Correction produced a second wrong example instead of a grounded one. [HN #43200146](https://news.ycombinator.com/item?id=43200146)

### 5.2 — Type-theory modeling, inconsistent across evaluations (see 1.2)
No self-check across repeated evaluations of the same question. [HN #35632530](https://news.ycombinator.com/item?id=35632530)

### 5.3 — Obscure 1960s book description (see 1.6)
Correction produced a second fabricated description rather than a retrieval. [HN #47264291](https://news.ycombinator.com/item?id=47264291)

### 5.4 — Product feature currency: does a connector exist?
> "Can't find Microsoft 365 connector in Claude Cowork — does it actually exist?" — Reddit r/ClaudeAI, /u/Flyfishdk_daGr8 (2026-04-14)

- **Failure mode:** User couldn't find a specific product connector and questioned whether it exists at all.
- **Root cause:** product_feature_currency_gap (primary), no_live_data (secondary) — assistant/product documentation or the user's static knowledge of the feature set is out of date relative to what's actually shipped.
- **Orchestration needs:** Check current product documentation/feature-flag state live rather than answering from a static or cached feature list; version/date-stamp feature-existence answers so staleness is visible; route feature-availability questions to an authoritative, frequently-updated source (release notes, docs site) instead of general knowledge.
- Source: [Reddit r/ClaudeAI](https://www.reddit.com/r/ClaudeAI/comments/1skzn8s/cant_find_microsoft_365_connector_in_claude/) *(title-only evidence — see audit trail)*

---

## Root-Cause Taxonomy (11 categories)

| Category | Meaning |
|---|---|
| `stale_knowledge` | Training-data cutoff makes the answer outdated |
| `no_live_data` | Task needs a live fetch/search; model answered from memory instead |
| `no_verification` | No check of generated content against any ground truth before presenting |
| `no_self_check_after_correction` | User flags an error; model regenerates without re-verifying |
| `local_geographic_specificity` | Task scoped to a specific real-world instance that generic knowledge can't resolve |
| `long_tail_knowledge_gap` | Entity/fact is too obscure for reliable parametric recall |
| `no_execution_grounding` | Claimed code/computation output was never actually run to confirm |
| `tool_routing_failure` | Model should have triggered a tool/search but didn't (or vice versa) |
| `pipeline_ingestion_failure` | Failure originates in a non-LLM stage (parsing, chunking) upstream of generation |
| `state_session_loss` | Product-layer failure to persist/index session state, unrelated to reasoning |
| `product_feature_currency_gap` | Assistant's own product/feature knowledge is stale relative to what's shipped |

---

## Research Audit Trail

**Sources queried:**
- Hacker News Algolia API (`hn.algolia.com/api/v1/search`, `tags=comment`), 17 queries: `chatgpt useless for`, `gave me the wrong`, `outdated info chatgpt`, `confidently wrong chatgpt`, `claude hallucinat`, `AI agent failed`, `chatgpt can't find`, `AI got it wrong`, `chatgpt doesn't know current`, `chatgpt made up a`, `ai doesn't have access to real-time`, `chatgpt gave me fake`, `wrong address chatgpt`, `asked chatgpt to book`, `chatgpt can't check`, `gpt hallucinated`, `chatgpt confidently`, `ai can't do`.
- Reddit `search.rss` (subreddit-scoped anonymous endpoint — `old.reddit.com/.json`, `www.reddit.com/search.json`, and Pushshift all returned 403 in practice), 8 subreddit×query combos across r/ChatGPT, r/ClaudeAI, r/LocalLLaMA, r/artificial, r/OpenAI: `useless for`, `got it wrong`, `hallucinat`, `confidently wrong`, `made up a`, `can't find`.

**Coverage:** HN comments span 2023-03-29 to 2026-03-05. Reddit posts span 2026-02-05 to 2026-06-20 (Reddit's RSS search only surfaces recent results; older Reddit complaints are underrepresented as a result — a known gap, not a silent one).

**Volume:** 25 total candidates scanned in detail (17 HN + 8 Reddit) after an initial density check across ~7-18 queries each (nbHits ranging 99-436 on HN). 18 kept, 7 rejected.

**Filter bar applied:** kept only entries with a concrete named task (what the user asked for) AND a concrete stated failure (what went wrong, was fabricated, or was lost) verifiable from the quote itself. Rejected 7: truncated quotes reading as success, success stories with a distrust footnote, generic non-first-person commentary, vague titles with no named domain/facts, and one arithmetic pun with no real task (full list in `filtered_failures.json`'s `rejected[]` array).

**Known limitation:** Reddit's post body and comment text are blocked to anonymous access (403) on every endpoint tried (`old.reddit.com/.json`, `www.reddit.com/search.json`, Pushshift, redlib mirror, per-post `.json`/`.rss`). The 5 Reddit entries in this catalog use post **titles** as the verbatim user quote — sufficient to identify task + failure for all 5, but shallower than the full first-person narrative available from HN comments. Flagged per-entry above as "title-only evidence."

**Balance check (verified against raw data, corrected):** entries span both praise-heavy threads (e.g., HN #40351302, #42342642 — user impressed by a later correct answer) and pure-complaint threads, and multiple model vendors (12/18 mention ChatGPT/GPT-4/4o explicitly, 3/18 Claude, 1/18 Google AI search, 2/18 unspecified/local model). Actual source spread is 3 communities, not 5: Hacker News (13 entries) plus 2 of the 5 targeted subreddits — r/LocalLLaMA (2) and r/ClaudeAI (3). r/ChatGPT, r/artificial, and r/OpenAI were queried (see search combos above) but yielded no entries that passed the concrete-task+concrete-failure filter bar, and Reddit's RSS-only access (see "Known limitation" below) further narrowed what was retrievable. Net: this catalog is HN-and-ChatGPT-heavy by data availability, not by design — a real skew worth accounting for when using it as a test-goal source, not a "balanced across 5 communities" claim.

**Source data files:** `filtered_failures.json` (18 kept + 7 rejected entries with full metadata), `analysis.json` (root-cause taxonomy, pattern families, per-entry orchestration requirements), `hn_results.json` / `reddit_results.json` (raw pre-filter candidate sets).
