"""Чтение и проверка выгрузки из ``data/``.

Принципы:

* **Никаких молчаливых допущений.** Если строка противоречит формату
  (неизвестный статус, платёж на чужую работу, ``paid`` без даты оплаты),
  поднимается :class:`~advance_report.errors.DataError` с файлом, строкой и
  колонкой. Лучше не построить отчёт, чем построить неверный.
* **Мягкие расхождения — предупреждения.** Например, сумма цен работ не
  равна сумме договора: расчёт возможен, но бухгалтеру об этом надо сказать.
* **Терпимость к оформлению.** BOM, разделитель ``;`` вместо ``,`` (так
  сохраняет Excel), пробелы внутри сумм, пустая строка в конце файла —
  всё это принимается.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .errors import DataError
from .formatting import ZERO, fmt_ru, parse_amount, parse_date

#: Статусы работ по описанию формата.
WORK_STATUSES = ("planned", "done", "cancelled")

#: Статусы платежей по описанию формата.
PAYMENT_STATUSES = ("paid", "queued", "refunded")

#: Обязательные колонки каждого файла (порядок в файле не важен).
CONTRACT_COLUMNS = ("contract_id", "contractor", "amount", "signed_at")
WORK_COLUMNS = ("work_id", "contract_id", "title", "price", "status", "completed_at", "cancelled_at")
PAYMENT_COLUMNS = (
    "payment_id",
    "contract_id",
    "work_id",
    "purpose",
    "amount",
    "status",
    "created_at",
    "paid_at",
    "refunded_at",
)


@dataclass(frozen=True)
class Contract:
    """Договор с подрядчиком: общая сумма на несколько работ."""

    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass(frozen=True)
class Work:
    """Работа внутри договора."""

    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    """Платёж по договору; ``work_id`` пуст, если платёж не привязан к работе."""

    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: Decimal
    status: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None


@dataclass
class Dataset:
    """Вся выгрузка в разобранном виде, в порядке строк исходных файлов."""

    data_dir: Path
    contracts: dict[str, Contract] = field(default_factory=dict)
    works: dict[str, Work] = field(default_factory=dict)
    payments: list[Payment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def works_of(self, contract_id: str) -> list[Work]:
        """Работы договора в порядке исходного файла."""
        return [w for w in self.works.values() if w.contract_id == contract_id]

    def payments_of(self, contract_id: str) -> list[Payment]:
        """Платежи договора в порядке исходного файла."""
        return [p for p in self.payments if p.contract_id == contract_id]


def _detect_delimiter(header_line: str) -> str:
    """Определить разделитель CSV по строке заголовка.

    Excel в русской локали сохраняет CSV через ``;``, исходная выгрузка — через
    ``,``. В заголовке значения не бывает, поэтому достаточно посчитать символы.
    """
    if ";" in header_line and "," not in header_line:
        return ";"
    if "\t" in header_line and "," not in header_line:
        return "\t"
    return ","


def _read_table(path: Path, required: tuple[str, ...]) -> list[tuple[str, dict[str, str]]]:
    """Прочитать CSV и вернуть строки вместе с описанием их места в файле."""
    if not path.is_file():
        raise DataError(f"Не найден файл выгрузки: {path}")

    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError(f"{path.name}: файл не читается как UTF-8 ({exc.reason})") from exc
    except OSError as exc:
        raise DataError(f"{path.name}: не удалось прочитать файл ({exc.strerror})") from exc

    lines = text.splitlines()
    if not lines:
        raise DataError(f"{path.name}: файл пуст")

    delimiter = _detect_delimiter(lines[0])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = [(name or "").strip() for name in (reader.fieldnames or [])]

    missing = [name for name in required if name not in header]
    if missing:
        raise DataError(
            f"{path.name}: в заголовке нет обязательных колонок: {', '.join(missing)}. "
            f"Заголовок файла: {', '.join(header) or '(пусто)'}"
        )

    rows: list[tuple[str, dict[str, str]]] = []
    for raw in reader:
        # Пустые строки (типичный хвост файла) пропускаем: они не несут данных.
        if all(not isinstance(value, str) or not value.strip() for value in raw.values()):
            continue
        rows.append(
            (
                f"{path.name}:{reader.line_num}",
                {name: (raw.get(name) or "").strip() for name in required},
            )
        )
    return rows


def _parse_contracts(rows: list[tuple[str, dict[str, str]]]) -> dict[str, Contract]:
    contracts: dict[str, Contract] = {}
    for where, row in rows:
        contract_id = row["contract_id"]
        if not contract_id:
            raise DataError(f"{where}: пустой contract_id")
        if contract_id in contracts:
            raise DataError(f"{where}: договор {contract_id} встречается в файле повторно")

        contractor = row["contractor"]
        if not contractor:
            raise DataError(f"{where}: у договора {contract_id} не заполнен contractor")

        contracts[contract_id] = Contract(
            contract_id=contract_id,
            contractor=contractor,
            amount=parse_amount(row["amount"], f"{where}, колонка amount"),
            signed_at=parse_date(row["signed_at"], where, "signed_at", required=True),
        )

    if not contracts:
        raise DataError("contracts.csv: в файле нет ни одного договора")
    return contracts


def _parse_works(rows: list[tuple[str, dict[str, str]]], contracts: dict[str, Contract]) -> dict[str, Work]:
    works: dict[str, Work] = {}
    for where, row in rows:
        work_id = row["work_id"]
        if not work_id:
            raise DataError(f"{where}: пустой work_id")
        if work_id in works:
            raise DataError(f"{where}: работа {work_id} встречается в файле повторно")

        contract_id = row["contract_id"]
        if not contract_id:
            raise DataError(f"{where}: у работы {work_id} не заполнен contract_id")
        if contract_id not in contracts:
            raise DataError(f"{where}: работа {work_id} ссылается на неизвестный договор {contract_id}")

        title = row["title"]
        if not title:
            raise DataError(f"{where}: у работы {work_id} не заполнено название")

        status = row["status"]
        if status not in WORK_STATUSES:
            raise DataError(
                f"{where}: неизвестный статус работы {status!r}; "
                f"ожидается один из: {', '.join(WORK_STATUSES)}"
            )

        completed_at = parse_date(row["completed_at"], where, "completed_at", required=False)
        cancelled_at = parse_date(row["cancelled_at"], where, "cancelled_at", required=False)

        # Взаимная согласованность статуса и дат: по этим датам отчёт
        # восстанавливает состояние на отчётную дату, поэтому противоречие
        # в исходной строке делает расчёт неоднозначным.
        if status == "done":
            if completed_at is None:
                raise DataError(f"{where}: у работы {work_id} статус done, но не заполнено completed_at")
            if cancelled_at is not None:
                raise DataError(f"{where}: у работы {work_id} заполнены и completed_at, и cancelled_at")
        elif status == "cancelled":
            if cancelled_at is None:
                raise DataError(f"{where}: у работы {work_id} статус cancelled, но не заполнено cancelled_at")
            if completed_at is not None:
                raise DataError(f"{where}: у отменённой работы {work_id} заполнено completed_at")
        else:  # planned
            if completed_at is not None or cancelled_at is not None:
                raise DataError(f"{where}: у запланированной работы {work_id} заполнены даты результата")

        works[work_id] = Work(
            work_id=work_id,
            contract_id=contract_id,
            title=title,
            price=parse_amount(row["price"], f"{where}, колонка price"),
            status=status,
            completed_at=completed_at,
            cancelled_at=cancelled_at,
        )

    return works


def _parse_payments(
    rows: list[tuple[str, dict[str, str]]],
    contracts: dict[str, Contract],
    works: dict[str, Work],
) -> list[Payment]:
    payments: list[Payment] = []
    seen: set[str] = set()

    for where, row in rows:
        payment_id = row["payment_id"]
        if not payment_id:
            raise DataError(f"{where}: пустой payment_id")
        if payment_id in seen:
            raise DataError(f"{where}: платёж {payment_id} встречается в файле повторно")
        seen.add(payment_id)

        contract_id = row["contract_id"]
        if contract_id not in contracts:
            raise DataError(f"{where}: платёж {payment_id} ссылается на неизвестный договор {contract_id}")

        work_id = row["work_id"] or None
        if work_id is not None:
            if work_id not in works:
                raise DataError(f"{where}: платёж {payment_id} ссылается на неизвестную работу {work_id}")
            if works[work_id].contract_id != contract_id:
                raise DataError(
                    f"{where}: платёж {payment_id} относится к договору {contract_id}, "
                    f"а работа {work_id} — к договору {works[work_id].contract_id}"
                )

        status = row["status"]
        if status not in PAYMENT_STATUSES:
            raise DataError(
                f"{where}: неизвестный статус платежа {status!r}; "
                f"ожидается один из: {', '.join(PAYMENT_STATUSES)}"
            )

        created_at = parse_date(row["created_at"], where, "created_at", required=True)
        paid_at = parse_date(row["paid_at"], where, "paid_at", required=False)
        refunded_at = parse_date(row["refunded_at"], where, "refunded_at", required=False)

        if status == "paid":
            if paid_at is None:
                raise DataError(f"{where}: у платежа {payment_id} статус paid, но не заполнено paid_at")
            if refunded_at is not None:
                raise DataError(f"{where}: у платежа {payment_id} статус paid, но заполнено refunded_at")
        elif status == "queued":
            if paid_at is not None:
                raise DataError(f"{where}: платёж {payment_id} в очереди (queued), но заполнено paid_at")
            if refunded_at is not None:
                raise DataError(f"{where}: платёж {payment_id} в очереди (queued), но заполнено refunded_at")
        else:  # refunded
            if paid_at is None:
                raise DataError(f"{where}: у возвращённого платежа {payment_id} не заполнено paid_at")
            if refunded_at is None:
                raise DataError(f"{where}: у возвращённого платежа {payment_id} не заполнено refunded_at")
            if refunded_at < paid_at:
                raise DataError(
                    f"{where}: платёж {payment_id} возвращён раньше, чем оплачен "
                    f"({refunded_at.isoformat()} < {paid_at.isoformat()})"
                )

        payments.append(
            Payment(
                payment_id=payment_id,
                contract_id=contract_id,
                work_id=work_id,
                purpose=row["purpose"],
                amount=parse_amount(row["amount"], f"{where}, колонка amount"),
                status=status,
                created_at=created_at,
                paid_at=paid_at,
                refunded_at=refunded_at,
            )
        )

    return payments


def _cross_checks(dataset: Dataset) -> None:
    """Проверки, которые не мешают расчёту, но важны бухгалтеру."""
    for contract in dataset.contracts.values():
        works = dataset.works_of(contract.contract_id)
        if not works:
            dataset.warnings.append(
                f"{contract.contract_id}: у договора нет ни одной работы — "
                "платежи по нему пойдут в «не распределено»"
            )
            continue

        works_total = sum((work.price for work in works), ZERO)
        if works_total != contract.amount:
            dataset.warnings.append(
                f"{contract.contract_id}: сумма договора {fmt_ru(contract.amount)} не равна "
                f"сумме цен работ {fmt_ru(works_total)} (разница "
                f"{fmt_ru(contract.amount - works_total)}). Расчёт ведётся по ценам работ, "
                "сумма договора показывается отдельно"
            )

        for payment in dataset.payments_of(contract.contract_id):
            if payment.paid_at is not None and payment.paid_at < contract.signed_at:
                dataset.warnings.append(
                    f"{payment.payment_id}: дата оплаты {payment.paid_at.strftime('%d.%m.%Y')} "
                    f"раньше даты подписания договора {contract.contract_id} "
                    f"({contract.signed_at.strftime('%d.%m.%Y')})"
                )


def load_dataset(data_dir: Path | str) -> Dataset:
    """Прочитать и проверить всю выгрузку из каталога ``data_dir``."""
    data_dir = Path(data_dir)
    contracts = _parse_contracts(_read_table(data_dir / "contracts.csv", CONTRACT_COLUMNS))
    works = _parse_works(_read_table(data_dir / "works.csv", WORK_COLUMNS), contracts)
    payments = _parse_payments(_read_table(data_dir / "payments.csv", PAYMENT_COLUMNS), contracts, works)

    dataset = Dataset(data_dir=data_dir, contracts=contracts, works=works, payments=payments)
    _cross_checks(dataset)
    return dataset
