import pytest

pytestmark = pytest.mark.real_tg_safe


def test_analytics_usefulness(run_cli, assert_cli_ok):
    result = run_cli("analytics", "usefulness", "--limit", "5")
    assert_cli_ok(result)
    out = (result.stdout + result.stderr).lower()
    assert "channel_id" in out or "no channels with collected posts found" in out
