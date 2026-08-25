#!/usr/bin/env python3
"""Extract citations from LaTeX source and resolve them against .bib entries.

Dependency-free. Outputs JSON to stdout.

Usage:
    python extract_citations.py paper.tex --bib refs.bib
    python extract_citations.py paper.tex --bib refs.bib --key smith2019
    python extract_citations.py main.tex --bib refs.bib --follow-inputs
"""

import argparse
import json
import os
import re
import sys

# Citation commands from natbib, biblatex, and plain LaTeX.
CITE_CMDS = [
    "citep", "citet", "citealp", "citealt", "citeauthor", "citeyearpar",
    "citeyear", "autocite", "textcite", "parencite", "footcite", "fullcite",
    "supercite", "smartcite", "cites", "Citep", "Citet", "Autocite",
    "Textcite", "Parencite", "cite",
]
CITE_RE = re.compile(
    r"\\(" + "|".join(CITE_CMDS) + r")\s*((?:\[[^\]]*\]\s*)*)\{([^}]*)\}"
)

# Abbreviations that must not be treated as sentence boundaries.
ABBREVS = [
    "et al.", "e.g.", "i.e.", "cf.", "vs.", "etc.", "Fig.", "Figs.", "Sec.",
    "Secs.", "Tab.", "Eq.", "Eqs.", "Ref.", "Refs.", "App.", "Alg.", "Ch.",
    "Dr.", "Prof.", "Mr.", "Ms.", "St.", "approx.", "resp.", "Thm.", "Def.",
    "Lem.", "Cor.", "no.", "No.", "vol.", "Vol.", "pp.", "p.",
]


def strip_comments(text):
    """Remove LaTeX comments, preserving escaped percent signs."""
    out = []
    for line in text.split("\n"):
        result, i = [], 0
        while i < len(line):
            if line[i] == "\\" and i + 1 < len(line):
                result.append(line[i:i + 2])
                i += 2
            elif line[i] == "%":
                break
            else:
                result.append(line[i])
                i += 1
        out.append("".join(result))
    return "\n".join(out)


def resolve_inputs(text, base_dir, depth=0):
    """Inline \\input and \\include files so multi-file papers work."""
    if depth > 5:
        return text

    def repl(m):
        name = m.group(2).strip()
        for cand in (name, name + ".tex"):
            path = os.path.join(base_dir, cand)
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    sub = strip_comments(f.read())
                return resolve_inputs(sub, os.path.dirname(path) or base_dir,
                                      depth + 1)
        return m.group(0)

    return re.sub(r"\\(input|include)\s*\{([^}]*)\}", repl, text)


def clean_for_reading(text):
    """Flatten LaTeX markup into readable prose for claim extraction.

    Keeps the sentence intact but removes formatting noise that would make
    the extracted claim hard to read.
    """
    text = re.sub(r"\\(begin|end)\{[^}]*\}", " ", text)
    # Headings must be dropped whole; otherwise the title glues onto the
    # following sentence and corrupts the extracted claim.
    text = re.sub(r"\\(section|subsection|subsubsection|paragraph)\s*\*?\s*"
                  r"\{[^}]*\}", " ", text)
    # Citation keys are not prose. Replace the whole command with a marker.
    text = CITE_RE.sub(" [CIT] ", text)
    text = re.sub(r"\\(label|ref|eqref|cref|Cref|autoref)\s*\{[^}]*\}", "", text)
    text = re.sub(r"\\(emph|textit|textbf|texttt|textsc|text)\s*\{([^}]*)\}",
                  r"\2", text)
    text = re.sub(r"\\[a-zA-Z]+\s*\*?", " ", text)
    text = text.replace("~", " ")
    for esc in ("&", "%", "$", "#", "_"):
        text = text.replace("\\" + esc, esc)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return text.strip()


def split_sentences(text):
    """Split into sentences, protecting known abbreviations."""
    protected = text
    for i, ab in enumerate(ABBREVS):
        protected = protected.replace(ab, f"\x00{i}\x00")
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\\(])", protected)
    restored = []
    for p in parts:
        for i, ab in enumerate(ABBREVS):
            p = p.replace(f"\x00{i}\x00", ab)
        if p.strip():
            restored.append(p.strip())
    return restored


