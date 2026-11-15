"""Тесты загрузки и проверки выгрузки из data/."""

from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import support
from advance_report.errors import DataError
from advance_report.loading import load_dataset

VALID_CONTRACTS = ["D-1,ООО «Ромашка»,1000.00,2025-01-01"]
VALID_WORKS = ["W-1,D-1,Съёмка,1000.00,planned,,"]
VALID_PAYMENTS = ["P-1,D-1,,Аванс,1000.00,paid,2025-01-02,2025-01-03,"]


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        # Временный каталог — внутри решения: системный %TEMP% в этой среде
        # закрыт на запись.
        self.root = support.make_temp_dir("loading-")

    def tearDown(self) -> None:
        support.remove_temp_dir(self.root)

    def _load(self, **overrides):
        contracts = overrides.pop("contracts", VALID_CONTRACTS)
        works = overrides.pop("works", VALID_WORKS)
        payments = overrides.pop("payments", VALID_PAYMENTS)
        support.write_dataset(self.root, contracts=contracts, works=works, payments=payments, **overrides)
        return load_dataset(self.root)

    def test_valid_dataset(self) -> None:
        dataset = self._load()
        self.assertEqual(list(dataset.contracts), ["D-1"])
        self.assertEqual(list(dataset.works), ["W-1"])
        self.assertEqual(len(dataset.payments), 1)
        self.assertEqual(dataset.contracts["D-1"].amount, Decimal("1000.00"))
        self.assertEqual(dataset.warnings, [])
        payment = dataset.payments[0]
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.paid_at.isoformat(), "2025-01-03")
        self.assertIsNone(payment.work_id)

    def test_missing_file(self) -> None:
        (self.root / "contracts.csv").write_text("\n".join([support.CONTRACTS_HEADER, *VALID_CONTRACTS]), encoding="utf-8")
        with self.assertRaises(DataError) as ctx:
            load_dataset(self.root)
        self.assertIn("Не найден файл", str(ctx.exception))

    def test_empty_file(self) -> None:
        (self.root / "contracts.csv").write_text("", encoding="utf-8")
        for name in ("works.csv", "payments.csv"):
            (self.root / name).write_text("x", encoding="utf-8")
        with self.assertRaises(DataError):
            load_dataset(self.root)

    def test_missing_column(self) -> None:
        header = "contract_id,contractor,signed_at"
        (self.root / "contracts.csv").write_text(header + "\nD-1,ООО,2025-01-01\n", encoding="utf-8")
        with self.assertRaises(DataError) as ctx:
            load_dataset(self.root)
        self.assertIn("amount", str(ctx.exception))

    def test_duplicate_contract(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(contracts=[*VALID_CONTRACTS, *VALID_CONTRACTS])
        self.assertIn("повторно", str(ctx.exception))

    def test_duplicate_work(self) -> None:
        with self.assertRaises(DataError):
            self._load(works=[*VALID_WORKS, *VALID_WORKS])

    def test_work_references_unknown_contract(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(works=["W-1,D-9,Съёмка,1000.00,planned,,"])
        self.assertIn("неизвестный договор", str(ctx.exception))

    def test_payment_references_unknown_work(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(payments=["P-1,D-1,W-9,Аванс,1000.00,paid,2025-01-02,2025-01-03,"])
        self.assertIn("неизвестную работу", str(ctx.exception))

    def test_payment_work_from_other_contract(self) -> None:
        contracts = ["D-1,ООО «Ромашка»,1000.00,2025-01-01", "D-2,ИП Иванов,500.00,2025-01-01"]
        works = ["W-1,D-1,Съёмка,1000.00,planned,,", "W-2,D-2,Логотип,500.00,planned,,"]
        payments = ["P-1,D-2,W-1,Аванс,100.00,paid,2025-01-02,2025-01-03,"]
        with self.assertRaises(DataError) as ctx:
            self._load(contracts=contracts, works=works, payments=payments)
        self.assertIn("относится к договору", str(ctx.exception))

    def test_unknown_work_status(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(works=["W-1,D-1,Съёмка,1000.00,in_progress,,"])
        self.assertIn("неизвестный статус", str(ctx.exception))

    def test_unknown_payment_status(self) -> None:
        with self.assertRaises(DataError):
            self._load(payments=["P-1,D-1,,Аванс,1000.00,partial,2025-01-02,2025-01-03,"])

    def test_done_requires_completed_at(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(works=["W-1,D-1,Съёмка,1000.00,done,,"])
        self.assertIn("completed_at", str(ctx.exception))

    def test_cancelled_requires_cancelled_at(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(works=["W-1,D-1,Съёмка,1000.00,cancelled,2025-02-01,"])
        self.assertIn("cancelled_at", str(ctx.exception))

    def test_planned_with_dates_is_rejected(self) -> None:
        with self.assertRaises(DataError):
            self._load(works=["W-1,D-1,Съёмка,1000.00,planned,2025-02-01,"])

    def test_paid_requires_paid_at(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(payments=["P-1,D-1,,Аванс,1000.00,paid,2025-01-02,,"])
        self.assertIn("paid_at", str(ctx.exception))

    def test_refunded_requires_refund_date(self) -> None:
        with self.assertRaises(DataError) as ctx:
            self._load(payments=["P-1,D-1,,Аванс,1000.00,refunded,2025-01-02,2025-01-03,"])
        self.assertIn("refunded_at", str(ctx.exception))

    def test_refund_before_payment_is_rejected(self) -> None:
        with self.assertRaises(DataError):
            self._load(payments=["P-1,D-1,,Аванс,1000.00,refunded,2025-01-02,2025-01-05,2025-01-04"])

    def test_queued_with_paid_at_is_rejected(self) -> None:
        with self.assertRaises(DataError):
            self._load(payments=["P-1,D-1,,Аванс,1000.00,queued,2025-01-02,2025-01-03,"])

    def test_negative_amount_is_rejected(self) -> None:
        with self.assertRaises(DataError):
            self._load(works=["W-1,D-1,Съёмка,-1000.00,planned,,"])

    def test_amount_mismatch_is_warning(self) -> None:
        dataset = self._load(contracts=["D-1,ООО «Ромашка»,1200.00,2025-01-01"])
        self.assertEqual(len(dataset.warnings), 1)
        self.assertIn("не равна", dataset.warnings[0])

    def test_bom_and_semicolon(self) -> None:
        header_c = support.CONTRACTS_HEADER.replace(",", ";")
        header_w = support.WORKS_HEADER.replace(",", ";")
        header_p = support.PAYMENTS_HEADER.replace(",", ";")
        for name, header, rows in (
            ("contracts.csv", header_c, VALID_CONTRACTS),
            ("works.csv", header_w, VALID_WORKS),
            ("payments.csv", header_p, VALID_PAYMENTS),
        ):
            body = "\n".join(row.replace(",", ";") for row in rows)
            (self.root / name).write_text(header + "\n" + body + "\n", encoding="utf-8-sig")
        dataset = load_dataset(self.root)
        self.assertEqual(len(dataset.contracts), 1)

    def test_empty_trailing_line_is_ignored(self) -> None:
        dataset = self._load(works=[*VALID_WORKS, ""])
        self.assertEqual(list(dataset.works), ["W-1"])

    def test_real_data_loads_without_warnings(self) -> None:
        dataset = load_dataset(support.DATA_DIR)
        self.assertEqual(sorted(dataset.contracts), ["D-1", "D-2"])
        self.assertEqual(len(dataset.works), 9)
        self.assertEqual(len(dataset.payments), 8)
        self.assertEqual(dataset.warnings, [])


if __name__ == "__main__":
    unittest.main()
