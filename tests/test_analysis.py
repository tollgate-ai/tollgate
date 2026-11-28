"""Tests for the analysis format and the check of its citations."""

import tempfile
import unittest
from pathlib import Path

from gate import analysis as an
from tests.helpers import within

TEXT = """---
task: retries
commit: 3d549d7
---
## Structure
- confirmed: the sender lives in one module [src/send.py:1]
## Entry points
- confirmed: the web handler calls send [src/web.py::deliver]
## Frameworks
- assumed: the project runs on Python 3.11
## Similar features
- confirmed: timeouts are retried the same way [src/send.py:3]
## Tests
- confirmed: tests use unittest [tests/test_send.py:1]
## Standards
- assumed: no written standards beyond the README
"""


def project():
    d = tempfile.mkdtemp()
    Path(d, "src").mkdir()
    Path(d, "tests").mkdir()
    Path(d, "src/send.py").write_text("def send():\n    pass\n# retry on timeout\n")
    Path(d, "src/web.py").write_text("def deliver(req):\n    return send()\n")
    Path(d, "tests/test_send.py").write_text("import unittest\n")
    return d


class FormatTest(unittest.TestCase):
    def test_reads_the_six_sections(self):
        a = an.parse(TEXT)
        self.assertEqual((a.task, a.commit), ("retries", "3d549d7"))
        self.assertEqual(list(a.sections), list(an.SECTIONS))
        first = a.sections["Structure"][0]
        self.assertEqual((first.status, first.cites), ("confirmed", [("src/send.py", 1, None)]))
        self.assertEqual(a.sections["Entry points"][0].cites, [("src/web.py", None, "deliver")])

    def test_refuses_a_missing_section(self):
        with self.assertRaisesRegex(an.AnalysisError, "Tests"):
            an.parse(TEXT.replace("## Tests\n- confirmed: tests use unittest [tests/test_send.py:1]\n", ""))

    def test_refuses_a_header_with_no_commit(self):
        with self.assertRaisesRegex(an.AnalysisError, "commit"):
            an.parse(TEXT.replace("commit: 3d549d7\n", ""))

    def test_each_finding_is_confirmed_or_assumed(self):
        with self.assertRaisesRegex(an.AnalysisError, "confirmed, specified, or assumed: the sender"):
            an.parse(TEXT.replace("- confirmed: the sender", "- the sender"))

    def test_a_confirmed_finding_with_no_citation_becomes_assumed(self):
        fact = an.parse(TEXT.replace(" [tests/test_send.py:1]", "")).sections["Tests"][0]
        self.assertEqual(fact.status, "assumed")
        self.assertIn("tests use unittest (no valid citation", fact.text)

    def test_a_long_broken_citation_is_read_quickly(self):
        broken = "- confirmed: tests use unittest [" + "!/" * 40 + "\n"
        with within(2):
            a = an.parse(TEXT.replace("- confirmed: tests use unittest [tests/test_send.py:1]\n", broken))
        self.assertEqual(a.sections["Tests"][0].status, "assumed")


SPEC_TEXT = TEXT.replace(
    "- assumed: the project runs on Python 3.11",
    "- specified: a failed send is retried 3 times [docs/design.md::Retries]\n"
    "- specified: the design has one module [docs/design.md]\n"
    "- assumed: the project runs on Python 3.11")


def spec_project():
    d = project()
    Path(d, "docs").mkdir()
    Path(d, "docs/design.md").write_text("# Design\n\nText.\n\n## Retries \n\nThree.\n")
    Path(d, "notes.md").write_text("# Retries\n")
    return d


