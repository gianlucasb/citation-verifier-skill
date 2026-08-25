---
name: citation-check
description: >-
  Verify that claims made about cited works in a LaTeX paper are actually
  supported by those works. Resolves \cite keys against the .bib, fetches the
  referenced paper's full text, and reports whether each claim is supported,
  overstated, misattributed, or contradicted. Use this whenever the user
  mentions checking, verifying, or auditing citations or references; asks
  whether a citation is accurate or "says what I think it says"; uploads or
  points to a .tex or .bib file in a claims-checking context; is preparing a
  paper for submission, camera-ready, or rebuttal; asks to fact-check
  related-work or background sections; or asks whether a reference is real,
  resolvable, or out of date. Use it even if they don't say
  "citation" — "does [key] actually show that?" is this skill.
---

# Citation Check

Verify that what a paper *claims* about a reference matches what that
reference *actually says*.

The failure this catches is rarely a fabricated citation. It is drift: a
number that shifted, a result that got generalized past its evaluation, an
attack attributed to the wrong paper, or a claim inherited from someone
else's related-work section without anyone reading the original. Reviewers
catch these, and they cost credibility out of proportion to their size.

## Workflow

### 1. Locate the inputs

Find the `.tex` and `.bib` files. If the user pointed at a specific claim,
citation key, or section, scope to that. Otherwise ask what to cover before
processing a whole bibliography — full-text verification is slow, and a
90-reference paper is a long run the user may want to narrow.

### 2. Extract citations

The extractor lives beside this file, so invoke it by its path relative to
the skill directory — that location differs by surface (`~/.claude/skills/`
or `.claude/skills/` in Claude Code, a mounted skills path elsewhere), so
resolve it rather than assuming the working directory:

```bash
python3 <skill-dir>/scripts/extract_citations.py PAPER.tex --bib REFS.bib
```

Useful flags: `--key KEY` (repeatable) to scope to specific references,
`--section NAME` to scope to a section, `--follow-inputs` for multi-file
papers built from a `main.tex` with `\input`.

Output is JSON per citation site: the claim sentence, preceding context,
section, bib metadata, and locators (DOI, arXiv ID, IACR ePrint ID, URL).

Two lists in the output need acting on before any claim checking:

- `keys_missing_from_bib` — an unresolvable key is a compile error waiting to
  happen and needs no further verification. Report immediately.
- `keys_needing_published_version_check` — entries whose only venue is a
  preprint server. Run the check in step 4b on each.

### 2b. Pre-flight: confirm the sources can be read

**Run this before verifying anything.** A source you cannot read is not a
citation that passed — it is a citation nobody checked, and it looks
identical to a clean one in the final report unless it is surfaced
deliberately.

```bash
python3 <skill-dir>/scripts/extract_citations.py PAPER.tex --bib REFS.bib \
  | python3 <skill-dir>/scripts/check_access.py --library ~/Zotero/storage
```

It groups every key into four buckets and names, for each one it cannot get,
what to search for and the exact path to save the PDF to:

- **READY** — already on disk, an open-access PDF confirmed reachable, or —
  for a `@misc`/`@online`/`@software` entry cited by URL — the web page
  itself, which for a statute, news article or tool *is* the source.
  Where a local path is given, read that file instead of fetching. A path
  marked `[probable]` was matched on title words, so confirm the title and
  authors before trusting it.
- **TOOL FETCH** — bash has no outbound network on this surface, so nothing
  could be probed. No manual action needed; fetch these with the fetch tool
  during step 4 as usual.
- **SEARCH** — the `.bib` entry carries no DOI, arXiv ID or URL, so there was
  nothing to probe. Find these by title during step 4 as usual, and report
  any you genuinely cannot find as NOT LOCATED. Not a blocker: a plain
  `@inproceedings` with only a title, author and venue is normal.
- **UNCERTAIN** — reachable, but not confirmed full text: a landing page
  rather than a PDF, or a probe refused with 403 by a host that is not a
  known publisher, which is usually bot blocking. Try these yourself before
  asking the user; often the PDF is one link away.
- **NEEDS YOU** — paywalled at a publisher, a dead locator, unreachable, or
  the key is missing from the `.bib` entirely. These need the user.

Exit status is 3 when the NEEDS YOU bucket is non-empty and 0 otherwise, so
it is a reliable gate: **do not start verifying while it exits 3.**

Instead, show the user that bucket — the titles, what was tried, and the save
paths — and ask whether to wait for the PDFs or to proceed on the readable
subset. Both answers are fine; silently picking the second is not. If they
choose to proceed, every skipped key still appears in the final report as
UNVERIFIED.

Two flags matter: `--library DIR` (repeatable) points at a reference library
the user already has, and `--cache-dir DIR` sets where manually fetched PDFs
go. The cache is the contract offered to the user — a PDF saved as
`<cache-dir>/<key>.pdf` is picked up verbatim on the next run, so re-run the
check after they add files rather than asking twice.

