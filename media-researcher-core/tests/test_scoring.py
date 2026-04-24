"""Tests for the scoring module — no API keys required."""
import pytest
from datetime import datetime, timezone, timedelta

from media_researcher_core.models import (
    MediaTarget,
    PersonalizationDepth,
    RecentWork,
    ResearchBrief,
    TargetType,
)
from media_researcher_core.config import ScoringWeights
from media_researcher_core.scoring import Scorer


def make_brief(topic="AI infrastructure, developer tools") -> ResearchBrief:
    return ResearchBrief(
        target_type=TargetType.MIXED,
        topic=topic,
        num_results=10,
        depth=PersonalizationDepth.MEDIUM,
    )


def make_target(
    id="t1",
    name="Test Pod",
    target_type=TargetType.PODCASTS,
    audience_size=10_000,
    recent_work=None,
) -> MediaTarget:
    return MediaTarget(
        id=id,
        target_type=target_type,
        name=name,
        audience_size=audience_size,
        audience_unit="monthly downloads",
        recent_work=recent_work or [],
        source="test",
    )


class TestScoringWeights:
    def test_valid_weights_pass(self):
        w = ScoringWeights(topical_fit=0.35, audience=0.25, recency=0.20, response_likelihood=0.20)
        w.validate()  # should not raise

    def test_invalid_weights_raise(self):
        w = ScoringWeights(topical_fit=0.5, audience=0.5, recency=0.5, response_likelihood=0.5)
        with pytest.raises(ValueError, match="sum to 1.0"):
            w.validate()


class TestScorer:
    def setup_method(self):
        self.weights = ScoringWeights(
            topical_fit=0.35, audience=0.25, recency=0.20, response_likelihood=0.20
        )
        self.scorer = Scorer(self.weights)

    def test_scores_are_between_0_and_1(self):
        brief = make_brief()
        targets = [make_target(id=f"t{i}", audience_size=i * 1000) for i in range(1, 6)]
        ranked = self.scorer.score_and_rank(targets, brief)
        for t in ranked:
            assert 0.0 <= t.composite_score <= 1.0
            assert 0.0 <= t.topical_fit_score <= 1.0
            assert 0.0 <= t.audience_score <= 1.0
            assert 0.0 <= t.recency_score <= 1.0
            assert 0.0 <= t.response_likelihood_score <= 1.0

    def test_sorted_descending(self):
        brief = make_brief()
        targets = [make_target(id=f"t{i}") for i in range(5)]
        ranked = self.scorer.score_and_rank(targets, brief)
        scores = [t.composite_score for t in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_topical_fit_keyword_match(self):
        brief = make_brief(topic="developer tools AI")
        target_match = make_target(name="AI Developer Tools Podcast")
        target_miss = make_target(name="Gardening Weekly")
        ranked = self.scorer.score_and_rank([target_match, target_miss], brief)
        assert ranked[0].id == target_match.id

    def test_recency_recent_work_boosts_score(self):
        brief = make_brief()
        recent = make_target(
            id="recent",
            recent_work=[
                RecentWork(title="AI infra deep dive", date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5))
            ],
        )
        stale = make_target(
            id="stale",
            recent_work=[
                RecentWork(title="AI infra old post", date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=200))
            ],
        )
        ranked = self.scorer.score_and_rank([recent, stale], brief)
        assert ranked[0].id == "recent"

    def test_unknown_audience_gets_neutral_score(self):
        brief = make_brief()
        target = make_target(audience_size=None)
        self.scorer.score_and_rank([target], brief)
        assert target.audience_score == 0.3

    def test_small_podcast_has_high_response_likelihood(self):
        brief = make_brief()
        small = make_target(id="small", target_type=TargetType.PODCASTS, audience_size=5_000)
        large = make_target(id="large", target_type=TargetType.PODCASTS, audience_size=500_000)
        self.scorer.score_and_rank([small, large], brief)
        assert small.response_likelihood_score > large.response_likelihood_score

    def test_publication_has_low_response_likelihood(self):
        brief = make_brief()
        pub = make_target(id="pub", target_type=TargetType.PUBLICATIONS, audience_size=10_000_000)
        self.scorer.score_and_rank([pub], brief)
        assert pub.response_likelihood_score == 0.3


class TestMarkdownFormatter:
    def test_renders_without_errors(self):
        from media_researcher_core.models import ResearchReport
        from media_researcher_core.output import MarkdownFormatter

        brief = make_brief()
        target = make_target()
        report = ResearchReport(brief=brief, targets=[target])
        md = MarkdownFormatter().render(report)
        assert "Media Research Report" in md
        assert target.name in md

    def test_includes_critical_notice(self):
        from media_researcher_core.models import ResearchReport
        from media_researcher_core.output import MarkdownFormatter

        brief = make_brief()
        report = ResearchReport(brief=brief, targets=[])
        md = MarkdownFormatter().render(report)
        assert "IMPORTANT" in md or "human review" in md


class TestCSVFormatter:
    def test_renders_header_and_rows(self):
        from media_researcher_core.models import ResearchReport
        from media_researcher_core.output import CSVFormatter

        brief = make_brief()
        targets = [make_target(id=f"t{i}", name=f"Target {i}") for i in range(3)]
        report = ResearchReport(brief=brief, targets=targets)
        csv_str = CSVFormatter().render(report)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 4  # header + 3 rows
        assert "name" in lines[0]