class SpecifiedTest(unittest.TestCase):
    def test_reads_a_specified_fact_with_a_doc_or_a_heading(self):
        facts = an.parse(SPEC_TEXT).sections["Frameworks"]
        self.assertEqual([f.status for f in facts], ["specified", "specified", "assumed"])
        self.assertEqual(facts[0].cites, [("docs/design.md", None, "Retries")])
        self.assertEqual(facts[1].cites, [("docs/design.md", None, None)])

    def test_a_heading_may_hold_spaces(self):
        a = an.parse(SPEC_TEXT.replace("::Retries]", "::How a send is retried]"))
        self.assertEqual(a.sections["Frameworks"][0].cites,
                         [("docs/design.md", None, "How a send is retried")])

    def test_a_specified_fact_cites_a_spec_doc(self):
        for bad in ("- specified: a send is retried", "- specified: a send is retried [src/send.py:1]"):
            with self.subTest(bad=bad):
                fact = an.parse(SPEC_TEXT.replace(
                    "- specified: a failed send is retried 3 times [docs/design.md::Retries]",
                    bad)).sections["Frameworks"][0]
                self.assertEqual(fact.status, "assumed")
                self.assertIn("(no valid citation", fact.text)

    def test_every_citation_that_exists_stays_specified(self):
        a = an.parse(SPEC_TEXT)
        self.assertEqual(an.verify(a, spec_project(), ["docs/"]), [])
        self.assertEqual([f.status for f in a.sections["Frameworks"]],
                         ["specified", "specified", "assumed"])

    def test_a_spec_path_may_name_the_file_itself(self):
        a = an.parse(SPEC_TEXT)
        self.assertEqual(an.verify(a, spec_project(), ["docs/design.md"]), [])

    def test_a_heading_of_any_level_matches_after_trimming(self):
        root = spec_project()
        for heading in ("Design", "Retries"):
            a = an.parse(SPEC_TEXT.replace("::Retries]", f"::{heading}]"))
            self.assertEqual(an.verify(a, root, ["docs"]), [], heading)

    def test_a_missing_heading_makes_the_fact_assumed(self):
        a = an.parse(SPEC_TEXT.replace("::Retries]", "::Timeouts]"))
        self.assertEqual(an.verify(a, spec_project(), ["docs"]), ["docs/design.md::Timeouts"])
        fact = a.sections["Frameworks"][0]
        self.assertEqual(fact.status, "assumed")
        self.assertIn("(not found: docs/design.md::Timeouts)", fact.text)

    def test_a_heading_text_in_the_body_is_not_a_heading(self):
        a = an.parse(SPEC_TEXT.replace("::Retries]", "::Text.]"))
        self.assertEqual(an.verify(a, spec_project(), ["docs"]), ["docs/design.md::Text."])

    def test_a_heading_inside_a_fenced_block_is_not_a_heading(self):
        from gate import paths
        root = tempfile.mkdtemp()
        text = ("# Real\n\n```sh\n# Comment\n```\n\n~~~\n# Tilde\n~~~\n\n"
                "````md\n# Outer\n```\n# Still outer\n```\n````\n\n"
                "```\n# Not closed by a tilde\n~~~\n# Still in\n```\n\n## After\n")
        Path(root, "d.md").write_text(text)
        for heading, found in (("Real", True), ("After", True), ("Comment", False),
                               ("Tilde", False), ("Outer", False), ("Still outer", False),
                               ("Not closed by a tilde", False), ("Still in", False)):
            self.assertIs(paths.heading_exists(root, "d.md", heading), found, heading)

    def test_a_fence_that_never_closes_hides_the_rest_of_the_file(self):
        from gate import paths
        root = tempfile.mkdtemp()
        Path(root, "d.md").write_text("# Top\n\n```\n# Lost\n")
        self.assertTrue(paths.heading_exists(root, "d.md", "Top"))
        self.assertFalse(paths.heading_exists(root, "d.md", "Lost"))

    def test_a_missing_doc_makes_the_fact_assumed(self):
        a = an.parse(SPEC_TEXT.replace("[docs/design.md]", "[docs/gone.md]"))
        self.assertEqual(an.verify(a, spec_project(), ["docs"]), ["docs/gone.md"])
        self.assertEqual(a.sections["Frameworks"][1].status, "assumed")

    def test_a_doc_outside_the_spec_paths_does_not_count(self):
        a = an.parse(SPEC_TEXT.replace("[docs/design.md::Retries]", "[notes.md::Retries]"))
        self.assertEqual(an.verify(a, spec_project(), ["docs"]), ["notes.md::Retries"])
        self.assertEqual(a.sections["Frameworks"][0].status, "assumed")

    def test_a_folder_name_that_only_starts_the_same_does_not_count(self):
        a = an.parse(SPEC_TEXT)
        self.assertEqual(an.verify(a, spec_project(), ["doc"]),
                         ["docs/design.md::Retries", "docs/design.md"])

    def test_with_no_spec_paths_a_specified_fact_becomes_assumed(self):
        a = an.parse(SPEC_TEXT)
        self.assertEqual(len(an.verify(a, spec_project())), 2)
        self.assertEqual([f.status for f in a.sections["Frameworks"]], ["assumed"] * 3)

    def test_a_citation_cannot_leave_the_project(self):
        a = an.parse(SPEC_TEXT.replace("[docs/design.md]", "[../design.md]"))
        self.assertEqual(an.verify(a, spec_project(), ["docs", ".."]), ["../design.md"])

    def test_confirmed_and_assumed_facts_are_not_checked_against_the_spec(self):
        a = an.parse(TEXT)
        self.assertEqual(an.verify(a, project(), ["docs"]), [])
        self.assertTrue(all(f.status == "confirmed" for f in a.sections["Structure"]))

    def test_render_reads_back_the_same(self):
        a = an.parse(SPEC_TEXT)
        self.assertEqual(an.render(an.parse(an.render(a))), an.render(a))
        self.assertIn("- specified: the design has one module [docs/design.md]", an.render(a))

    def test_the_command_takes_the_spec_paths(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        root = spec_project()
        for spec, expect in ((["--spec", "docs"], "Every citation was found."),
                             ([], "Not found, so made assumptions")):
            out = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO(SPEC_TEXT)), redirect_stdout(out):
                self.assertEqual(an.main(["--root", root, *spec]), 0)
            self.assertIn(expect, out.getvalue())


