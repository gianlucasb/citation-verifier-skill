#!/usr/bin/env python3
"""Pre-flight: confirm every cited work can actually be read.

Consumes the JSON that extract_citations.py emits and reports, per citation
key, whether the full text is already on disk, can be fetched right now, or
needs the user to go and get it by hand -- and in that case exactly where to
save it so that a re-run picks it up.

The point is to surface the gap *before* verification starts. A source that
cannot be read is not a citation that passed; it is a citation nobody
checked, and learning that at the end of a 90-reference run is too late to
be useful.

Dependency-free. Human-readable report on stdout, --json for the structured
form. Exits 3 when anything needs a manual fetch, so a caller cannot mistake
an incomplete run for a clean one.

Usage:
    python extract_citations.py paper.tex --bib refs.bib | python check_access.py
    python check_access.py --citations cites.json --cache-dir refs/
    python check_access.py --citations cites.json --library ~/Zotero/storage
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "citation-check-preflight/1.0 (+https://github.com/gianlucasb/citation-verifier-skill)"

# Hosts that serve a landing page to everyone and the PDF only to
# subscribers. Arriving at one of these with a 200 is not access, so it must
# not be reported as such.
PAYWALL_HOSTS = (
    "ieeexplore.ieee.org", "dl.acm.org", "portal.acm.org",
    "link.springer.com", "sciencedirect.com", "onlinelibrary.wiley.com",
    "tandfonline.com", "jstor.org", "cambridge.org", "academic.oup.com",
    "nature.com", "science.org", "dailymed.nlm.nih.gov",
)

# Words too common in paper titles to identify a PDF by. Matching on these
# would pair a key with whatever unrelated paper happens to share them.
TITLE_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "using", "via",
    "into", "over", "under", "towards", "toward", "based", "approach",
    "analysis", "study", "novel", "efficient", "robust", "practical",
    "framework", "system", "systems", "attack", "attacks", "learning",
    "networks", "network", "data", "model", "models", "secure", "security",
    "detection", "towards", "understanding", "evaluation", "empirical",
}

MAX_LIBRARY_FILES = 50000


def load_citations(path):
    """Read extract_citations.py output from a file or stdin."""
    try:
        raw = sys.stdin.read() if path == "-" else open(
            path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        sys.stderr.write("error: cannot read %s (%s)\n" % (path, e.strerror))
        sys.exit(2)
    if not raw.strip():
        sys.stderr.write("error: no citation JSON on stdin. Pipe "
                         "extract_citations.py into this, or pass "
                         "--citations FILE.\n")
        sys.exit(2)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write("error: input is not valid JSON (%s). Expected the "
                         "output of extract_citations.py.\n" % e)
        sys.exit(2)


def collapse_to_keys(data):
    """One record per unique key, carrying the metadata needed to find it."""
    by_key = {}
    for c in data.get("citations", []):
        rec = by_key.setdefault(c["key"], {
            "key": c["key"],
            "title": c.get("title"),
            "author": c.get("author"),
            "year": c.get("year"),
            "venue": c.get("venue"),
            "bib_found": c.get("bib_found", False),
            "locators": c.get("locators") or {},
            "sites": 0,
            "sections": [],
        })
        rec["sites"] += 1
        if c.get("section") and c["section"] not in rec["sections"]:
            rec["sections"].append(c["section"])
    return [by_key[k] for k in sorted(by_key)]


def candidate_urls(loc):
    """Build lookup URLs in the order SKILL.md prescribes: open venues first.

    Open-access sources come before the DOI deliberately -- a DOI usually
    redirects to a publisher landing page, which is the least useful of the
    options even when it resolves.
    """
    out = []
    if loc.get("arxiv"):
        aid = re.sub(r"^arxiv:", "", loc["arxiv"].strip(), flags=re.I)
        out.append(("arxiv", "https://arxiv.org/pdf/" + aid))
    if loc.get("iacr_eprint"):
        out.append(("iacr", "https://eprint.iacr.org/%s.pdf"
                    % loc["iacr_eprint"].strip()))
    if loc.get("url"):
        out.append(("bib-url", loc["url"].strip()))
    if loc.get("doi"):
        out.append(("doi", "https://doi.org/" + loc["doi"].strip()))
    seen, uniq = set(), []
    for label, url in out:
        if url not in seen:
            seen.add(url)
            uniq.append((label, url))
    return uniq


def title_tokens(title):
    """Distinctive lowercase words of a title, for matching PDFs on disk."""
    if not title:
        return set()
    words = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()
    return {w for w in words if len(w) > 3 and w not in TITLE_STOP}


def scan_local(records, cache_dir, libraries):
    """Look for PDFs already on disk, by cache filename then by title.

    The cache is the contract this tool offers the user: save a PDF as
    <cache-dir>/<key>.pdf and the next run finds it. Libraries are scanned
    more loosely, since a Zotero store names files by title, so those hits
    are reported as probable and left for the caller to confirm.
    """
    found = {}
    for rec in records:
        for ext in (".pdf", ".PDF"):
            p = os.path.join(cache_dir, rec["key"] + ext)
            if os.path.isfile(p):
                found[rec["key"]] = {"path": p, "confidence": "exact",
                                     "why": "cache filename matches the key"}
                break

    if not libraries:
        return found, 0

    pdfs, capped = [], False
    for lib in libraries:
        for root, _dirs, files in os.walk(os.path.expanduser(lib)):
            for fn in files:
                if fn.lower().endswith(".pdf"):
                    pdfs.append(os.path.join(root, fn))
                    if len(pdfs) >= MAX_LIBRARY_FILES:
                        capped = True
                        break
            if capped:
                break
        if capped:
            break

    index = [(os.path.splitext(os.path.basename(p))[0], p) for p in pdfs]
    for rec in records:
        if rec["key"] in found:
            continue
        klow = rec["key"].lower()
        hit = next((p for stem, p in index if stem.lower() == klow), None)
        if hit:
            found[rec["key"]] = {"path": hit, "confidence": "exact",
                                 "why": "library filename matches the key"}
            continue
        want = title_tokens(rec.get("title"))
        # Under two distinctive words there is nothing to match on, and a
        # coincidental hit is worse than no hit.
        if len(want) < 2:
            continue
        best, best_score = None, 0.0
        for stem, p in index:
            got = title_tokens(stem.replace("_", " ").replace("-", " "))
            score = len(want & got) / float(len(want))
            if score > best_score:
                best, best_score = p, score
        if best and best_score >= 0.6:
            found[rec["key"]] = {
                "path": best, "confidence": "probable",
                "why": "title words match the filename (%d%%)"
                       % round(best_score * 100)}
    return found, len(pdfs)


def probe(url, timeout, delay):
    """Fetch the first bytes of a URL and describe what came back."""
    if delay:
        time.sleep(delay)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
        "Range": "bytes=0-2047",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            head = r.read(2048)
            return {"url": url, "status": r.status, "final_url": r.geturl(),
                    "content_type": (r.headers.get("Content-Type") or "")
                    .split(";")[0].strip().lower(),
                    "is_pdf": head[:5].startswith(b"%PDF"), "error": None}
    except urllib.error.HTTPError as e:
        return {"url": url, "status": e.code, "final_url": e.geturl(),
                "content_type": "", "is_pdf": False, "error": None}
    except Exception as e:                      # URLError, timeout, bad TLS
        return {"url": url, "status": None, "final_url": url,
                "content_type": "", "is_pdf": False,
                "error": "%s: %s" % (type(e).__name__, e)}


def host_of(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_paywall_host(url):
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in PAYWALL_HOSTS)


def classify(p):
    """Turn a probe result into a status and a human explanation."""
    if p["error"]:
        return "unreachable", p["error"]
    code = p["status"]
    if code in (200, 206):
        if p["is_pdf"] or p["content_type"] == "application/pdf":
            return "open_pdf", "PDF served directly"
        if is_paywall_host(p["final_url"]):
            return "paywalled", ("landed on %s, which serves the PDF only to "
                                 "subscribers" % host_of(p["final_url"]))
        return "landing_page", ("returned %s, not a PDF -- probably an "
                                "abstract or landing page"
                                % (p["content_type"] or "HTML"))
    if code in (401, 402, 403):
        # Cloudflare and several publishers answer 403 to any non-browser
        # client, so this is not proof of a paywall.
        return "paywalled", ("HTTP %d from %s -- a paywall, or bot blocking "
                             "that a browser or the fetch tool may get past"
                             % (code, host_of(p["final_url"])))
    if code in (404, 410):
        return "not_found", "HTTP %d -- the locator in the .bib is dead" % code
    return "unreachable", "HTTP %s" % code


def resolve_one(rec, timeout, delay):
    """Probe a key's candidates, stopping at the first real full text."""
    cands = candidate_urls(rec["locators"])
    if not cands:
        return {"status": "no_locator", "note": (
            "no DOI, arXiv ID, ePrint ID or URL in the .bib entry"),
            "tried": []}
    tried, best = [], None
    for label, url in cands:
        status, note = classify(probe(url, timeout, delay))
        tried.append({"via": label, "url": url, "status": status,
                      "note": note})
        if status == "open_pdf":
            return {"status": "open_pdf", "note": note, "url": url,
                    "via": label, "tried": tried}
        rank = {"landing_page": 3, "paywalled": 2, "not_found": 1,
                "unreachable": 0}
        if best is None or rank[status] > rank[best["status"]]:
            best = {"status": status, "note": note, "url": url,
                    "via": label}
    best["tried"] = tried
    return best


