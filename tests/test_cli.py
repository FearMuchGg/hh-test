"""Тесты командной строки: форматы вывода, коды возврата, повторяемость."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import support
from advance_report.cli import main

AS_OF = "2026-11-10"


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        # Временный каталог — внутри решения: системный %TEMP% закрыт на запись.
        self.base = support.make_temp_dir("cli-")

    def tearDown(self) -> None:
        support.remove_temp_dir(self.base)

    def _run(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue()

    def test_console_contains_key_numbers(self) -> None:
        code, out = self._run("--data", str(support.DATA_DIR), "--date", AS_OF)
        self.assertEqual(code, 0)
        self.assertIn("D-1", out)
        self.assertIn("W-23", out)
        self.assertIn("185 000,00", out)  # остаток по D-1
        self.assertIn("200 000,00", out)  # итого осталось оплатить
        self.assertIn("не принята на дату", out)

    def test_all_formats_write_files(self) -> None:
        out_dir = self.base / "out"
        code, _ = self._run(
            "--data", str(support.DATA_DIR), "--date", AS_OF, "--format", "all", "--out", str(out_dir)
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(path.name for path in out_dir.iterdir()),
            [
                "contracts_2026-11-10.csv",
                "payments_2026-11-10.csv",
                "report_2026-11-10.json",
                "report_2026-11-10.md",
                "works_2026-11-10.csv",
            ],
        )

    def test_json_totals_and_meta(self) -> None:
        out_dir = self.base / "out"
        self._run("--data", str(support.DATA_DIR), "--date", AS_OF, "--format", "json", "--out", str(out_dir))
        payload = json.loads((out_dir / f"report_{AS_OF}.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["meta"]["as_of"], AS_OF)
        self.assertEqual(payload["totals"]["paid_total"], "390000.00")
        self.assertEqual(payload["totals"]["remaining"], "200000.00")
        self.assertEqual(payload["totals"]["queued_total"], "150000.00")
        self.assertEqual(len(payload["contracts"]), 2)
        self.assertEqual(len(payload["works"]), 9)
        d1 = {row["contract_id"]: row for row in payload["contracts"]}["D-1"]
        self.assertEqual(d1["remaining"], "185000.00")
        self.assertEqual(d1["cancelled_total"], "100000.00")

    def test_csv_uses_semicolon_and_russian_decimals(self) -> None:
        out_dir = self.base / "out"
        self._run("--data", str(support.DATA_DIR), "--date", AS_OF, "--format", "csv", "--out", str(out_dir))
        text = (out_dir / f"works_{AS_OF}.csv").read_text(encoding="utf-8-sig")
        header = text.splitlines()[0]
        self.assertIn(";", header)
        self.assertIn("315000,00", (out_dir / f"contracts_{AS_OF}.csv").read_text(encoding="utf-8-sig"))

    def test_csv_dot_option(self) -> None:
        out_dir = self.base / "out"
        self._run(
            "--data", str(support.DATA_DIR), "--date", AS_OF, "--format", "csv",
            "--out", str(out_dir), "--csv-dot", "--csv-delimiter", ",",
        )
        text = (out_dir / f"contracts_{AS_OF}.csv").read_text(encoding="utf-8-sig")
        self.assertIn("315000.00", text)

    def test_invalid_date_is_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                main(["--date", "10.11.2026"])
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_data_returns_two(self) -> None:
        code, out = self._run("--data", str(self.base / "нет-такого"), "--date", AS_OF)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")  # отчёт не печатается, ошибка ушла в stderr

    def test_broken_data_returns_two(self) -> None:
        bad = support.write_dataset(
            self.base / "bad",
            contracts=["D-1,ООО,1000.00,2025-01-01"],
            works=["W-1,D-1,Работа,1000.00,unknown,,"],  # неизвестный статус
            payments=[],
        )
        code, _ = self._run("--data", str(bad), "--date", AS_OF)
        self.assertEqual(code, 2)

    def test_report_is_byte_identical_on_repeat(self) -> None:
        first, second = self.base / "first", self.base / "second"
        self._run("--data", str(support.DATA_DIR), "--date", AS_OF, "--format", "all", "--out", str(first))
        self._run("--data", str(support.DATA_DIR), "--date", AS_OF, "--format", "all", "--out", str(second))
        for path in sorted(first.iterdir()):
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes(), msg=path.name)

    def test_markdown_has_tables(self) -> None:
        out_dir = self.base / "out"
        self._run("--data", str(support.DATA_DIR), "--date", AS_OF, "--format", "md", "--out", str(out_dir))
        text = (out_dir / f"report_{AS_OF}.md").read_text(encoding="utf-8")
        self.assertIn("| Договор |", text)
        self.assertIn("## Работы", text)
        self.assertIn("## Проверки", text)


if __name__ == "__main__":
    unittest.main()