class VerifyTest(unittest.TestCase):
    def test_every_citation_that_exists_stays_confirmed(self):
        a = an.parse(TEXT)
        self.assertEqual(an.verify(a, project()), [])
        self.assertTrue(all(f.status == "confirmed" for f in a.sections["Structure"]))

    def test_a_citation_that_does_not_exist_makes_the_finding_assumed(self):
        bad = (TEXT.replace("[src/send.py:1]", "[src/send.py:40]")
               .replace("[src/web.py::deliver]", "[src/web.py::receive]")
               .replace("[tests/test_send.py:1]", "[tests/test_gone.py:1]"))
        a = an.parse(bad)
        demoted = an.verify(a, project())
        self.assertEqual(demoted, ["src/send.py:40", "src/web.py::receive", "tests/test_gone.py:1"])
        self.assertEqual(a.sections["Structure"][0].status, "assumed")
        self.assertIn("not found: src/send.py:40", an.render(a))

    def test_a_citation_cannot_leave_the_project(self):
        a = an.parse(TEXT.replace("[src/send.py:1]", "[../../etc/passwd:1]"))
        self.assertEqual(an.verify(a, project()), ["../../etc/passwd:1"])

    def test_render_reads_back_the_same(self):
        a = an.parse(TEXT)
        self.assertEqual(an.render(an.parse(an.render(a))), an.render(a))



