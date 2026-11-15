"""Печать отчёта: консоль, Markdown, CSV и JSON.

Все форматы строятся из одного и того же :class:`~advance_report.engine.Report`,
поэтому цифры в них совпадают по построению. Форматы отличаются только
удобством: консоль — посмотреть глазами, Markdown — переслать, CSV — открыть
в Excel, JSON — обработать программно.
"""

from __future__ import annotations

import csv as csv_module
import json
from decimal import Decimal
from pathlib import Path

from .engine import (
    PAYMENT_STATE_LABELS,
    PAYMENT_STATUS_LABELS,
    ContractRow,
    Report,
    Totals,
    WorkRow,
)
from .formatting import fmt_ru

RUB = "руб."


# --- Вспомогательные таблицы ------------------------------------------------


def _console_table(headers: list[str], rows: list[list[str]], right: set[int]) -> list[str]:
    """Текстовая таблица с выравниванием по ширине колонок."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: list[str]) -> str:
        parts = [
            cell.rjust(widths[index]) if index in right else cell.ljust(widths[index])
            for index, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    result = [line(headers), "  ".join("-" * width for width in widths).rstrip()]
    result.extend(line(row) for row in rows)
    return result


def _md_table(headers: list[str], rows: list[list[str]], right: set[int]) -> list[str]:
    """Таблица Markdown; в ячейках экранируется вертикальная черта."""
    escaped = [[cell.replace("|", "\\|") for cell in row] for row in rows]

    def fmt(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    separator = ["---:" if index in right else "---" for index in range(len(headers))]
    return [fmt(headers), fmt(separator), *[fmt(row) for row in escaped]]


def _money(value: Decimal) -> str:
    """Сумма в русской записи: 315 000,00."""
    return fmt_ru(value)


def _minuses(report: Report) -> list[str]:
    """Общие строки итогов, одинаковые для консоли и Markdown."""
    totals = report.totals
    lines = [
        f"Итого: сумма договоров {_money(totals.amount)}, к оплате {_money(totals.payable)}, "
        f"выплачено {_money(totals.paid_total)}, осталось оплатить {_money(totals.remaining)}"
    ]
    if totals.overpaid > Decimal("0.00"):
        lines.append(f"Переплата по договорам: {_money(totals.overpaid)}")
    if totals.queued_total > Decimal("0.00"):
        lines.append(f"В очереди на оплату (в выплаты не входит): {_money(totals.queued_total)}")
    if totals.unallocated > Decimal("0.00"):
        lines.append(f"Не распределено по работам (аванс без работ): {_money(totals.unallocated)}")
    return lines


# --- Общие наборы строк -----------------------------------------------------


def _works_rows(report: Report) -> list[list[str]]:
    return [
        [
            row.contract_id,
            row.work_id,
            row.title,
            row.status_label,
            _money(row.price),
            _money(row.paid),
            _money(row.remaining),
            row.note,
        ]
        for row in report.works
    ]


def _contracts_rows(report: Report) -> list[list[str]]:
    return [
        [
            row.contract_id,
            row.contractor,
            _money(row.amount),
            _money(row.cancelled_total),
            _money(row.payable),
            _money(row.paid_total),
            _money(row.remaining),
            _money(row.overpaid),
            _money(row.queued_total),
        ]
        for row in report.contracts
    ]


def _accepted_rows(report: Report) -> list[list[str]]:
    return [
        [
            row.contract_id,
            _money(row.accepted_total),
            _money(row.accepted_paid),
            _money(row.accepted_remaining),
            _money(row.advance_ahead),
        ]
        for row in report.contracts
    ]


def _payments_rows(report: Report) -> list[list[str]]:
    return [
        [
            row.payment_id,
            row.contract_id,
            row.work_id or "—",
            row.purpose,
            _money(row.amount),
            PAYMENT_STATUS_LABELS.get(row.status, row.status),
            "да" if row.included else "нет",
            row.reason,
        ]
        for row in report.payments
    ]


def _sources_rows(report: Report) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in report.works:
        if not row.sources:
            continue
        parts = [f"{payment_id}: {_money(amount)} ({kind})" for payment_id, amount, kind in row.sources]
        rows.append([row.work_id, _money(row.price), _money(row.paid), "; ".join(parts)])
    return rows


def _bullets(items: tuple[str, ...] | list[str], empty: str = "нет") -> list[str]:
    return [f"- {item}" for item in items] if items else [f"- {empty}"]


# --- Консоль ----------------------------------------------------------------


def render_console(report: Report) -> str:
    lines: list[str] = [
        "ВЫПЛАТЫ ПО ДОГОВОРАМ И РАБОТАМ",
        f"Отчётная дата: {report.as_of.strftime('%d.%m.%Y')} (день включительно). Валюта: {RUB}",
        f"Данные: {report.data_dir}",
        "",
        "РАБОТЫ",
    ]

    lines.extend(
        _console_table(
            ["Договор", "Работа", "Название", "Статус на дату", "Цена", "Выплачено", "Осталось", "Примечание"],
            _works_rows(report),
            right={4, 5, 6},
        )
    )

    lines.extend(["", "ДОГОВОРЫ"])
    lines.extend(
        _console_table(
            [
                "Договор",
                "Подрядчик",
                "Сумма договора",
                "Отменено",
                "К оплате",
                "Выплачено",
                "Осталось",
                "Переплата",
                "В очереди",
            ],
            _contracts_rows(report),
            right={2, 3, 4, 5, 6, 7, 8},
        )
    )
    lines.append("")
    lines.extend(_minuses(report))

    lines.extend(["", "ДОЛГ ПО ПРИНЯТЫМ РАБОТАМ"])
    lines.extend(
        _console_table(
            ["Договор", "Принято работ", "Оплачено принятых", "Долг по принятым", "Аванс сверх принятого"],
            _accepted_rows(report),
            right={1, 2, 3, 4},
        )
    )

    lines.extend(["", "ПЛАТЕЖИ"])
    lines.extend(
        _console_table(
            ["Платёж", "Договор", "Работа", "Назначение", "Сумма", "Статус", "Учтён", "Комментарий"],
            _payments_rows(report),
            right={4},
        )
    )

    lines.extend(["", "ИЗ ЧЕГО СЛОЖИЛИСЬ ВЫПЛАТЫ ПО РАБОТАМ"])
    sources = _sources_rows(report)
    lines.extend(
        _console_table(
            ["Работа", "Цена", "Выплачено", "Источники"],
            sources,
            right={1, 2},
        )
        if sources
        else ["- выплат нет"]
    )

    lines.extend(["", "ПРОВЕРКИ (суммы должны сходиться)"])
    lines.extend(_bullets(report.checks))

    lines.extend(["", "СОБЫТИЯ ПОСЛЕ ОТЧЁТНОЙ ДАТЫ (справочно, в расчёт не входят)"])
    lines.extend(_bullets(report.after_date))

    lines.extend(["", "ПРЕДУПРЕЖДЕНИЯ"])
    lines.extend(_bullets(report.warnings))

    return "\n".join(lines) + "\n"


# --- Markdown ---------------------------------------------------------------


def render_markdown(report: Report) -> str:
    lines: list[str] = [
        "# Отчёт: выплаты по договорам и работам",
        "",
        f"**Отчётная дата:** {report.as_of.strftime('%d.%m.%Y')} (день включительно)",
        f"**Валюта:** {RUB}",
        f"**Данные:** `{report.data_dir}`",
        "",
        "## Работы",
        "",
    ]
    lines.extend(
        _md_table(
            ["Договор", "Работа", "Название", "Статус на дату", "Цена", "Выплачено", "Осталось", "Примечание"],
            _works_rows(report),
            right={4, 5, 6},
        )
    )
    lines.extend(["", "## Договоры", ""])
    lines.extend(
        _md_table(
            [
                "Договор",
                "Подрядчик",
                "Сумма договора",
                "Отменено",
                "К оплате",
                "Выплачено",
                "Осталось",
                "Переплата",
                "В очереди",
            ],
            _contracts_rows(report),
            right={2, 3, 4, 5, 6, 7, 8},
        )
    )
    lines.extend(["", "**Итоги:**", ""])
    lines.extend(f"- {line}" for line in _minuses(report))
    lines.extend(["", "## Долг по принятым работам", ""])
    lines.extend(
        _md_table(
            ["Договор", "Принято работ", "Оплачено принятых", "Долг по принятым", "Аванс сверх принятого"],
            _accepted_rows(report),
            right={1, 2, 3, 4},
        )
    )
    lines.extend(["", "## Платежи, из которых собран отчёт", ""])
    lines.extend(
        _md_table(
            ["Платёж", "Договор", "Работа", "Назначение", "Сумма", "Статус", "Учтён", "Комментарий"],
            _payments_rows(report),
            right={4},
        )
    )
    lines.extend(["", "## Из чего сложились выплаты по работам", ""])
    sources = _sources_rows(report)
    lines.extend(
        _md_table(["Работа", "Цена", "Выплачено", "Источники"], sources, right={1, 2})
        if sources
        else ["_Выплат нет._"]
    )
    lines.extend(["", "## Проверки", ""])
    lines.extend(_bullets(report.checks))
    lines.extend(["", "## События после отчётной даты", ""])
    lines.extend(_bullets(report.after_date))
    lines.extend(["", "## Предупреждения", ""])
    lines.extend(_bullets(report.warnings))
    return "\n".join(lines) + "\n"


# --- JSON -------------------------------------------------------------------


def _money_json(value: Decimal) -> str:
    """Деньги в JSON — строкой: так копейки не превращаются в float."""
    return f"{value:.2f}"


def _contract_dict(row: ContractRow) -> dict[str, object]:
    return {
        "contract_id": row.contract_id,
        "contractor": row.contractor,
        "signed_at": row.signed_at.isoformat(),
        "amount": _money_json(row.amount),
        "works_total": _money_json(row.works_total),
        "cancelled_total": _money_json(row.cancelled_total),
        "payable": _money_json(row.payable),
        "accepted_total": _money_json(row.accepted_total),
        "accepted_paid": _money_json(row.accepted_paid),
        "accepted_remaining": _money_json(row.accepted_remaining),
        "advance_ahead": _money_json(row.advance_ahead),
        "paid_total": _money_json(row.paid_total),
        "remaining": _money_json(row.remaining),
        "overpaid": _money_json(row.overpaid),
        "queued_total": _money_json(row.queued_total),
        "refunded_total": _money_json(row.refunded_total),
        "unallocated": _money_json(row.unallocated),
    }


def _work_dict(row: WorkRow) -> dict[str, object]:
    return {
        "contract_id": row.contract_id,
        "work_id": row.work_id,
        "title": row.title,
        # status_as_of — производный статус на отчётную дату; status_source —
        # то, что стоит в выгрузке. Разделены, чтобы потребитель не принял
        # «pending» за новый статус исходного формата.
        "status_as_of": row.status,
        "status_source": row.source_status,
        "status_label": row.status_label,
        "price": _money_json(row.price),
        "payable": _money_json(row.payable),
        "paid": _money_json(row.paid),
        "paid_direct": _money_json(row.paid_direct),
        "paid_from_contract": _money_json(row.paid_from_contract),
        "remaining": _money_json(row.remaining),
        "overpay": _money_json(row.overpay),
        "sources": [
            {"payment_id": payment_id, "amount": _money_json(amount), "kind": kind}
            for payment_id, amount, kind in row.sources
        ],
        "note": row.note,
    }


def _payment_dict(row) -> dict[str, object]:
    return {
        "payment_id": row.payment_id,
        "contract_id": row.contract_id,
        "work_id": row.work_id,
        "purpose": row.purpose,
        "amount": _money_json(row.amount),
        "status": row.status,
        "state": row.state,
        "state_label": PAYMENT_STATE_LABELS[row.state],
        "created_at": row.created_at.isoformat(),
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
        "refunded_at": row.refunded_at.isoformat() if row.refunded_at else None,
        "included": row.included,
        "reason": row.reason,
    }


def _totals_dict(totals: Totals) -> dict[str, object]:
    return {
        "contracts": totals.contracts,
        "works": totals.works,
        "amount": _money_json(totals.amount),
        "works_total": _money_json(totals.works_total),
        "cancelled_total": _money_json(totals.cancelled_total),
        "payable": _money_json(totals.payable),
        "accepted_total": _money_json(totals.accepted_total),
        "accepted_paid": _money_json(totals.accepted_paid),
        "accepted_remaining": _money_json(totals.accepted_remaining),
        "advance_ahead": _money_json(totals.advance_ahead),
        "paid_total": _money_json(totals.paid_total),
        "remaining": _money_json(totals.remaining),
        "overpaid": _money_json(totals.overpaid),
        "queued_total": _money_json(totals.queued_total),
        "refunded_total": _money_json(totals.refunded_total),
        "unallocated": _money_json(totals.unallocated),
    }


def render_json(report: Report) -> str:
    payload = {
        "meta": {
            "report": "выплаты по договорам и работам",
            "as_of": report.as_of.isoformat(),
            "currency": "RUB",
            "data_dir": report.data_dir,
            "note": "деньги — строки с двумя знаками; разделитель дробной части — точка",
        },
        "totals": _totals_dict(report.totals),
        "contracts": [_contract_dict(row) for row in report.contracts],
        "works": [_work_dict(row) for row in report.works],
        "payments": [_payment_dict(row) for row in report.payments],
        "checks": list(report.checks),
        "warnings": list(report.warnings),
        "after_date": list(report.after_date),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# --- CSV --------------------------------------------------------------------


def _csv_amount(value: Decimal, decimal_comma: bool) -> str:
    text = f"{value:.2f}"
    return text.replace(".", ",") if decimal_comma else text


def _write_csv(path: Path, headers: list[str], rows: list[list[str]], delimiter: str) -> Path:
    """CSV с BOM: Excel в русской локали открывает такой файл без настройки."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv_module.writer(handle, delimiter=delimiter, lineterminator="\r\n")
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def write_csv_files(
    report: Report,
    out_dir: Path,
    *,
    delimiter: str = ";",
    decimal_comma: bool = True,
) -> list[Path]:
    """Записать три CSV: работы, договоры, платежи. Вернуть пути файлов."""
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = report.as_of.isoformat()
    amount = lambda value: _csv_amount(value, decimal_comma)  # noqa: E731 — короткий адаптер формата

    works_rows = [
        [
            row.contract_id,
            row.work_id,
            row.title,
            row.status_label,
            amount(row.price),
            amount(row.paid),
            amount(row.paid_direct),
            amount(row.paid_from_contract),
            amount(row.remaining),
            amount(row.overpay),
            row.note,
        ]
        for row in report.works
    ]
    contracts_rows = [
        [
            row.contract_id,
            row.contractor,
            row.signed_at.isoformat(),
            amount(row.amount),
            amount(row.works_total),
            amount(row.cancelled_total),
            amount(row.payable),
            amount(row.accepted_total),
            amount(row.accepted_paid),
            amount(row.accepted_remaining),
            amount(row.advance_ahead),
            amount(row.paid_total),
            amount(row.remaining),
            amount(row.overpaid),
            amount(row.queued_total),
            amount(row.refunded_total),
            amount(row.unallocated),
        ]
        for row in report.contracts
    ]
    payments_rows = [
        [
            row.payment_id,
            row.contract_id,
            row.work_id or "",
            row.purpose,
            amount(row.amount),
            row.status,
            "да" if row.included else "нет",
            row.paid_at.isoformat() if row.paid_at else "",
            row.refunded_at.isoformat() if row.refunded_at else "",
            row.reason,
        ]
        for row in report.payments
    ]

    return [
        _write_csv(
            out_dir / f"works_{suffix}.csv",
            [
                "contract_id",
                "work_id",
                "title",
                "status_as_of",
                "price",
                "paid",
                "paid_direct",
                "paid_from_contract",
                "remaining",
                "overpay",
                "note",
            ],
            works_rows,
            delimiter,
        ),
        _write_csv(
            out_dir / f"contracts_{suffix}.csv",
            [
                "contract_id",
                "contractor",
                "signed_at",
                "amount",
                "works_total",
                "cancelled_total",
                "payable",
                "accepted_total",
                "accepted_paid",
                "accepted_remaining",
                "advance_ahead",
                "paid_total",
                "remaining",
                "overpaid",
                "queued_total",
                "refunded_total",
                "unallocated",
            ],
            contracts_rows,
            delimiter,
        ),
        _write_csv(
            out_dir / f"payments_{suffix}.csv",
            [
                "payment_id",
                "contract_id",
                "work_id",
                "purpose",
                "amount",
                "status",
                "included",
                "paid_at",
                "refunded_at",
                "reason",
            ],
            payments_rows,
            delimiter,
        ),
    ]


def report_date_suffix(report: Report) -> str:
    """Суффикс имён файлов отчёта: ISO-дата."""
    return report.as_of.isoformat()
