import os
from unittest import TestCase
from unittest.mock import MagicMock, patch

from jf_agent.agent_logging import _console_use_color


def _build_stream(is_tty: bool) -> MagicMock:
    stream = MagicMock()
    stream.isatty.return_value = is_tty
    return stream


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