class AnalystInputTest(unittest.TestCase):
    """The driver gives the analyst the spec paths and checks its citations against them."""

    def run_analyze(self, spec):
        from types import SimpleNamespace

        from driver.task import analyze
        from tests.driver_fakes import sh
        root = spec_project()
        who = ("-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "commit.gpgsign=false")
        sh(root, "git", "init", "-q")
        sh(root, "git", "add", "-A")
        sh(root, "git", *who, "commit", "-q", "-m", "x")
        commit = sh(root, "git", "rev-parse", "HEAD")
        seen, events = [], []
        text = SPEC_TEXT.replace("task: retries", "task: demo").replace("3d549d7", commit)

        def role(name, inputs):
            seen.append((name, inputs))
            return SimpleNamespace(data={"analysis": text}, cost_usd=0)

        log = SimpleNamespace(add=lambda event, **kw: events.append((event, kw)))
        analyze(role, log, root, "Add retries.", "demo", spec)
        return seen, events

    def test_the_analyst_gets_the_spec_paths(self):
        seen, _ = self.run_analyze(["docs/"])
        self.assertEqual(seen[0][0], "analyst")
        self.assertEqual(seen[0][1]["spec"], ["docs/"])

    def test_the_analyst_inputs_have_no_spec_when_none_is_set(self):
        # So the key of a role call, and a session paused before spec existed, is unchanged.
        seen, _ = self.run_analyze(())
        self.assertNotIn("spec", seen[0][1])
        self.assertEqual(sorted(seen[0][1]), ["commit", "problems", "request", "task_id"])

    def test_the_driver_checks_each_specified_citation_against_the_spec_paths(self):
        _, events = self.run_analyze(["docs/"])
        self.assertEqual(events[-1][1]["not_found"], [])
        _, events = self.run_analyze(["other/"])
        self.assertEqual(events[-1][1]["not_found"], ["docs/design.md::Retries", "docs/design.md"])


class SameRuleTest(unittest.TestCase):
    def test_the_copied_check_and_the_gate_find_a_name_the_same_way(self):
        from gate import paths
        from setup import check_writing
        self.assertEqual(paths.DEFINES, check_writing.DEFINES)
        self.assertEqual(paths.LANGUAGE_DEFINES, check_writing.LANGUAGE_DEFINES)
        import inspect
        self.assertEqual(inspect.getsource(paths.defines), inspect.getsource(check_writing.defines))


class LanguageNameTest(unittest.TestCase):
    """A citation path::Name resolves for the definitions of each language."""

    GO = """package ports

import "context"

type Acceptance struct {
	ID string
}

type (
	Receipt struct{}
	Store   interface {
		Accept(ctx context.Context, a Acceptance) error
		Close()
	}
)

const (
	MaxTries int = 3
)

func NewAcceptance(id string) Acceptance { return Acceptance{ID: id} }

func (s *store) Save(ctx context.Context) error {
	doThing(ctx)
	return nil
}
"""

    def test_go_types_funcs_methods_and_interface_methods(self):
        from gate import paths
        for name in ("Acceptance", "Receipt", "Store", "Accept", "MaxTries", "NewAcceptance",
                     "Save", "ports.Acceptance"):
            with self.subTest(name=name):
                self.assertTrue(paths.defines(self.GO, name, "internal/ports/repository.go"))
        for name in ("doThing", "Missing", "ID"):
            with self.subTest(name=name):
                self.assertFalse(paths.defines(self.GO, name, "internal/ports/repository.go"))

    def test_the_go_patterns_apply_only_to_go_files(self):
        from gate import paths
        self.assertFalse(paths.defines("type Acceptance struct {}\n", "Acceptance", "notes.txt"))
        # The default still finds a Go func, and a Python def, in any file.
        self.assertTrue(paths.defines("func Run() {}\n", "Run", "x.txt"))

    def test_other_languages(self):
        from gate import paths
        cases = [("src/lib.rs", "pub struct Receipt {}\n", "Receipt"),
                 ("src/lib.rs", "pub(crate) async fn accept() {}\n", "accept"),
                 ("src/a.ts", "export interface Receipt {}\n", "Receipt"),
                 ("src/a.tsx", "export default function App() {}\n", "App"),
                 ("src/a.js", "export const total = 1\n", "total")]
        for path, text, name in cases:
            with self.subTest(path=path, name=name):
                self.assertTrue(paths.defines(text, name, path))

    def test_a_go_citation_in_an_analysis_is_found(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            Path(root, "internal/ports").mkdir(parents=True)
            Path(root, "internal/ports/repository.go").write_text(self.GO)
            text = ("---\ntask: t\ncommit: c\n---\n"
                    "## Structure\n- confirmed: the port [internal/ports/repository.go::Acceptance]\n"
                    "## Entry points\n- confirmed: a method [internal/ports/repository.go::Save]\n"
                    "## Frameworks\n- assumed: none\n## Similar features\n- assumed: none\n"
                    "## Tests\n- assumed: none\n## Standards\n- assumed: none\n")
            self.assertEqual(an.verify(an.parse(text), root), [])


class CommandTest(unittest.TestCase):
    def run_main(self, text, root):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)), redirect_stdout(out):
            code = an.main(["--root", root])
        return code, out.getvalue()

    def test_prints_the_checked_analysis(self):
        code, out = self.run_main(TEXT.replace("[src/send.py:1]", "[src/send.py:40]"), project())
        self.assertEqual(code, 0)
        self.assertIn("- assumed: the sender lives in one module [src/send.py:40] "
                      "(not found: src/send.py:40)", out)
        self.assertIn("Not found, so made assumptions: src/send.py:40", out)

    def test_a_bad_analysis_ends_with_1_and_says_why(self):
        code, out = self.run_main("no header", project())
        self.assertEqual(code, 1)
        self.assertIn("an analysis starts with a --- line", out)