def network_alive(timeout):
    """Is outbound HTTPS available at all?

    On sandboxed surfaces bash networking is restricted to package
    registries, so every probe would fail and the report would blame the
    papers for the environment. Check once and say which it is.
    """
    for url in ("https://arxiv.org/robots.txt",
                "https://eprint.iacr.org/robots.txt"):
        r = probe(url, timeout, 0)
        if r["error"] is None and r["status"] in (200, 206, 404):
            return True
    return False


def disp(path):
    """Show a path relative to cwd when that is shorter and unambiguous."""
    ap = os.path.abspath(path)
    rp = os.path.relpath(ap)
    return rp if not rp.startswith("..") else ap


def build_report(data, args):
    records = collapse_to_keys(data)
    cache_dir = args.cache_dir
    if cache_dir is None:
        src = data.get("source_file") or "."
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(src)),
                                 "citation-check-cache")

    local, n_scanned = scan_local(records, cache_dir, args.library)
    online = network_alive(args.timeout) if not args.no_network else False

    remaining = [r for r in records if r["key"] not in local]
    resolved = {}
    if online and remaining:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, args.jobs)) as pool:
            futs = {pool.submit(resolve_one, r, args.timeout, args.delay):
                    r["key"] for r in remaining}
            for f in concurrent.futures.as_completed(futs):
                resolved[futs[f]] = f.result()

    for rec in records:
        key = rec["key"]
        rec["save_as"] = os.path.join(cache_dir, key + ".pdf")
        if key in local:
            rec["access"] = dict(local[key], status="local")
        elif not rec["bib_found"]:
            rec["access"] = {"status": "no_bib_entry", "tried": [], "note": (
                "key is not in the .bib, so there is nothing to look up")}
        elif not online:
            cands = candidate_urls(rec["locators"])
            rec["access"] = {
                "status": "needs_tool_fetch" if cands else "no_locator",
                "tried": [],
                "candidates": [u for _, u in cands],
                "note": ("bash has no outbound network here; try these with "
                         "the fetch tool" if cands else
                         "no DOI, arXiv ID, ePrint ID or URL in the .bib "
                         "entry")}
        else:
            rec["access"] = resolved.get(key, {
                "status": "unreachable", "tried": [], "note": "not probed"})

    return {
        "source_file": data.get("source_file"),
        "network_from_bash": online,
        "cache_dir": cache_dir,
        "library_pdfs_scanned": n_scanned,
        "total_keys": len(records),
        "keys": records,
    }


