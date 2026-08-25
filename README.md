# Citation Check

A [Claude Skill](https://code.claude.com/docs/en/skills) that checks whether
what a paper *claims* about a citation matches what the cited work *actually
says*.

It catches citation drift: a number that shifted, a result generalized past
its evaluation, an attack attributed to the wrong paper, or a claim copied
from someone else's related-work section without anyone reading the
original. Point it at a `.tex` and `.bib` file and it resolves every
`\cite`, fetches the cited paper, and reports a verdict per claim —
`SUPPORTED`, `OVERSTATED`, `MISATTRIBUTED`, `CONTRADICTED`, `NOT LOCATED`,
and more. See [`citation-check/SKILL.md`](citation-check/SKILL.md) for the
full verdict table and workflow.

Before it verifies anything it runs a **pre-flight access check**: every
cited work is probed to see whether its full text is actually readable, and
anything that isn't — paywalled, dead DOI, no locator in the `.bib` — is
reported up front, with what to search for and the exact path to save the PDF
to. A source that can't be read is a citation nobody checked, and that is
indistinguishable from a clean one unless it's surfaced deliberately.

The two bundled scripts are dependency-free (stdlib-only Python 3).
`extract_citations.py` does the mechanical part: parsing LaTeX citation
commands, matching keys against the `.bib`, and pulling out the sentence each
citation actually attaches to. `check_access.py` consumes its output and
answers "can I read all of this yet?".

## Install

Pick whichever surface you use. The two are independent — installing on one
doesn't install on the other.

### Claude Code

**Option A — plugin marketplace (recommended):**

```
/plugin marketplace add gianlucasb/citation-verifier-skill
/plugin install citation-check@citation-verifier-skill
```

Update later with `/plugin marketplace update` followed by
`/plugin update citation-check@citation-verifier-skill`.

**Option B — manual, no plugin system:**

```bash
git clone https://github.com/gianlucasb/citation-verifier-skill.git
ln -s "$(pwd)/citation-verifier-skill/citation-check" ~/.claude/skills/citation-check
```

Use `~/.claude/skills/` for a personal skill available in every project, or
`.claude/skills/` inside a specific repo to scope it there. Because it's a
symlink, `git pull` inside `citation-verifier-skill/` picks up updates
automatically.

### claude.ai (web / desktop)

Requires a Pro, Max, Team, or Enterprise plan with code execution enabled.

1. Download [`citation-check.skill`](citation-check.skill) from this repo
   (raw file, or attached to a [release](../../releases)).
2. In Claude, go to **Settings → Features → Skills**, click **+ → Create
   skill**, and upload the file.

Custom skills on claude.ai are private to your account and don't sync with
Claude Code — if you use both, install separately on each.

## Usage

Once installed, just ask, in either surface:

> Check the citations in `paper.tex` against `refs.bib`.

> Does `\cite{smith2019}` actually say that?

> Verify the related-work section's claims.

> Just check whether you can get all the papers first — don't verify yet.

That last one runs the pre-flight alone. It's worth doing before a long run:
it takes seconds, and it tells you what to go download before you wait on a
90-reference verification that would have skipped six of them.

Claude finds the skill from the request itself — you don't need to invoke it
by name. For a large bibliography, expect it to ask what to scope to first;
full-text verification of every reference in a 90-citation paper is a long
run.

## Requirements

Only Python 3 (standard library only, no `pip install`). Claude Code runs it
directly; claude.ai runs it inside its sandboxed code execution container
automatically.

The pre-flight needs outbound HTTPS to probe sources. Where it doesn't have
that — claude.ai's sandbox restricts bash networking to package registries —
it detects the fact, says so, and hands Claude the candidate URLs to fetch
with its own tooling instead of blaming the papers for the environment.

## Repo layout

```
citation-check/
├── .claude-plugin/plugin.json   # Claude Code plugin manifest
├── SKILL.md                     # the skill itself
└── scripts/
    ├── extract_citations.py     # citation extraction / .bib resolution
    └── check_access.py          # pre-flight: is every source readable?
.claude-plugin/marketplace.json  # lets Claude Code install this via /plugin
citation-check.skill             # prebuilt zip for the claude.ai uploader
scripts/build-skill-zip.sh       # regenerates citation-check.skill
```

If you edit anything under `citation-check/`, re-run
`scripts/build-skill-zip.sh` before committing so the prebuilt zip stays in
sync.

## Security note

Skills run with Claude's file and network access. Read
[`citation-check/SKILL.md`](citation-check/SKILL.md),
[`extract_citations.py`](citation-check/scripts/extract_citations.py) and
[`check_access.py`](citation-check/scripts/check_access.py) before
installing — auditing something this small takes a couple of minutes and is
good practice for any skill from an outside source, this one included.

## License

[MIT](LICENSE)