ABSENT = TEXT.replace(
    "- assumed: the project runs on Python 3.11",
    "- confirmed: the project has no go.mod [./]\n"
    "- confirmed: the project has no config folder [src/]\n"
    "- assumed: the project runs on Python 3.11")


class AbsenceTest(unittest.TestCase):
    """A fact about something the project lacks cites the folder that would hold it."""

    def test_reads_a_folder_citation(self):
        facts = an.parse(ABSENT).sections["Frameworks"]
        self.assertEqual([f.cites for f in facts], [[("./", None, None)], [("src/", None, None)], []])

    def test_a_folder_that_exists_keeps_the_fact_confirmed(self):
        a = an.parse(ABSENT)
        self.assertEqual(an.verify(a, project()), [])
        self.assertEqual([f.status for f in a.sections["Frameworks"]],
                         ["confirmed", "confirmed", "assumed"])

    def test_a_folder_that_does_not_exist_makes_the_fact_assumed(self):
        a = an.parse(ABSENT.replace("[src/]", "[config/]"))
        self.assertEqual(an.verify(a, project()), ["config/"])
        fact = a.sections["Frameworks"][1]
        self.assertEqual(fact.status, "assumed")
        self.assertIn("(not found: config/)", fact.text)

    def test_a_folder_outside_the_project_makes_the_fact_assumed(self):
        a = an.parse(ABSENT.replace("[src/]", "[../]"))
        self.assertEqual(an.verify(a, project()), ["../"])
        self.assertEqual(a.sections["Frameworks"][1].status, "assumed")

    def test_a_file_is_not_a_folder(self):
        a = an.parse(ABSENT.replace("[src/]", "[src/send.py/]"))
        self.assertEqual(an.verify(a, project()), ["src/send.py/"])

    def test_a_code_citation_with_no_line_or_name_makes_the_fact_assumed(self):
        fact = an.parse(TEXT.replace(" [tests/test_send.py:1]",
                                     " [tests/test_send.py]")).sections["Tests"][0]
        self.assertEqual(fact.status, "assumed")
        self.assertIn("[path:line] or [path::name]", fact.text)

    def test_a_roadmap_is_not_a_spec_doc(self):
        text = SPEC_TEXT.replace("[docs/design.md]", "[.tollgate/roadmaps/r.md]")
        a = an.parse(text)
        self.assertEqual(an.verify(a, spec_project(), ["docs"]), [".tollgate/roadmaps/r.md"])
        self.assertEqual(a.sections["Frameworks"][1].status, "assumed")

    def test_a_specified_fact_cited_by_line_becomes_assumed_with_the_right_form(self):
        text = SPEC_TEXT.replace("[docs/design.md]", "[.tollgate/roadmaps/r.md:11]")
        fact = an.parse(text).sections["Frameworks"][1]
        self.assertEqual(fact.status, "assumed")
        self.assertIn("a specified fact [doc] or [doc::heading]", fact.text)


class PointerTest(unittest.TestCase):
    def test_a_pointer_in_place_of_the_analysis_is_refused_with_the_fix(self):
        pointer = "---\ntask: retries\ncommit: 3d549d7\n---\n(see the block above)\n"
        with self.assertRaisesRegex(an.AnalysisError,
                                    "empty or missing sections: Structure.*not a pointer to the block above"):
            an.parse(pointer)


if __name__ == "__main__":
    unittest.main()
