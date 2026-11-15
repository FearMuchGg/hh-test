"""Вспомогательные функции тестов: пути, конструкторы объектов, запись CSV."""

from __future__ import annotations

import shutil
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from advance_report.loading import Contract, Dataset, Payment, Work

SOLUTION_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SOLUTION_DIR.parent
DATA_DIR = REPO_ROOT / "data"

#: Каталог временных файлов тестов — внутри решения, а не в системном %TEMP%:
#: системный временный каталог может быть недоступен на запись (песочница,
#: корпоративные политики), и тогда тесты падают не по делу.
TMP_ROOT = SOLUTION_DIR / ".tmp-tests"


def make_temp_dir(prefix: str) -> Path:
    """Создать временный каталог под тесты.

    Используется обычный ``mkdir`` с уникальным именем, а не
    ``tempfile.mkdtemp``: на Windows mkdtemp ставит каталогу права 0700, и
    процесс под ограниченным токеном (песочница) теряет доступ на запись.
    """
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TMP_ROOT / f"{prefix}{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


def remove_temp_dir(path: Path) -> None:
    """Удалить временный каталог, не роняя тест из-за прав доступа."""
    shutil.rmtree(path, ignore_errors=True)

CONTRACTS_HEADER = "contract_id,contractor,amount,signed_at"
WORKS_HEADER = "work_id,contract_id,title,price,status,completed_at,cancelled_at"
PAYMENTS_HEADER = "payment_id,contract_id,work_id,purpose,amount,status,created_at,paid_at,refunded_at"


def money(value: str) -> Decimal:
    """Короткая запись Decimal в тестах."""
    return Decimal(value)


def contract(
    contract_id: str = "D-1",
    amount: str = "1000.00",
    signed_at: date = date(2025, 1, 1),
    contractor: str = "ООО «Тест»",
) -> Contract:
    return Contract(contract_id, contractor, money(amount), signed_at)


def work(
    work_id: str = "W-1",
    contract_id: str = "D-1",
    price: str = "1000.00",
    status: str = "planned",
    completed_at: date | None = None,
    cancelled_at: date | None = None,
    title: str | None = None,
) -> Work:
    return Work(
        work_id=work_id,
        contract_id=contract_id,
        title=title or f"Работа {work_id}",
        price=money(price),
        status=status,
        completed_at=completed_at,
        cancelled_at=cancelled_at,
    )


def payment(
    payment_id: str = "P-1",
    contract_id: str = "D-1",
    amount: str = "1000.00",
    status: str = "paid",
    work_id: str | None = None,
    created_at: date = date(2025, 1, 2),
    paid_at: date | None = None,
    refunded_at: date | None = None,
    purpose: str = "тестовый платёж",
) -> Payment:
    if status in ("paid", "refunded") and paid_at is None:
        paid_at = created_at
    return Payment(
        payment_id=payment_id,
        contract_id=contract_id,
        work_id=work_id,
        purpose=purpose,
        amount=money(amount),
        status=status,
        created_at=created_at,
        paid_at=paid_at,
        refunded_at=refunded_at,
    )


def dataset(
    contracts: list[Contract],
    works: list[Work],
    payments: list[Payment],
) -> Dataset:
    """Собрать датасет так же, как его собирает загрузчик, но без CSV."""
    return Dataset(
        data_dir=DATA_DIR,
        contracts={item.contract_id: item for item in contracts},
        works={item.work_id: item for item in works},
        payments=list(payments),
    )


def write_dataset(
    root: Path,
    *,
    contracts: list[str],
    works: list[str],
    payments: list[str],
    encoding: str = "utf-8",
    delimiter: str = ",",
) -> Path:
    """Записать три CSV во временный каталог.

    Строки передаются в обычной записи через запятую; для проверки
    переносимости разделитель колонок заменяется на нужный.
    """
    root.mkdir(parents=True, exist_ok=True)
    for name, header, rows in (
        ("contracts.csv", CONTRACTS_HEADER, contracts),
        ("works.csv", WORKS_HEADER, works),
        ("payments.csv", PAYMENTS_HEADER, payments),
    ):
        header_line = delimiter.join(header.split(","))
        body = "\n".join(delimiter.join(row.split(",")) for row in rows)
        (root / name).write_text(header_line + "\n" + body + "\n", encoding=encoding)
    return root
