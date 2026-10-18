"""Regression coverage for managed module and console-script daemon invocations."""

from __future__ import annotations

import os
import shlex
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tg_agent.cli import process_control, worker_handoff

DAEMON_COMMANDS = [
    f"{entry} {options}{command}{suffix}"
    for entry in (
        "python -m tg_agent.main",
        "/venv/bin/python3.11 /venv/bin/tg-agent",
        "/venv/bin/tg-agent",
        "python -u -m tg_agent.main",
    )
    for options in ("", "--config prod.yaml ", "--config=prod.yaml ", "--config 'my config.yaml' ")
    for command, suffix in (("worker", ""), ("restart", ""), ("serve", " --no-worker"))
]


class TestDaemonDetection(unittest.TestCase):
    def test_supported_invocations(self):
        for command in DAEMON_COMMANDS:
            with (
                self.subTest(command=command),
                patch.object(process_control, "_process_command", return_value=command),
                patch.object(process_control, "is_process_alive", return_value=True),
            ):
                self.assertTrue(process_control.is_expected_server_process(1234))

    def test_rejects_unrelated_and_incomplete_invocations(self):
        for command in (
            "python -m other_app worker",
            "python -m tg_agent.main stop",
            "python -m tg_agent.main --config worker",
            "tg-agent --config restart stop",
            "tg-agent dialogs send worker",
            "tg-agent --version worker",
            "tg-agent --unknown worker",
            "tg-agent --config",
            "python -c 'print(1)' -m tg_agent.main worker",
            "echo tg-agent worker",
            "python script.py -m tg_agent.main worker",
            "tg-agent-helper worker",
            "tg-agent 'unterminated",
        ):
            with (
                self.subTest(command=command),
                patch.object(process_control, "_process_command", return_value=command),
                patch.object(process_control, "is_process_alive", return_value=True),
            ):
                self.assertFalse(process_control.is_expected_server_process(1234))

    def test_linux_preserves_argument_boundaries(self):
        argv = ["/my venv/bin/python", "/my venv/bin/tg-agent", "--config", "worker config.yaml", "worker"]
        with (
            patch.object(process_control.sys, "platform", "linux"),
            patch.object(Path, "read_bytes", return_value=("\0".join(argv) + "\0").encode()),
        ):
            self.assertEqual(shlex.split(process_control._process_command(1234)), argv)

    def test_pid_guard_keeps_live_daemon_pid(self):
        for command in DAEMON_COMMANDS:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                pid_file = Path(directory) / "app.pid"
                pid_file.write_text("1234\n")
                with (
                    patch.object(process_control.os, "getpid", return_value=5678),
                    patch.object(process_control, "_process_command", return_value=command),
                    patch.object(process_control, "is_process_alive", return_value=True),
                    self.assertRaises(process_control.ProcessControlError),
                ):
                    process_control.register_current_process(pid_file)
                self.assertEqual(pid_file.read_text(), "1234\n")

    def test_stop_uses_real_detection(self):
        for command in DAEMON_COMMANDS:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                pid_file = Path(directory) / "app.pid"
                pid_file.write_text("1234\n")
                with (
                    patch.object(process_control, "_process_command", return_value=command),
                    patch.object(process_control, "is_process_alive", side_effect=[True, True, False]),
                    patch.object(os, "kill") as kill,
                ):
                    self.assertEqual(process_control.stop_server(pid_file).result, process_control.StopResult.STOPPED)
                kill.assert_called_once_with(1234, signal.SIGTERM)
                self.assertFalse(pid_file.exists())

    def test_handoff_uses_real_detection(self):
        for command in DAEMON_COMMANDS:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                pid_file = Path(directory) / "app.pid"
                pid_file.write_text("1234\n")
                with (
                    patch.object(worker_handoff, "pid_file_path", return_value=pid_file),
                    patch.object(process_control, "_process_command", return_value=command),
                    patch.object(process_control, "is_process_alive", return_value=True),
                ):
                    self.assertEqual(worker_handoff.serve_is_running(None), "--no-worker" not in command)
