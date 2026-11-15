"""Тесты разбора и печати денег и дат."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from advance_report.errors import DataError
from advance_report.formatting import (
    fmt_csv,
    fmt_date,
    fmt_date_iso,
    fmt_plain,
    fmt_ru,
    parse_amount,
    parse_date,
    q2,
)


class ParseAmountTests(unittest.TestCase):
    """Разбор сумм: что принимаем и что отвергаем."""

    def test_plain_amount(self) -> None:
        self.assertEqual(parse_amount("100000.00", "тест"), Decimal("100000.00"))

    def test_integer_amount_gets_kopecks(self) -> None:
        self.assertEqual(parse_amount("500", "тест"), Decimal("500.00"))

    def test_one_decimal_digit(self) -> None:
        self.assertEqual(parse_amount("10.5", "тест"), Decimal("10.50"))

    def test_spaces_inside_amount(self) -> None:
        # Excel охотно пишет «1 000 000,00»; пробелы не меняют значение.
        self.assertEqual(parse_amount(" 1 000 000.00 ", "тест"), Decimal("1000000.00"))

    def test_comma_decimal_is_rejected(self) -> None:
        # Двусмысленно: запятая может быть и тысячами, и копейками.
        with self.assertRaises(DataError):
            parse_amount("1000,00", "тест")

    def test_negative_amount_is_rejected(self) -> None:
        with self.assertRaises(DataError):
            parse_amount("-1.00", "тест")

    def test_empty_amount_is_rejected(self) -> None:
        with self.assertRaises(DataError):
            parse_amount("", "тест")

    def test_three_decimals_rejected(self) -> None:
        with self.assertRaises(DataError):
            parse_amount("1.005", "тест")

    def test_garbage_rejected(self) -> None:
        for value in ("руб", "1e5", "10.00.00", "1,000", "1000 руб."):
            with self.subTest(value=value), self.assertRaises(DataError):
                parse_amount(value, "тест")

    def test_error_mentions_place(self) -> None:
        with self.assertRaises(DataError) as ctx:
            parse_amount("abc", "works.csv:4, колонка price")
        self.assertIn("works.csv:4", str(ctx.exception))


class QuantizeTests(unittest.TestCase):
    """Округление до копейки — половина вверх, как в бухгалтерии."""

    def test_half_up(self) -> None:
        self.assertEqual(q2(Decimal("0.005")), Decimal("0.01"))
        self.assertEqual(q2(Decimal("2.675")), Decimal("2.68"))

    def test_half_down_below(self) -> None:
        self.assertEqual(q2(Decimal("0.004")), Decimal("0.00"))
        self.assertEqual(q2(Decimal("2.674")), Decimal("2.67"))


class FormatTests(unittest.TestCase):
    def test_russian_money(self) -> None:
        self.assertEqual(fmt_ru(Decimal("315000.00")), "315 000,00")
        self.assertEqual(fmt_ru(Decimal("0.00")), "0,00")
        self.assertEqual(fmt_ru(Decimal("1234567.89")), "1 234 567,89")

    def test_plain_money(self) -> None:
        self.assertEqual(fmt_plain(Decimal("315000")), "315000.00")

    def test_csv_money_uses_comma(self) -> None:
        self.assertEqual(fmt_csv(Decimal("33.30")), "33,30")

    def test_empty_date(self) -> None:
        self.assertEqual(fmt_date(None), "—")
        self.assertEqual(fmt_date_iso(None), "")

    def test_date_format(self) -> None:
        self.assertEqual(fmt_date(date(2026, 11, 10)), "10.11.2026")
        self.assertEqual(fmt_date_iso(date(2026, 11, 10)), "2026-11-10")


class ParseDateTests(unittest.TestCase):
    def test_iso_date(self) -> None:
        self.assertEqual(parse_date("2026-11-10", "тест", "paid_at", required=True), date(2026, 11, 10))

    def test_empty_optional(self) -> None:
        self.assertIsNone(parse_date("", "тест", "paid_at", required=False))

    def test_empty_required(self) -> None:
        with self.assertRaises(DataError):
            parse_date("", "тест", "paid_at", required=True)

    def test_russian_format_rejected(self) -> None:
        with self.assertRaises(DataError):
            parse_date("10.11.2026", "тест", "paid_at", required=True)


if __name__ == "__main__":
    unittest.main()
