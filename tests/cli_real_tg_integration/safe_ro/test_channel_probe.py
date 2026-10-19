import pytest

pytestmark = pytest.mark.real_tg_safe


@pytest.mark.timeout(240)
def test_channel_probe(run_cli, assert_cli_ok, live_channel):
    pk, _channel_id = live_channel
    result = run_cli("channel", "probe", pk, "--limit", "35", timeout=180)
    assert_cli_ok(result)
    assert result.stdout.strip(), "`channel probe` produced empty stdout"
