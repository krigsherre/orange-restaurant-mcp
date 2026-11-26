import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _path  # noqa: F401
from orange.env import load_env, parse_env


class ParseEnvTests(unittest.TestCase):
    def test_parses_comments_export_quotes_and_empties(self):
        text = """
# comment

export DEEPSEEK_API_KEY=sk-test
ORANGE_LLM="off"
QUOTED='spaces kept'
EMPTY=
NO_EQUALS
"""
        values = parse_env(text)
        self.assertEqual(values["DEEPSEEK_API_KEY"], "sk-test")
        self.assertEqual(values["ORANGE_LLM"], "off")
        self.assertEqual(values["QUOTED"], "spaces kept")
        self.assertEqual(values["EMPTY"], "")
        self.assertNotIn("NO_EQUALS", values)

    def test_handles_bom_and_crlf(self):
        self.assertEqual(parse_env("\ufeffA=1\r\nB=2\r\n"), {"A": "1", "B": "2"})


class LoadEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".env"
        self.path.write_text("DEEPSEEK_API_KEY=from-file\nORANGE_LLM=off\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_into_the_process_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            applied = load_env(self.path)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-file")
            self.assertEqual(applied["ORANGE_LLM"], "off")

    def test_existing_variables_win(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "from-shell"}, clear=True):
            load_env(self.path)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-shell")

    def test_missing_file_is_not_an_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_env(Path(self.tmp.name) / "absent.env"), {})


if __name__ == "__main__":
    unittest.main()
