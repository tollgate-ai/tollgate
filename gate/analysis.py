"""Read and check an analysis: .tollgate/analysis/<task id>.md.

The analyst writes it before the plan, from the code alone. It has a header and
six sections, and each fact is one line that is confirmed, specified, or assumed:

    ---
    task: retries
    commit: 3d549d7
    ---
    ## Structure          how the project is laid out
    ## Entry points       where a run starts, and the modules that matter
    ## Frameworks         the frameworks, libraries, and patterns it uses
    ## Similar features   how the code already does something like the request
    ## Tests              the test framework and how tests are written
    ## Standards          the coding standards and instructions it follows

    - confirmed: the sender lives in one module [src/send.py:1]
    - confirmed: the handler calls it [src/web.py::deliver]
    - specified: a failed send is retried 3 times [docs/design.md::Retries]
    - assumed: the project runs on Python 3.11

A confirmed fact cites the code: [path:line] or [path::name]. A fact about something
the project lacks, such as no go.mod or no tests, cites the folder that would hold it:
[./] for the project root, or [docs/]. It is confirmed when that folder exists. A specified fact is
one that the spec docs of the project state as the intended design. It cites a spec
doc: [path] or [path::heading]. verify() checks each citation against the commit the
analysis read, and makes a fact whose citation does not exist an assumption. A
citation of a specified fact counts only inside one of the spec paths.
"""

import posixpath
import re
from dataclasses import dataclass, field

from gate.paths import folder_exists, heading_exists, line_exists, name_exists, read_inside

ANALYSES = ".tollgate/analysis"
SECTIONS = ("Structure", "Entry points", "Frameworks", "Similar features", "Tests", "Standards")
STATUSES = ("confirmed", "specified", "assumed")
CITE = re.compile(r"\[([^\]\s:]+)(?::(\d+)|::([\w.]+)|(/))\]")
SPEC_CITE = re.compile(r"\[([^\]\s:]+)(?:::([^\]]+))?\]")


class AnalysisError(ValueError):
    pass


@dataclass
class Fact:
    status: str
    text: str
    # (path, line or None, name or None). For a specified fact: (path, None, heading or None).
    cites: list = field(default_factory=list)


@dataclass
class Analysis:
    task: str
    commit: str
    sections: dict = field(default_factory=dict)   # section name -> [Fact]


# A confirmed fact cites code as [path:line] or [path::name], or a folder; a specified fact
# cites a spec doc as [path] or [path::heading]. A fact that cites neither way is read as
# assumed, with this note.
NO_CITATION = "(no valid citation: a confirmed fact cites [path:line] or [path::name], a specified fact [doc] or [doc::heading])"


def analysis_path(task_id: str) -> str:
    return f"{ANALYSES}/{task_id}.md"


def _cite(c) -> str:
    path, line, name = c
    # A folder citation keeps its slash, so it prints as written.
    if line is not None:
        return f"{path}:{line}"
    return f"{path}::{name}" if name else path


def parse(text: str) -> Analysis:
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        raise AnalysisError("an analysis starts with a --- line")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise AnalysisError("the analysis header has no closing --- line") from None
    header = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep:
            header[key.strip()] = value.strip()
    for key in ("task", "commit"):
        if not header.get(key):
            raise AnalysisError(f"the analysis header needs a {key}")
    sections, current = {}, None
    for line in lines[end + 1:]:
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        item = line.strip().removeprefix("- ").strip()
        if not item or current is None:
            continue
        status, sep, claim = item.partition(":")
        if not sep or status.strip() not in STATUSES:
            raise AnalysisError(f"each fact starts with confirmed, specified, or assumed: {item}")
        status, claim = status.strip(), claim.strip()
        if status == "specified":
            cites = [(m[1], None, m[2].strip() if m[2] else None)
                     for m in SPEC_CITE.finditer(claim)]
            if not cites:
                # As verify() does with a citation it cannot find: the fact stays, as an
                # assumption, so one citation in the wrong form does not refuse the analysis.
                status, claim = "assumed", f"{claim} {NO_CITATION}"
        else:
            # A folder citation, [docs/], is kept as the path with its slash, and no line or name.
            cites = [(m[1] + "/" if m[4] else m[1], int(m[2]) if m[2] else None, m[3] or None)
                     for m in CITE.finditer(claim)]
            if status == "confirmed" and not cites:
                status, claim, cites = "assumed", f"{claim} {NO_CITATION}", []
        sections[current].append(Fact(status, claim, cites))
    missing = [s for s in SECTIONS if not sections.get(s)]
    if missing:
        raise AnalysisError(
            f"the analysis has empty or missing sections: {', '.join(missing)}. The analysis "
            "field of the json block holds the whole analysis, the header and all six sections, "
            "as one string. It is not a pointer to the block above")
    return Analysis(header["task"], header["commit"], {s: sections[s] for s in SECTIONS})


def in_spec(path: str, spec) -> bool:
    """True when path is one of the spec paths, or below one that is a folder."""
    path = posixpath.normpath(path)
    if path.startswith(("/", "../")) or path == "..":
        return False
    for entry in spec:
        entry = posixpath.normpath(entry)
        if entry == "." or path == entry or path.startswith(entry + "/"):
            return True
    return False


def _spec_cite_exists(root: str, c, spec) -> bool:
    path, _, heading = c
    if not in_spec(path, spec):
        return False
    if heading is None:
        return read_inside(root, path) is not None
    return heading_exists(root, path, heading)


def _code_cite_exists(root: str, c) -> bool:
    path, line, name = c
    if line is not None:
        return line_exists(root, path, line)
    if name is None:    # a folder citation: the path ends in a slash
        return folder_exists(root, path)
    return name_exists(root, path, name)


def verify(analysis: Analysis, root: str, spec=()) -> list:
    """Check every citation of a confirmed or specified fact against the files under root.
    A fact with a citation that does not exist becomes assumed. Return those citations.
    spec lists the paths of the spec docs, relative to root; a specified fact may cite
    only inside them."""
    missing = []
    for facts in analysis.sections.values():
        for f in facts:
            if f.status == "confirmed":
                gone = [c for c in f.cites if not _code_cite_exists(root, c)]
            elif f.status == "specified":
                gone = [c for c in f.cites if not _spec_cite_exists(root, c, spec)]
            else:
                continue
            if gone:
                f.status = "assumed"
                f.text += " (not found: " + ", ".join(_cite(c) for c in gone) + ")"
                missing += [_cite(c) for c in gone]
    return missing


def render(analysis: Analysis) -> str:
    out = ["---", f"task: {analysis.task}", f"commit: {analysis.commit}", "---"]
    for name, facts in analysis.sections.items():
        out.append(f"## {name}")
        out += [f"- {f.status}: {f.text}" for f in facts]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    """Check an analysis read on standard input against the project; print it checked."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog="python3 -m gate.analysis", description=main.__doc__)
    parser.add_argument("--root", default=".", help="the project folder")
    parser.add_argument("--spec", action="append", default=[],
                        help="a spec path, relative to the project folder; repeat for more")
    args = parser.parse_args(argv)
    try:
        found = parse(sys.stdin.read())
    except AnalysisError as err:
        print(f"The analysis cannot be read: {err}")
        return 1
    missing = verify(found, args.root, args.spec)
    print(render(found))
    print("Every citation was found." if not missing
          else f"Not found, so made assumptions: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