def extract_context(text, start, end, window=1200):
    """Return the sentence containing the citation plus preceding sentences.

    The claim frequently lives in the sentence *before* the one carrying the
    \\cite, so preceding context is included deliberately.
    """
    left = max(0, start - window)
    before = clean_for_reading(text[left:start])
    after = clean_for_reading(text[end:min(len(text), end + 400)])

    before_sents = split_sentences(before)
    after_sents = split_sentences(after)

    claim = before_sents[-1] if before_sents else ""
    if after_sents:
        # The citation may sit mid-sentence; append the remainder.
        tail = after_sents[0]
        if not claim.endswith((".", "!", "?")):
            claim = (claim + " " + tail).strip()

    prior = before_sents[-3:-1] if len(before_sents) > 1 else []
    claim = re.sub(r"\s+([.,;:!?])", r"\1", claim).strip()
    return {
        "claim_sentence": claim,
        "preceding_context": " ".join(prior),
    }


def find_section(text, pos):
    """Identify the nearest preceding \\section or \\subsection heading."""
    heads = list(re.finditer(
        r"\\(section|subsection|subsubsection)\s*\*?\s*\{([^}]*)\}",
        text[:pos]))
    return clean_for_reading(heads[-1].group(2)) if heads else "(no section)"


def parse_bib(path):
    """Minimal brace-balanced BibTeX parser. Returns {key: {field: value}}."""
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    entries = {}
    for m in re.finditer(r"@(\w+)\s*\{", content):
        etype = m.group(1).lower()
        if etype in ("comment", "preamble", "string"):
            continue
        i, depth = m.end(), 1
        while i < len(content) and depth:
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
            i += 1
        body = content[m.end():i - 1]

        comma = body.find(",")
        if comma == -1:
            continue
        key = body[:comma].strip()
        fields = {"entry_type": etype}

        pos = comma + 1
        while pos < len(body):
            fm = re.match(r"\s*([a-zA-Z\-_]+)\s*=\s*", body[pos:])
            if not fm:
                break
            name = fm.group(1).lower()
            vstart = pos + fm.end()
            if vstart >= len(body):
                break
            if body[vstart] in "{\"":
                opener = body[vstart]
                closer = "}" if opener == "{" else "\""
                j, d = vstart + 1, 1
                while j < len(body) and d:
                    if opener == "{":
                        if body[j] == "{":
                            d += 1
                        elif body[j] == "}":
                            d -= 1
                    elif body[j] == closer and body[j - 1] != "\\":
                        d = 0
                    j += 1
                value = body[vstart + 1:j - 1]
                pos = j
            else:
                j = vstart
                while j < len(body) and body[j] != ",":
                    j += 1
                value = body[vstart:j]
                pos = j
            fields[name] = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
            while pos < len(body) and body[pos] in " ,\n\t":
                pos += 1
        entries[key] = fields
    return entries


# Words that mean "preprint" when they appear in a venue field.
PREPRINT_WORDS = ("arxiv", "corr", "preprint", "biorxiv", "medrxiv",
                  "ssrn", "openreview", "techrxiv", "under review",
                  "submitted to")
# Preprint-server hosts. Deliberately excludes code hosts and doc sites, which
# are legitimate permanent homes for software and documentation citations.
PREPRINT_HOSTS = ("arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com",
                  "openreview.net", "eprint.iacr.org", "techrxiv.org")


def preprint_status(entry):
    """Flag entries whose only venue is a preprint server.

    Such a work may since have appeared at a peer-reviewed venue, in which
    case the preprint's numbers may be superseded. Software, documentation,
    and web resources are NOT preprints and are not flagged -- they have no
    published version to find, so flagging them is noise.
    """
    if not entry:
        return None

    # The venue is whatever booktitle/journal says. archiveprefix and eprint
    # are just identifiers: a published paper routinely carries an arXiv ID
    # alongside its real venue, and that must not read as "preprint".
    venue = " ".join(str(entry.get(f, ""))
                     for f in ("journal", "booktitle", "series")).lower()
    if venue and not any(w in venue for w in PREPRINT_WORDS):
        return "published"

    urls = " ".join(str(entry.get(f, "")) for f in
                    ("url", "howpublished", "note")).lower()
    if (any(w in venue for w in PREPRINT_WORDS)
            or any(h in urls for h in PREPRINT_HOSTS)
            or bool(entry.get("eprint"))):
        return "preprint_only"
    return "other"


