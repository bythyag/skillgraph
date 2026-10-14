import pytest

from skillgraph.cli import main


def run_cli(*argv):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    return exc.value.code


def test_init_and_install_skill(tmp_path, examples, capsys):
    main(["init", str(examples / "skills" / "bugfix-tdd"), "--dir", str(tmp_path / "evals" / "bugfix")])
    suite = (tmp_path / "evals" / "bugfix" / "suite.yaml").read_text()
    assert "claims/bugfix-tdd.claims.json" in suite and (tmp_path / "evals" / "bugfix" / "fixtures" / "example").is_dir()
    assert "already exists" in str(run_cli("init", str(examples / "skills" / "bugfix-tdd"), "--dir", str(tmp_path / "evals" / "bugfix")))

    main(["install-skill", "--project", str(tmp_path)])
    installed = tmp_path / ".claude" / "skills" / "skillgraph"
    assert (installed / "SKILL.md").read_text().startswith("---\nname: skillgraph")
    assert (installed / "references" / "build-spec.md").exists()


def test_lint_exits_nonzero_on_errors(examples, capsys):
    # csv-report deliberately references a missing file
    assert run_cli("lint", str(examples / "suite.yaml"), "--skills", "csv-report") == 1
    assert "dangling-file" in capsys.readouterr().out


def test_doctor_flags_unconfigured_provider(capsys):
    code = run_cli("doctor", "--judge-model", "openrouter:google/gemini-3.8-flash")
    out = capsys.readouterr().out
    assert code == 1 and "openrouter isn't set up" in out


def test_graph_export(examples, tmp_path, capsys):
    main(["graph", str(examples / "suite.yaml"), "--skill", "release-notes", "--format", "cypher"])
    assert "MERGE (s:Skill {name: \"release-notes\"})" in capsys.readouterr().out
    main(["graph", str(examples / "suite.yaml"), "--skill", "release-notes", "--format", "graphml", "-o", str(tmp_path / "g.graphml")])
    assert (tmp_path / "g.graphml").read_text().startswith("<?xml")