A 403 is reported as paywalled but may be bot blocking; the fetch tool or a
browser sometimes gets through where the probe did not. Worth one attempt
before reporting it to the user as unobtainable.

### 3. Identify the actual claim

The extracted `claim_sentence` is a starting point, not the answer. Read it
alongside `preceding_context` and decide what assertion is really being
attributed to the reference. Common patterns that need care:

- **The claim precedes the citation.** "Cache attacks recover AES keys in
  seconds. This was first shown by \cite{x}." The assertion is in the prior
  sentence.
- **Multi-key citations.** When `co_cited_with` is non-empty, the claim may
  be supported jointly, or by only one of the cited works. Check whether the
  specific paper supports it, and say which one carries it.
- **Bare citations.** `\cite{x}` after "see also" or in a list of prior work
  makes no verifiable claim. Mark these NO CLAIM and move on — don't
  manufacture an assertion to evaluate.
- **Locator arguments.** If `locator_arg` names a section or page, the claim
  is about that specific part. Verify against it.

### 4. Fetch the paper

**Fetch with whatever network access this environment actually has.** The
constraint differs by surface, and guessing wrong wastes a turn on a
confusing failure:

- Where bash networking is restricted to package registries (claude.ai and
  similar sandboxes), shell fetching fails — `curl https://arxiv.org` will
  not work. Use the fetch tool, with PDF text extraction for PDFs.
- Where bash has ordinary network access (Claude Code and local runs), shell
  fetching works and is faster for bulk verification across a large
  bibliography. Scripting the retrieval of many PDFs at once is reasonable
  there.

If unsure which applies, try the fetch tool first: it works on every surface,
and a failed `curl` is a slower way to learn the answer.

Try in this order — for security venues specifically:

1. **arXiv** — `https://arxiv.org/abs/ID` for the abstract, then
   `https://arxiv.org/pdf/ID` for full text.
2. **IACR ePrint** — `https://eprint.iacr.org/YYYY/NNN.pdf`. Fully open, and
   the primary venue for crypto and much of systems security.
3. **USENIX** — open access, all years. Search `usenix.org` for the title.
4. **NDSS** — open access via `ndss-symposium.org`.
5. **DOI** — `https://doi.org/DOI`. Often lands on a paywall for IEEE S&P
   and ACM CCS.
6. **Web search on the exact title** — authors' institutional pages host
   PDFs of paywalled papers very often.

Confirm you fetched the right paper before reading it: match title and
author list against the bib entry. Landing on a different paper with a
similar title is a real failure mode and produces confidently wrong verdicts.

**Version matters.** If the bib cites a published version but you only
obtained a preprint, say so explicitly in the finding. Numbers and claims
routinely change between arXiv v1 and camera-ready, and a verdict based on
the wrong version is worse than no verdict.

**Separate "can't read it" from "can't find it."** These are different
findings and must not collapse into one bucket:

- The work is indexed — you can confirm title, authors, and venue in ACM DL,
  IEEE Xplore, DBLP, Semantic Scholar, ACL Anthology, or the publisher — but
  the full text is behind a paywall and no preprint exists. Mark UNVERIFIED,
  say what you tried, and ask the user for the PDF — an upload, or a local
  path where the filesystem is available.
- Searching the exact title, then the title without subtitle, then
  distinctive author-plus-keyword combinations returns **no record of the
  work existing at all**. Mark NOT LOCATED and report it at the top of the
  findings, above every other verdict.

NOT LOCATED is the most serious thing this skill can find. A reference that
doesn't exist is a desk-reject risk, not a wording problem. It usually means
one of: a fabricated or LLM-generated citation; a garbled title or author
list; a paper withdrawn or renamed between drafts; or a venue and year that
don't correspond to anything real.

Do not conclude NOT LOCATED from one failed search. Try at least three
distinct query formulations, and check DBLP by author name, since a garbled
title with correct authors will surface there. State plainly which searches
you ran, so the user can judge whether the work is missing or merely obscure.
Never guess at what the reference "probably" is — report the gap and let the
user resolve it.

Ideally the pre-flight in step 2b already surfaced these and the user has
decided what to do, so nothing here is a surprise. Where a source turns out
to be unobtainable only at this stage, collect those cases and ask for the
PDFs once, rather than interrupting per-paper — and name the same
`<cache-dir>/<key>.pdf` paths the pre-flight uses, so a re-run picks them up.

Where a local filesystem is available, check whether the user already has a
reference library (a Zotero storage directory, a `papers/` folder beside the
manuscript) before asking; `check_access.py --library DIR` does this
mechanically.

### 4b. Check whether preprints have since been published

For every key in `keys_needing_published_version_check`, look for a
peer-reviewed version. Papers routinely appear at a venue months or years
after the preprint, and after the citing author first added the entry.