def clean_field(value):
    """Normalise a BibTeX field for display."""
    if not value:
        return None
    value = value.replace("\\&", "&").replace("--", "-")
    value = re.sub(r"[{}]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def first_url(entry):
    """Pull a URL out of url, howpublished or note, in that order.

    Hand-written entries routinely put the link in howpublished as
    \\url{...} and leave the url field empty, so reading only the url field
    misses them -- and a locator that is present but unread looks exactly
    like a reference with no locator at all.
    """
    for f in ("url", "howpublished", "note"):
        v = str(entry.get(f, ""))
        if not v:
            continue
        m = re.search(r"\\url\s*\{([^}]+)\}", v)
        if m:
            return m.group(1).strip()
        m = re.search(r"https?://[^\s,}{]+", v)
        if m:
            return m.group(0).strip().rstrip(".,;")
    return None


def build_locators(entry):
    """Derive lookup handles: DOI, arXiv ID, IACR ePrint ID, URL."""
    if not entry:
        return {}
    loc = {}
    if entry.get("doi"):
        loc["doi"] = entry["doi"].replace("https://doi.org/", "").strip()

    eprint = entry.get("eprint", "")
    prefix = entry.get("archiveprefix", entry.get("eprinttype", "")).lower()
    blob = " ".join(str(v) for v in entry.values())

    if eprint and ("arxiv" in prefix or re.match(r"^\d{4}\.\d{4,5}", eprint)):
        loc["arxiv"] = eprint.strip()
    else:
        am = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s,}{]+)", blob, re.I)
        if am:
            loc["arxiv"] = am.group(1).replace(".pdf", "")

    # A bare "arXiv:2401.01234" in howpublished or note names no host, so the
    # arxiv.org pattern above cannot see it.
    if "arxiv" not in loc:
        am = re.search(r"arxiv\s*:\s*(\d{4}\.\d{4,5}(?:v\d+)?)", blob, re.I)
        if am:
            loc["arxiv"] = am.group(1)

    im = re.search(r"eprint\.iacr\.org/(\d{4}/\d+)", blob, re.I)
    if im:
        loc["iacr_eprint"] = im.group(1)
    elif eprint and "iacr" in prefix:
        loc["iacr_eprint"] = eprint.strip()

    url = first_url(entry)
    if url:
        loc["url"] = url
    return loc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tex")
    ap.add_argument("--bib", action="append", default=[],
                    help="Path to .bib file (repeatable)")
    ap.add_argument("--key", action="append", default=[],
                    help="Only report these citation keys")
    ap.add_argument("--section", help="Only citations under this section")
    ap.add_argument("--follow-inputs", action="store_true",
                    help="Inline \\input and \\include files")
    args = ap.parse_args()

    with open(args.tex, encoding="utf-8", errors="replace") as f:
        text = strip_comments(f.read())
    if args.follow_inputs:
        text = resolve_inputs(text, os.path.dirname(os.path.abspath(args.tex)))

    bib = {}
    for b in args.bib:
        if os.path.isfile(b):
            bib.update(parse_bib(b))

    results, seen = [], set()
    for m in CITE_RE.finditer(text):
        keys = [k.strip() for k in m.group(3).split(",") if k.strip()]
        optargs = m.group(2).strip()
        section = find_section(text, m.start())
        if args.section and args.section.lower() not in section.lower():
            continue
        ctx = extract_context(text, m.start(), m.end())

        for key in keys:
            if args.key and key not in args.key:
                continue
            sig = (key, ctx["claim_sentence"][:90])
            if sig in seen:
                continue
            seen.add(sig)
            entry = bib.get(key)
            results.append({
                "key": key,
                "command": m.group(1),
                "locator_arg": optargs or None,
                "co_cited_with": [k for k in keys if k != key],
                "section": section,
                "claim_sentence": ctx["claim_sentence"],
                "preceding_context": ctx["preceding_context"],
                "bib_found": entry is not None,
                "title": clean_field(entry.get("title")) if entry else None,
                "author": clean_field(entry.get("author")) if entry else None,
                "year": clean_field(entry.get("year")) if entry else None,
                "venue": clean_field(
                    entry.get("booktitle") or entry.get("journal")
                    or entry.get("publisher")) if entry else None,
                "locators": build_locators(entry),
                "venue_status": preprint_status(entry),
            })

    missing = sorted({r["key"] for r in results if not r["bib_found"]})
    preprints = sorted({r["key"] for r in results
                        if r.get("venue_status") == "preprint_only"})
    json.dump({
        "source_file": args.tex,
        "total_citation_sites": len(results),
        "unique_keys": len({r["key"] for r in results}),
        "keys_missing_from_bib": missing,
        "keys_needing_published_version_check": preprints,
        "citations": results,
    }, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
