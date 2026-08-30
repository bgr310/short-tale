"""Campaign schema: the committed config must load, and must refuse secrets."""

import pytest
import yaml
from pydantic import ValidationError

from shorttale.campaign import Campaign, load_campaigns


def test_shipped_campaigns_load(repo_root):
    campaigns = load_campaigns(repo_root / "config")
    assert "tailmailer" in campaigns
    assert "demo" in campaigns


def test_tailmailer_is_configured_sanely(tailmailer):
    assert tailmailer.product.name == "Tailmailer"
    assert tailmailer.product.url == "tailmailer.com"
    assert tailmailer.sources.reddit.enabled
    assert len(tailmailer.sources.reddit.subreddits) >= 5
    assert tailmailer.product.claims_allowed, "guardrails must not be empty"
    assert tailmailer.product.claims_forbidden
    # Review gate is the default and must stay that way.
    assert tailmailer.publish.mode == "review"


def test_shorts_duration_ceiling_is_enforced():
    base = _minimal()
    base["style"] = {"duration": {"max_seconds": 75}}
    with pytest.raises(ValidationError, match="under 60s"):
        Campaign.model_validate(base)


def test_a_source_must_be_enabled():
    base = _minimal()
    base["sources"] = {"reddit": {"enabled": False}, "rss": {"enabled": False}}
    with pytest.raises(ValidationError, match="at least one source"):
        Campaign.model_validate(base)


def test_secrets_in_campaign_yaml_are_rejected():
    """The whole point: config/campaigns/ is committed, so it can't hold keys."""
    base = _minimal()
    base["sources"] = {"reddit": {"enabled": True, "api_key": "abcd1234abcd1234"}}
    with pytest.raises(ValidationError, match="credential"):
        Campaign.model_validate(base)


def test_no_committed_campaign_contains_a_credential(repo_root):
    for path in (repo_root / "config" / "campaigns").glob("*.y*ml"):
        raw = yaml.safe_load(path.read_text())
        flat = yaml.dump(raw).lower()
        for hint in ("client_secret:", "password:", "api_key:", "bearer "):
            assert hint not in flat, f"{path.name} contains {hint!r}"


def _minimal() -> dict:
    return {
        "name": "t",
        "topic": {"description": "d"},
        "product": {"name": "P", "url": "p.com", "one_liner": "o"},
    }


def test_dry_run_fixtures_ship_inside_the_package():
    """--dry-run must work in the container, which does not copy tests/."""
    from shorttale.pipeline.orchestrator import (
        FIXTURES,
        _fixture_script,
        _load_fixture_candidates,
    )

    assert FIXTURES.is_dir(), f"fixtures missing from the package at {FIXTURES}"
    assert len(_load_fixture_candidates()) >= 1
    assert _fixture_script().word_count > 20