# Buckets, in the order the report presents them. Anything not READY has to
# be visible before verification starts, but only BLOCKED needs a human:
# TOOL is work the verification step does anyway, and calling it a manual
# fetch would raise a false alarm on every sandboxed run.
READY = ("local", "open_pdf")
BLOCKED = ("paywalled", "not_found", "unreachable", "no_locator",
           "no_bib_entry")
UNCERTAIN = ("landing_page",)
TOOL = ("needs_tool_fetch",)

HOW_TO_FIND = {
    "paywalled": "search the exact title -- author institutional pages host "
                 "paywalled papers very often",
    "not_found": "the locator is dead; search the exact title, then check "
                 "DBLP by author",
    "unreachable": "retry, then search the exact title",
    "no_locator": "search the exact title, then DBLP by author name",
    "no_bib_entry": "fix or add the .bib entry first -- this key will not "
                    "compile",
    "landing_page": "open the page and look for a PDF link",
    "needs_tool_fetch": "use the fetch tool on the candidate URLs above",
}


def render_text(rep, only_missing=False):
    out = []
    W = 74
    src = rep["source_file"] or "(stdin)"
    out.append("Pre-flight access check -- %s" % src)
    out.append("%d unique citation keys" % rep["total_keys"])
    if not rep["network_from_bash"]:
        out.append("")
        out.append("! bash has no outbound network here. Nothing was probed;")
        out.append("  candidate URLs are listed for the fetch tool instead.")
    if rep["library_pdfs_scanned"]:
        out.append("scanned %d PDFs in the given libraries"
                   % rep["library_pdfs_scanned"])

    groups = {"ready": [], "uncertain": [], "tool": [], "blocked": []}
    for rec in rep["keys"]:
        st = rec["access"]["status"]
        groups["ready" if st in READY else
               "tool" if st in TOOL else
               "uncertain" if st in UNCERTAIN else "blocked"].append(rec)

    def describe(rec, indent="    "):
        a = rec["access"]
        lines = []
        head = rec["key"]
        if rec.get("title"):
            head += " -- " + rec["title"]
        lines.append("  " + head[:W])
        meta = " / ".join(x for x in (rec.get("author"), rec.get("venue"),
                                      rec.get("year")) if x)
        if meta:
            lines.append(indent + meta[:W])
        for t in a.get("tried", []):
            lines.append("%stried  %s -> %s" % (indent, t["url"], t["status"]))
            lines.append("%s       %s" % (indent, t["note"]))
        for u in a.get("candidates", []):
            lines.append("%stry    %s" % (indent, u))
        if not a.get("tried") and not a.get("candidates"):
            lines.append(indent + a["note"])
        hint = HOW_TO_FIND.get(a["status"])
        if hint:
            lines.append("%sfind   %s" % (indent, hint))
        # Only worth naming a save path for something a human has to go get.
        # A key absent from the .bib is fixed by editing the .bib, not by
        # putting a PDF anywhere.
        if a["status"] not in TOOL and a["status"] != "no_bib_entry":
            lines.append("%ssave   %s" % (indent, disp(rec["save_as"])))
        return lines

    if groups["blocked"]:
        out.append("")
        out.append("NEEDS YOU (%d) -- fetch these by hand before verifying"
                   % len(groups["blocked"]))
        for rec in groups["blocked"]:
            out.append("")
            out.extend(describe(rec))

    if groups["uncertain"]:
        out.append("")
        out.append("UNCERTAIN (%d) -- reachable, but no PDF confirmed"
                   % len(groups["uncertain"]))
        for rec in groups["uncertain"]:
            out.append("")
            out.extend(describe(rec))

    if groups["tool"]:
        out.append("")
        out.append("TOOL FETCH (%d) -- no manual action needed; fetch these "
                   "with the" % len(groups["tool"]))
        out.append("fetch tool during verification")
        for rec in groups["tool"]:
            out.append("")
            out.extend(describe(rec))

    if groups["ready"] and not only_missing:
        out.append("")
        out.append("READY (%d)" % len(groups["ready"]))
        for rec in groups["ready"]:
            a = rec["access"]
            where = a.get("path") or a.get("url") or ""
            flag = ""
            if a["status"] == "local" and a.get("confidence") == "probable":
                flag = "  [probable -- confirm title and authors match]"
            out.append("  %-24s %s%s" % (rec["key"], disp(where)
                                         if a["status"] == "local" else where,
                                         flag))

    out.append("")
    n_blocked = len(groups["blocked"])
    n_unc = len(groups["uncertain"])
    n_tool = len(groups["tool"])
    if n_blocked:
        out.append("%d of %d keys need you: fetch them by hand, save them to "
                   "%s," % (n_blocked, rep["total_keys"],
                            disp(rep["cache_dir"])))
        out.append("and re-run this check. Verifying without them would "
                   "silently skip %d citation%s."
                   % (n_blocked, "" if n_blocked == 1 else "s"))
        if n_unc or n_tool:
            out.append("A further %d still need fetching during verification."
                       % (n_unc + n_tool))
    elif n_unc or n_tool:
        out.append("Nothing needs a manual fetch. %d of %d keys still have to "
                   "be retrieved" % (n_unc + n_tool, rep["total_keys"]))
        out.append("during verification; report any that fail as UNVERIFIED "
                   "rather than omitting them.")
    else:
        out.append("All %d keys are readable. Safe to verify."
                   % rep["total_keys"])
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Check that every cited work can be read before "
                    "verification starts.")
    ap.add_argument("--citations", default="-",
                    help="extract_citations.py JSON, or - for stdin "
                         "(default)")
    ap.add_argument("--cache-dir", default=None,
                    help="where manually fetched PDFs live, named <key>.pdf "
                         "(default: citation-check-cache/ beside the .tex)")
    ap.add_argument("--library", action="append", default=[],
                    help="existing PDF directory to search, e.g. a Zotero "
                         "storage dir (repeatable)")
    ap.add_argument("--json", action="store_true",
                    help="emit the structured report instead of text")
    ap.add_argument("--only-missing", action="store_true",
                    help="omit the READY list from the text report")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--delay", type=float, default=0.2,
                    help="pause before each request, to stay polite "
                         "(default 0.2s)")
    ap.add_argument("--jobs", type=int, default=4,
                    help="concurrent probes (default 4)")
    ap.add_argument("--no-network", action="store_true",
                    help="skip probing and plan for tool-based fetching")
    args = ap.parse_args()

    rep = build_report(load_citations(args.citations), args)
    if args.json:
        json.dump(rep, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(rep, args.only_missing) + "\n")

    # Non-zero means "a human has to act", not merely "work remains": a
    # sandboxed run cannot probe anything, and exiting 3 for that would cry
    # wolf on every claude.ai invocation.
    blocked = sum(1 for r in rep["keys"]
                  if r["access"]["status"] in BLOCKED)
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
