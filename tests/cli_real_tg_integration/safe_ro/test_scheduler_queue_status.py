import pytest

pytestmark = pytest.mark.real_tg_safe


def test_scheduler_queue_status(run_cli, assert_cli_ok):
    result = run_cli("scheduler", "queue-status")
    assert_cli_ok(result)
    assert result.stdout.strip(), "`scheduler queue-status` produced empty stdout"
