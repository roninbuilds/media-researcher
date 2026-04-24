"""Tests for xAI integration — no real API key required."""
import pytest

from media_researcher_core.config import Config
from media_researcher_core.models import (
    MediaTarget,
    PersonalizationDepth,
    ResearchBrief,
    TargetType,
)
from media_researcher_core.discovery.xai_discovery import XAIDiscoverer, _safe_int, _audience_string
from media_researcher_core.enrichment.xai_enricher import XAIEnricher


class TestXAIConfig:
    def test_xai_key_read_from_env(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key-123")
        monkeypatch.setenv("XAI_MODEL", "grok-3-latest")
        config = Config()
        assert config.xai_api_key == "test-key-123"
        assert config.xai_model == "grok-3-latest"

    def test_xai_absent_from_available_sources(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        config = Config()
        assert config.available_sources()["xai"] is False

    def test_xai_present_in_available_sources(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        config = Config()
        assert config.available_sources()["xai"] is True

    def test_default_model_is_grok3(self, monkeypatch):
        monkeypatch.delenv("XAI_MODEL", raising=False)
        config = Config()
        assert config.xai_model == "grok-3-latest"


class TestXAIDiscovererInit:
    def test_client_none_without_key(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        config = Config()
        cache = _make_cache(config)
        discoverer = XAIDiscoverer(config, cache)
        assert discoverer._client is None

    def test_client_created_with_key(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        config = Config()
        cache = _make_cache(config)
        discoverer = XAIDiscoverer(config, cache)
        assert discoverer._client is not None

    @pytest.mark.asyncio
    async def test_discover_returns_empty_without_key(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        config = Config()
        cache = _make_cache(config)
        brief = ResearchBrief(
            target_type=TargetType.PODCASTS,
            topic="AI infrastructure",
            num_results=5,
            depth=PersonalizationDepth.LIGHT,
        )
        discoverer = XAIDiscoverer(config, cache)
        results = await discoverer.discover(brief)
        assert results == []


class TestXAIEnricher:
    def test_not_available_without_key(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        config = Config()
        cache = _make_cache(config)
        enricher = XAIEnricher(config, cache)
        assert enricher.available is False

    def test_available_with_key(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        config = Config()
        cache = _make_cache(config)
        enricher = XAIEnricher(config, cache)
        assert enricher.available is True

    @pytest.mark.asyncio
    async def test_light_depth_returns_target_unchanged(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        config = Config()
        cache = _make_cache(config)
        enricher = XAIEnricher(config, cache)
        target = MediaTarget(
            id="t1", target_type=TargetType.PODCASTS, name="Test Pod"
        )
        brief = ResearchBrief(
            target_type=TargetType.PODCASTS,
            topic="test",
            depth=PersonalizationDepth.LIGHT,
        )
        # Light depth should return immediately without calling the API
        result = await enricher._do_enrich(target, brief, PersonalizationDepth.LIGHT)
        assert result.id == "t1"
        assert result.pitch_angle is None


class TestXAIHelpers:
    def test_safe_int(self):
        assert _safe_int(1000) == 1000
        assert _safe_int("5000") == 5000
        assert _safe_int(None) is None
        assert _safe_int("not-a-number") is None

    def test_audience_string_empty(self):
        brief = ResearchBrief(
            target_type=TargetType.PODCASTS, topic="test", num_results=5,
            depth=PersonalizationDepth.LIGHT
        )
        assert _audience_string(brief) == "none"

    def test_audience_string_with_constraints(self):
        from media_researcher_core.models import AudienceConstraints
        brief = ResearchBrief(
            target_type=TargetType.PODCASTS,
            topic="test",
            num_results=5,
            depth=PersonalizationDepth.LIGHT,
            audience_constraints=AudienceConstraints(min_downloads=5000, max_downloads=100000),
        )
        result = _audience_string(brief)
        assert "5,000" in result
        assert "100,000" in result

    def test_parse_item_handles_bad_data(self):
        result = XAIDiscoverer._parse_item({"name": None})
        # Should not raise — just return None or a best-effort target
        # (method returns None on Exception, or a target with defaults)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cache(config: Config):
    from media_researcher_core.cache import EnrichmentCache
    return EnrichmentCache(config)
