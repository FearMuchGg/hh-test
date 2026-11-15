"""Тесты расчёта: состояние на дату, распределение выплат, итоги.

Контрольный набор — реальная выгрузка из ``data/`` на 10.11.2026. Цифры в
``RealDataTests`` посчитаны вручную по строкам CSV и служат эталоном: если
изменится логика распределения или трактовка дат, эти тесты упадут.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import support
from advance_report.engine import (
    AFTER,
    CANCELLED,
    DONE,
    PAID,
    PENDING,
    PLANNED,
    QUEUED,
    REFUNDED,
    build_report,
    effective_work_status,
    payment_state,
)
from advance_report.loading import load_dataset

AS_OF = date(2026, 11, 10)


class RealDataTests(unittest.TestCase):
    """Эталонные цифры по выгрузке на 10.11.2026."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_report(load_dataset(support.DATA_DIR), AS_OF)
        cls.works = {row.work_id: row for row in cls.report.works}
        cls.contracts = {row.contract_id: row for row in cls.report.contracts}
        cls.payments = {row.payment_id: row for row in cls.report.payments}

    def test_works(self) -> None:
        expected = [
            ("W-11", DONE, "100000.00", "100000.00", "0.00"),
            ("W-12", DONE, "100000.00", "100000.00", "0.00"),
            ("W-13", CANCELLED, "100000.00", "0.00", "0.00"),
            ("W-14", DONE, "100000.00", "100000.00", "0.00"),
            ("W-15", PLANNED, "100000.00", "15000.00", "85000.00"),
            ("W-16", PLANNED, "100000.00", "0.00", "100000.00"),
            ("W-21", DONE, "20000.00", "20000.00", "0.00"),
            ("W-22", DONE, "30000.00", "30000.00", "0.00"),
            ("W-23", PENDING, "40000.00", "25000.00", "15000.00"),
        ]
        for work_id, status, price, paid, remaining in expected:
            with self.subTest(work_id=work_id):
                row = self.works[work_id]
                self.assertEqual(row.status, status)
                self.assertEqual(row.price, Decimal(price))
                self.assertEqual(row.paid, Decimal(paid))
                self.assertEqual(row.remaining, Decimal(remaining))

    def test_cancelled_work_note(self) -> None:
        self.assertIn("отменена 12.10.2026", self.works["W-13"].note)
        self.assertEqual(self.works["W-13"].payable, Decimal("0.00"))

    def test_work_accepted_after_report_date(self) -> None:
        row = self.works["W-23"]
        self.assertEqual(row.status, PENDING)
        self.assertIn("11.11.2026", row.note)

    def test_source_status_is_kept(self) -> None:
        """Исходный статус выгрузки сохраняется рядом с производным статусом на дату."""
        self.assertEqual(self.works["W-23"].source_status, "done")
        self.assertEqual(self.works["W-13"].source_status, "cancelled")

    def test_advance_note_on_planned_work(self) -> None:
        """У запланированной работы с деньгами есть пояснение, откуда аванс."""
        self.assertIn("аванс вперёд приёмки", self.works["W-15"].note)
        self.assertEqual(self.works["W-16"].note, "")

    def test_queue_check_line(self) -> None:
        text = "\n".join(self.report.checks)
        self.assertIn("в очереди 150 000,00", text)
        self.assertIn("останется 35 000,00", text)

    def test_contracts(self) -> None:
        d1 = self.contracts["D-1"]
        self.assertEqual(d1.amount, Decimal("600000.00"))
        self.assertEqual(d1.cancelled_total, Decimal("100000.00"))
        self.assertEqual(d1.payable, Decimal("500000.00"))
        self.assertEqual(d1.paid_total, Decimal("315000.00"))
        self.assertEqual(d1.remaining, Decimal("185000.00"))
        self.assertEqual(d1.overpaid, Decimal("0.00"))
        self.assertEqual(d1.queued_total, Decimal("150000.00"))
        self.assertEqual(d1.refunded_total, Decimal("50000.00"))
        self.assertEqual(d1.unallocated, Decimal("0.00"))
        self.assertEqual(d1.accepted_total, Decimal("300000.00"))
        self.assertEqual(d1.accepted_paid, Decimal("300000.00"))
        self.assertEqual(d1.accepted_remaining, Decimal("0.00"))
        self.assertEqual(d1.advance_ahead, Decimal("15000.00"))

        d2 = self.contracts["D-2"]
        self.assertEqual(d2.amount, Decimal("90000.00"))
        self.assertEqual(d2.cancelled_total, Decimal("0.00"))
        self.assertEqual(d2.payable, Decimal("90000.00"))
        self.assertEqual(d2.paid_total, Decimal("75000.00"))
        self.assertEqual(d2.remaining, Decimal("15000.00"))
        self.assertEqual(d2.queued_total, Decimal("0.00"))
        self.assertEqual(d2.accepted_total, Decimal("50000.00"))
        self.assertEqual(d2.advance_ahead, Decimal("25000.00"))

    def test_totals(self) -> None:
        totals = self.report.totals
        self.assertEqual(totals.contracts, 2)
        self.assertEqual(totals.works, 9)
        self.assertEqual(totals.amount, Decimal("690000.00"))
        self.assertEqual(totals.payable, Decimal("590000.00"))
        self.assertEqual(totals.paid_total, Decimal("390000.00"))
        self.assertEqual(totals.remaining, Decimal("200000.00"))
        self.assertEqual(totals.queued_total, Decimal("150000.00"))
        self.assertEqual(totals.unallocated, Decimal("0.00"))

    def test_included_payments(self) -> None:
        included = sorted(pid for pid, row in self.payments.items() if row.included)
        self.assertEqual(included, ["P-1", "P-3", "P-5", "P-6", "P-7"])

    def test_refunded_and_queued_and_future_are_excluded(self) -> None:
        self.assertFalse(self.payments["P-2"].included)  # возврат 10.09.2026
        self.assertFalse(self.payments["P-4"].included)  # в очереди
        self.assertFalse(self.payments["P-8"].included)  # оплачен 12.11.2026
        self.assertEqual(self.payments["P-2"].state, REFUNDED)
        self.assertEqual(self.payments["P-4"].state, QUEUED)
        self.assertEqual(self.payments["P-8"].state, AFTER)

    def test_payment_allocation_sources(self) -> None:
        self.assertEqual(
            self.works["W-14"].sources,
            (("P-3", Decimal("15000.00"), "прямой"), ("P-1", Decimal("85000.00"), "из платежа по договору")),
        )
        self.assertEqual(
            self.works["W-15"].sources,
            (("P-1", Decimal("15000.00"), "из платежа по договору"),),
        )
        self.assertEqual(
            self.works["W-22"].sources,
            (
                ("P-5", Decimal("25000.00"), "из платежа по договору"),
                ("P-6", Decimal("5000.00"), "из платежа по договору"),
            ),
        )
        self.assertEqual(
            self.works["W-23"].sources,
            (
                ("P-6", Decimal("20000.00"), "из платежа по договору"),
                ("P-7", Decimal("5000.00"), "из платежа по договору"),
            ),
        )

    def test_refund_window(self) -> None:
        """До возврата (05.09.2026) ошибочный аванс ещё считается выплатой."""
        before_refund = build_report(load_dataset(support.DATA_DIR), date(2026, 9, 5))
        d1 = {row.contract_id: row for row in before_refund.contracts}["D-1"]
        self.assertEqual(d1.paid_total, Decimal("350000.00"))

        on_refund_day = {row.contract_id: row for row in build_report(load_dataset(support.DATA_DIR), date(2026, 9, 10)).contracts}["D-1"]
        self.assertEqual(on_refund_day.paid_total, Decimal("300000.00"))

    def test_after_date_notes(self) -> None:
        text = "\n".join(self.report.after_date)
        self.assertIn("P-8", text)
        self.assertIn("W-23", text)
        self.assertIn("переплата 5 000,00", text)

    def test_report_invariants(self) -> None:
        for contract in self.report.contracts:
            rows = [row for row in self.report.works if row.contract_id == contract.contract_id]
            self.assertEqual(
                contract.paid_total,
                sum((row.paid for row in rows), Decimal("0.00")) + contract.unallocated,
            )
            for row in rows:
                if row.status == CANCELLED:
                    self.assertEqual(row.payable, Decimal("0.00"))
                    continue
                self.assertEqual(row.paid + row.remaining, row.price)
                sources_sum = sum((amount for _, amount, _ in row.sources), Decimal("0.00"))
                self.assertEqual(sources_sum, row.paid)

    def test_report_is_reproducible(self) -> None:
        again = build_report(load_dataset(support.DATA_DIR), AS_OF)
        self.assertEqual(again.works, self.report.works)
        self.assertEqual(again.contracts, self.report.contracts)
        self.assertEqual(again.payments, self.report.payments)


