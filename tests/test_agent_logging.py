import logging
import os
import re
from unittest import TestCase
from unittest.mock import MagicMock, patch

from jf_agent.agent_logging import _console_use_color, readable_log_formatter

ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*m')


def _build_stream(is_tty: bool) -> MagicMock:
    stream = MagicMock()
    stream.isatty.return_value = is_tty
    return stream


def _build_structlog_record() -> logging.LogRecord:
    # Mimics a record produced by structlog's wrap_for_formatter, with a fixed timestamp
    record = logging.makeLogRecord(
        {
            'msg': {
                'event': 'Log stream stopped.',
                'level': 'info',
                'timestamp': '2026-11-13T18:16:41.000000Z',
            },
            'levelno': logging.INFO,
            'levelname': 'INFO',
        }
    )
    record._logger = logging.getLogger(__name__)
    record._name = 'info'
    return record


class TestConsoleUseColor(TestCase):
    def test_tty_without_no_color_uses_color(self):
        with patch.dict(os.environ, clear=False):
            os.environ.pop('NO_COLOR', None)
            self.assertTrue(_console_use_color(_build_stream(is_tty=True)))

    def test_non_tty_does_not_use_color(self):
        with patch.dict(os.environ, clear=False):
            os.environ.pop('NO_COLOR', None)
            self.assertFalse(_console_use_color(_build_stream(is_tty=False)))

    def test_tty_with_no_color_set_does_not_use_color(self):
        with patch.dict(os.environ, {'NO_COLOR': '1'}):
            self.assertFalse(_console_use_color(_build_stream(is_tty=True)))

    def test_tty_with_empty_no_color_uses_color(self):
        with patch.dict(os.environ, {'NO_COLOR': ''}):
            self.assertTrue(_console_use_color(_build_stream(is_tty=True)))

    def test_stream_without_isatty_does_not_use_color(self):
        with patch.dict(os.environ, clear=False):
            os.environ.pop('NO_COLOR', None)
            self.assertFalse(_console_use_color(object()))

    def test_isatty_raising_does_not_use_color(self):
        with patch.dict(os.environ, clear=False):
            os.environ.pop('NO_COLOR', None)
            for error in (ValueError('I/O operation on closed file'), OSError()):
                stream = MagicMock()
                stream.isatty.side_effect = error
                self.assertFalse(_console_use_color(stream))


class TestReadableLogFormatter(TestCase):
    def test_console_output_without_color_matches_colored_output_without_ansi(self):
        colored = readable_log_formatter(use_color=True, use_brackets=True).format(
            _build_structlog_record()
        )
        plain = readable_log_formatter(use_color=False, use_brackets=True).format(
            _build_structlog_record()
        )

        self.assertNotEqual(colored, plain)
        self.assertEqual(ANSI_ESCAPE_RE.sub('', colored), plain)
        self.assertEqual(plain, '2026-11-13 18:16:41 [info    ] Log stream stopped.')

    def test_log_file_output_is_unchanged(self):
        output = readable_log_formatter(use_color=False).format(_build_structlog_record())

        self.assertEqual(output, '2026-11-13 18:16:41 info     Log stream stopped.')
