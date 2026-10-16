"""Live speech-to-text smoke for `dialogs transcribe` (#1545).

Gated like the other real-provider tests: ``RUN_REAL_PROVIDER_SMOKE=1`` plus
``GROQ_API_KEY`` and a real voice message ref
(``TGCF_SMOKE_VOICE_CHAT`` / ``TGCF_SMOKE_VOICE_MSG`` — a chat identifier and a
message id whose media is a voice note). Without any of those the whole module
skips, so the suite stays green offline.

Owner consent is required before running: this performs a real Telegram
download and a real (paid) Groq call.
"""

from __future__ import annotations

import os

import pytest

from tests.cli_real_provider_integration.conftest import cli_run

_GATE = os.environ.get("RUN_REAL_PROVIDER_SMOKE") == "1"
_GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
_VOICE_CHAT = os.environ.get("TGCF_SMOKE_VOICE_CHAT", "")
_VOICE_MSG = os.environ.get("TGCF_SMOKE_VOICE_MSG", "")

_skip_reason = "Set RUN_REAL_PROVIDER_SMOKE=1, GROQ_API_KEY and TGCF_SMOKE_VOICE_CHAT/TGCF_SMOKE_VOICE_MSG."

pytestmark = [
    pytest.mark.real_provider_smoke,
    pytest.mark.skipif(
        not (_GATE and _GROQ_KEY and _VOICE_CHAT and _VOICE_MSG),
        reason=_skip_reason,
    ),
]


def test_groq_engine_transcribes_real_voice(provider_tmp_env):
    """A real voice note comes back as non-empty text, then free from the cache."""
    config_path, _db_path = provider_tmp_env
    first = cli_run(config_path, "dialogs", "transcribe", _VOICE_CHAT, _VOICE_MSG, "--engine", "groq")
    assert first.returncode == 0, first.stderr
    assert "not verbatim" in first.stdout
    transcript_line = [line for line in first.stdout.splitlines() if line.strip() and not line.startswith("[")]
    assert transcript_line, f"no transcript text in output:\n{first.stdout}"

    # Acceptance criterion #1545: the repeat call answers from the cache —
    # no second download/upload, so it is also much faster.
    import time

    started = time.monotonic()
    second = cli_run(config_path, "dialogs", "transcribe", _VOICE_CHAT, _VOICE_MSG, "--engine", "groq")
    elapsed = time.monotonic() - started
    assert second.returncode == 0, second.stderr
    assert "cached" in second.stdout
    assert transcript_line[0] in second.stdout
    assert elapsed < 15.0, f"cache hit took {elapsed:.1f}s — expected a local DB read"