Where to look, in order:

1. **The arXiv abstract page.** Authors often add a `Journal reference` or a
   `Related DOI` field on publication. If present, that settles it.
2. **DBLP by author name.** Fastest way to see every version of a work.
3. **A title search.** Published versions frequently rename slightly —
   subtitles get dropped, questions become statements.
4. **Semantic Scholar or Google Scholar**, which cluster preprint and
   published versions of the same work.

If a published version exists, report it as OUTDATED VENUE and supply the
corrected BibTeX. Note that a published version means the preprint's numbers
may be superseded, so any claim verified against the preprint should be
re-checked against the published text.

If no published version exists, say so explicitly rather than staying silent
— "still preprint-only as of <date>" tells the user the check ran. A preprint
that has stayed a preprint for several years is worth a passing mention, as
some reviewers weigh unrefereed citations differently.

Absence of a published version is weak evidence, not proof: indexing lags,
and some venues are poorly covered. Don't assert a work was never published;
say you found no published version and name where you looked.

### 5. Verify the claim

Read the paper for the specific assertion. Prioritise abstract, intro
contributions list, the relevant results section, and any explicit
limitations or threat-model section. Locate the passage that bears on the
claim, then assign a verdict.

## Verdicts

| Verdict | Meaning |
|---|---|
| **SUPPORTED** | The paper states or demonstrates this. |
| **OVERSTATED** | Directionally right, but the paper's result is narrower — one dataset, one configuration, a bound rather than a guarantee. |
| **MISATTRIBUTED** | The claim is true, but this paper isn't its source. Name the paper that is, if identifiable. |
| **THREAT-MODEL MISMATCH** | The result holds only under assumptions the citing sentence doesn't carry — co-located attacker, known plaintext, disabled mitigation, physical access. |
| **NOT FOUND** | The paper doesn't address this. Distinct from contradiction. |
| **CONTRADICTED** | The paper says something incompatible with the claim. |
| **NO CLAIM** | Bare citation, nothing to verify. |
| **UNVERIFIED** | Work confirmed to exist, but full text unobtainable. State what was tried. |
| **NOT LOCATED** | No record of the work found at all. Report first. |
| **OUTDATED VENUE** | Cited as a preprint, but a peer-reviewed version exists. |

Threat-model mismatch deserves particular attention in security writing. A
sentence like "AES is broken by cache attacks \cite{x}" and a paper showing
key recovery against an unprotected software implementation by a co-located
attacker are not the same statement, and the gap between them is exactly
what a reviewer will name.

## Reporting

Report inline in the conversation, not to a file. Lead with what's wrong —
the user is scanning for problems, and a list that opens with twelve
SUPPORTED entries buries the one that matters.

Order: NOT LOCATED first, then CONTRADICTED and MISATTRIBUTED, then
OVERSTATED and THREAT-MODEL MISMATCH, then NOT FOUND, then OUTDATED VENUE,
then UNVERIFIED. Close with a single
line naming the count of SUPPORTED citations rather than listing them
individually.

**Account for every key.** Before reporting, reconcile the count: every
unique key the extractor found is either given a verdict or listed as
UNVERIFIED or NOT LOCATED. State the arithmetic — "47 keys: 39 verified, 6
unverified (below), 2 no-claim" — so a gap is visible as a number rather
than as an absence. A citation dropped because the PDF would not load is the
one failure this skill must never report as silence.

For each finding that isn't SUPPORTED:

```
[VERDICT] \cite{key} — Section, "short claim fragment"
  Paper says: <what the source actually establishes, in your own words>
  Gap: <the specific discrepancy>
  Suggested fix: <concrete rewrite, or a better reference>
```

Quote the source sparingly — under fifteen words, and only where exact
wording carries weight a paraphrase would lose (a stated bound, a hedge, an
explicit scope limit). Paraphrase everything else.

Always cite the evidence's location in the source paper — section number,
figure, or table — so the user can check your work in seconds. A finding
they have to re-derive is barely faster than checking it themselves.

## Judgment

**Flag uncertainty rather than resolving it silently.** If the relevant
passage is ambiguous, or if the claim depends on an interpretation the paper
neither states nor rules out, say that plainly instead of picking a verdict.
A confident wrong verdict costs the user more than an honest "unclear,"
because they'll act on it.

**Distinguish "the paper doesn't say this" from "this is false."** A claim
may be perfectly true and simply belong to a different citation. NOT FOUND
means the reference is wrong, not that the sentence is.

**Don't flag on style.** Compressed phrasing that a specialist reader would
correctly unpack is not an error. The bar is whether a reviewer could
reasonably object, not whether the sentence could theoretically be more
precise.

**Suggest fixes, don't rewrite the paper.** Offer the minimal edit that
makes the claim accurate — a qualifier, a corrected number, a swapped key.
The user decides what to adopt.
