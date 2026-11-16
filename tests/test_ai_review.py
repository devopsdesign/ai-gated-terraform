import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import ai_review  # noqa: E402

FIX = pathlib.Path(__file__).parent / "fixtures"


def review(name):
    plan = json.loads((FIX / name).read_text())
    return ai_review.assess(ai_review.load_changes(plan))


def test_creates_only_is_low_risk():
    r = review("plan_low.json")
    assert r.risk == "low"
    assert len(r.changes) == 2


def test_replace_and_iam_is_high_risk():
    r = review("plan_high.json")
    assert r.risk == "high"
    assert any("replaced" in x for x in r.reasons)
    assert any("aws_iam_role_policy" in x for x in r.reasons)
    # no-op resources are ignored
    assert all(c.kind != "no-op" for c in r.changes)


def test_many_changes_is_medium():
    changes = [ai_review.Change(f"local_file.f{i}", "local_file", ["create"]) for i in range(25)]
    assert ai_review.assess(changes).risk == "medium"


def test_empty_plan_renders_nothing_to_approve(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    out = tmp_path / "r.md"
    ai_review.main([str(FIX / "plan_empty.json"), "--out", str(out), "--no-llm"])
    assert "Nothing to approve" in out.read_text()


def test_llm_is_skipped_without_config(monkeypatch):
    for k in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    changes = [ai_review.Change("local_file.x", "local_file", ["create"])]
    assert ai_review.llm_summary(changes) is None


def test_risk_written_to_github_output(tmp_path, monkeypatch):
    gh = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    ai_review.main([str(FIX / "plan_high.json"), "--out", str(tmp_path / "r.md"), "--no-llm"])
    assert "risk=high" in gh.read_text()