class StatusTests(unittest.TestCase):
    """Восстановление состояния по датам событий, а не по колонке status."""

    def test_future_completion_is_pending(self) -> None:
        item = support.work(status="done", completed_at=date(2026, 11, 11))
        self.assertEqual(effective_work_status(item, AS_OF), PENDING)
        self.assertEqual(effective_work_status(item, date(2026, 11, 11)), DONE)

    def test_future_cancellation_is_planned(self) -> None:
        item = support.work(status="cancelled", cancelled_at=date(2026, 11, 12))
        self.assertEqual(effective_work_status(item, AS_OF), PLANNED)

    def test_cancelled_before_date(self) -> None:
        item = support.work(status="cancelled", cancelled_at=date(2026, 10, 12))
        self.assertEqual(effective_work_status(item, AS_OF), CANCELLED)

    def test_payment_states(self) -> None:
        paid = support.payment(status="paid", paid_at=date(2026, 10, 1), created_at=date(2026, 10, 1))
        self.assertEqual(payment_state(paid, AS_OF)[0], PAID)

        future = support.payment(status="paid", paid_at=date(2026, 11, 12), created_at=date(2026, 11, 12))
        self.assertEqual(payment_state(future, AS_OF)[0], AFTER)

        queued = support.payment(status="queued", created_at=date(2026, 11, 1))
        self.assertEqual(payment_state(queued, AS_OF)[0], QUEUED)

        refunded = support.payment(
            status="refunded",
            paid_at=date(2026, 9, 2),
            refunded_at=date(2026, 9, 10),
            created_at=date(2026, 9, 2),
        )
        self.assertEqual(payment_state(refunded, AS_OF)[0], REFUNDED)
        self.assertEqual(payment_state(refunded, date(2026, 9, 5))[0], PAID)


class AllocationTests(unittest.TestCase):
    """Распределение непривязанных платежей и граничные случаи."""

    def test_fifo_by_completion_date_not_by_id(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="200.00")],
            [
                support.work("W-1", price="100.00", status="done", completed_at=date(2026, 2, 1)),
                support.work("W-2", price="100.00", status="done", completed_at=date(2026, 1, 1)),
            ],
            [support.payment("P-1", amount="150.00", created_at=date(2026, 1, 5))],
        )
        report = build_report(dataset, date(2026, 3, 1))
        paid = {row.work_id: row.paid for row in report.works}
        self.assertEqual(paid["W-2"], Decimal("100.00"))
        self.assertEqual(paid["W-1"], Decimal("50.00"))

    def test_direct_payment_has_priority(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="200.00")],
            [
                support.work("W-1", price="100.00", status="done", completed_at=date(2026, 2, 1)),
                support.work("W-2", price="100.00", status="done", completed_at=date(2026, 1, 1)),
            ],
            [
                support.payment("P-1", amount="50.00", created_at=date(2026, 1, 2)),
                support.payment(
                    "P-2", amount="100.00", work_id="W-1", created_at=date(2026, 2, 2)
                ),
            ],
        )
        report = build_report(dataset, date(2026, 3, 1))
        paid = {row.work_id: row.paid for row in report.works}
        # W-1 закрыта прямым платежом, котёл ушёл на W-2.
        self.assertEqual(paid["W-1"], Decimal("100.00"))
        self.assertEqual(paid["W-2"], Decimal("50.00"))

    def test_cancelled_work_is_not_payable(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="200.00")],
            [
                support.work("W-1", price="100.00", status="planned"),
                support.work("W-2", price="100.00", status="cancelled", cancelled_at=date(2026, 1, 1)),
            ],
            [support.payment("P-1", amount="50.00", created_at=date(2026, 1, 5))],
        )
        report = build_report(dataset, date(2026, 3, 1))
        contract = report.contracts[0]
        self.assertEqual(contract.cancelled_total, Decimal("100.00"))
        self.assertEqual(contract.payable, Decimal("100.00"))
        self.assertEqual(contract.paid_total, Decimal("50.00"))
        self.assertEqual(contract.remaining, Decimal("50.00"))
        rows = {row.work_id: row for row in report.works}
        self.assertEqual(rows["W-2"].payable, Decimal("0.00"))
        self.assertEqual(rows["W-2"].remaining, Decimal("0.00"))

    def test_future_payment_not_counted(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="100.00")],
            [support.work("W-1", price="100.00", status="done", completed_at=date(2026, 10, 1))],
            [support.payment("P-1", amount="100.00", created_at=date(2026, 11, 12), paid_at=date(2026, 11, 12))],
        )
        report = build_report(dataset, AS_OF)
        self.assertEqual(report.contracts[0].paid_total, Decimal("0.00"))
        self.assertEqual(report.contracts[0].remaining, Decimal("100.00"))
        self.assertTrue(any("P-1" in note for note in report.after_date))

    def test_advance_goes_to_unaccepted_work(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="200.00")],
            [
                support.work("W-1", price="100.00", status="done", completed_at=date(2026, 10, 1)),
                support.work("W-2", price="100.00", status="done", completed_at=date(2026, 11, 11)),
            ],
            [support.payment("P-1", amount="150.00", created_at=date(2026, 10, 5))],
        )
        report = build_report(dataset, AS_OF)
        rows = {row.work_id: row for row in report.works}
        self.assertEqual(rows["W-2"].status, PENDING)
        self.assertEqual(rows["W-2"].paid, Decimal("50.00"))
        self.assertEqual(report.contracts[0].advance_ahead, Decimal("50.00"))
        self.assertEqual(report.contracts[0].accepted_remaining, Decimal("0.00"))

    def test_advance_above_all_works_is_unallocated(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="100.00")],
            [support.work("W-1", price="100.00", status="planned")],
            [support.payment("P-1", amount="500.00", created_at=date(2026, 1, 5))],
        )
        report = build_report(dataset, date(2026, 3, 1))
        contract = report.contracts[0]
        self.assertEqual(contract.paid_total, Decimal("500.00"))
        self.assertEqual(contract.unallocated, Decimal("400.00"))
        self.assertEqual(contract.overpaid, Decimal("400.00"))
        self.assertEqual(contract.remaining, Decimal("0.00"))
        self.assertTrue(any("переплата" in warning for warning in report.warnings))

    def test_direct_payment_to_cancelled_work_goes_to_pool(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="200.00")],
            [
                support.work("W-1", price="100.00", status="planned"),
                support.work("W-2", price="100.00", status="cancelled", cancelled_at=date(2026, 1, 1)),
            ],
            [support.payment("P-1", amount="40.00", work_id="W-2", created_at=date(2026, 1, 5))],
        )
        report = build_report(dataset, date(2026, 3, 1))
        rows = {row.work_id: row for row in report.works}
        self.assertEqual(rows["W-2"].paid, Decimal("0.00"))
        self.assertEqual(rows["W-1"].paid, Decimal("40.00"))
        self.assertTrue(any("отменённой" in warning for warning in report.warnings))

    def test_kopecks_are_exact(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="99.99")],
            [
                support.work("W-1", price="33.33", status="planned"),
                support.work("W-2", price="33.33", status="planned"),
                support.work("W-3", price="33.33", status="planned"),
            ],
            [support.payment("P-1", amount="100.00", created_at=date(2026, 1, 5))],
        )
        report = build_report(dataset, date(2026, 3, 1))
        paid = [row.paid for row in report.works]
        self.assertEqual(paid, [Decimal("33.33")] * 3)
        self.assertEqual(report.contracts[0].unallocated, Decimal("0.01"))
        self.assertEqual(report.contracts[0].overpaid, Decimal("0.01"))
        self.assertEqual(report.contracts[0].paid_total, Decimal("100.00"))

    def test_contract_without_works(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="1000.00")],
            [],
            [support.payment("P-1", amount="300.00", created_at=date(2026, 1, 5))],
        )
        report = build_report(dataset, date(2026, 3, 1))
        contract = report.contracts[0]
        self.assertEqual(contract.payable, Decimal("0.00"))
        self.assertEqual(contract.paid_total, Decimal("300.00"))
        self.assertEqual(contract.unallocated, Decimal("300.00"))

    def test_no_payments(self) -> None:
        dataset = support.dataset(
            [support.contract(amount="100.00")],
            [support.work("W-1", price="100.00", status="planned")],
            [],
        )
        report = build_report(dataset, date(2026, 3, 1))
        contract = report.contracts[0]
        self.assertEqual(contract.paid_total, Decimal("0.00"))
        self.assertEqual(contract.remaining, Decimal("100.00"))
        self.assertEqual(contract.advance_ahead, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
