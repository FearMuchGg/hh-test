"""Расчёт отчёта: сколько выплачено по каждой работе и что осталось оплатить.

Главная содержательная проблема задания: платежи по договору не привязаны к
работам, а бухгалтеру нужно понимать, «за что мы уже рассчитались». Поэтому
деньги, пришедшие по договору без привязки, раскладываются по работам в
порядке приёмки (ФИФО): сначала закрываются более ранние принятые работы,
остаток — аванс на работы, которые ещё не приняты. Платёж с явной привязкой
к работе (``work_id``) идёт прямо в эту работу и в общий котёл не попадает.

Ещё две вещи, которые задание проверяет отдельно:

* **Состояние на дату, а не «как в выгрузке».** В файлах статусы и суммы даны
  на момент выгрузки, а отчёт нужен на 10.11.2026. Работа, принятая 11.11,
  на 10.11 ещё не принята; платёж от 12.11 на 10.11 ещё не уплачен. Поэтому
  статусы восстанавливаются по датам событий, а не берутся из колонки
  ``status`` наугад.
* **Возвраты.** Платёж со статусом ``refunded`` — это деньги, которые ушли и
  вернулись. На отчётную дату после возврата он не увеличивает выплаченное,
  даже если в колонке ``status`` написано ``refunded``, а в сумме договора
  он фигурирует.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .errors import InternalError
from .formatting import ZERO, fmt_date, fmt_ru
from .loading import Contract, Dataset, Payment, Work

# --- Эффективные состояния работы на отчётную дату -------------------------

DONE = "done"          # выполнена и принята не позже отчётной даты
PLANNED = "planned"    # ещё не принята: запланирована или в работе
CANCELLED = "cancelled"  # отменена не позже отчётной даты
PENDING = "pending"    # принята позже отчётной даты (на дату — ещё нет)

WORK_STATUS_LABELS = {
    DONE: "выполнена",
    PLANNED: "запланирована",
    CANCELLED: "отменена",
    PENDING: "не принята на дату",
}

# --- Эффективные состояния платежа на отчётную дату -------------------------

PAID = "paid"          # деньги у подрядчика на отчётную дату
REFUNDED = "refunded"  # деньги ушли и вернулись до отчётной даты
QUEUED = "queued"      # в очереди, деньги не ушли
AFTER = "after"        # оплачен позже отчётной даты

PAYMENT_STATE_LABELS = {
    PAID: "учтён как выплата",
    REFUNDED: "не учитывается: возвращён",
    QUEUED: "не учитывается: в очереди",
    AFTER: "не учитывается: позже отчётной даты",
}

#: Русские подписи статусов платежа — для отчёта бухгалтеру.
PAYMENT_STATUS_LABELS = {
    "paid": "оплачен",
    "queued": "в очереди",
    "refunded": "возвращён",
}


def effective_work_status(work: Work, as_of: date) -> str:
    """Восстановить состояние работы на отчётную дату.

    Колонка ``status`` описывает момент выгрузки, который может быть позже
    отчётной даты. Порядок проверок: отмена раньше даты отчёта важнее
    приёмки; приёмка позже даты отчёта — это ещё не принятая работа.
    """
    if work.cancelled_at is not None and work.cancelled_at <= as_of:
        return CANCELLED
    if work.completed_at is not None and work.completed_at <= as_of:
        return DONE
    if work.completed_at is not None:  # принята позже отчётной даты
        return PENDING
    return PLANNED


def payment_state(payment: Payment, as_of: date) -> tuple[str, str]:
    """Восстановить состояние платежа на отчётную дату.

    Возвращает пару (состояние, объяснение для бухгалтера).
    """
    if payment.status == "refunded":
        if payment.refunded_at is not None and payment.refunded_at <= as_of:
            return REFUNDED, (
                f"оплачен {fmt_date(payment.paid_at)}, возвращён {fmt_date(payment.refunded_at)} — "
                "на отчётную дату денег у подрядчика нет"
            )
        # Возврат случился позже отчётной даты: на дату деньги ещё у подрядчика.
        return PAID, (
            f"оплачен {fmt_date(payment.paid_at)}, возврат ожидается "
            f"{fmt_date(payment.refunded_at)} — позже отчётной даты"
        )

    if payment.status == "queued":
        return QUEUED, "в очереди на оплату, деньги ещё не ушли"

    if payment.paid_at is None:
        # Загрузчик такие строки не пропускает; защита от будущих правок.
        raise InternalError(
            f"{payment.payment_id}: статус {payment.status}, но нет даты оплаты — "
            "загрузчик должен был остановиться раньше"
        )

    if payment.paid_at > as_of:
        return AFTER, f"оплачен {fmt_date(payment.paid_at)} — позже отчётной даты"

    return PAID, f"оплачен {fmt_date(payment.paid_at)}"


def _allocate(
    works: list[Work],
    statuses: dict[str, str],
    included: list[Payment],
    warnings: list[str],
) -> tuple[dict[str, Decimal], dict[str, list[tuple[str, Decimal, str]]], Decimal]:
    """Разложить выплаты договора по работам.

    :returns: (выплачено по работам, источники выплат, нераспределённый остаток)

    Алгоритм:

    1. Прямые платежи (``work_id`` заполнен) относятся на свою работу.
    2. Остальные платежи складываются в общий котёл договора и закрывают
       работы по очереди: сначала принятые в порядке даты приёмки, затем
       ожидающие приёмки, затем запланированные; внутри группы — по номеру
       работы (другого порядка в данных нет).
    3. Если котёл больше, чем сумма работ, остаток — «не распределено»:
       аванс, за который пока нечего закрывать.
    """
    paid: dict[str, Decimal] = {work.work_id: ZERO for work in works}
    sources: dict[str, list[tuple[str, Decimal, str]]] = {work.work_id: [] for work in works}

    # Ёмкость работы = её цена; у отменённой работы оплачивать нечего.
    capacity: dict[str, Decimal] = {
        work.work_id: (work.price if statuses[work.work_id] != CANCELLED else ZERO) for work in works
    }

    pool: list[Payment] = []
    for payment in included:
        if payment.work_id is None:
            pool.append(payment)
            continue

        status = statuses[payment.work_id]
        if status == CANCELLED:
            # Деньги, привязанные к отменённой работе, — фактическая выплата по
            # договору, но закрывать ею нечего: отправляем в общий котёл и
            # говорим об этом бухгалтеру.
            warnings.append(
                f"{payment.payment_id}: платёж {fmt_ru(payment.amount)} привязан к отменённой "
                f"работе {payment.work_id}; учтён как платёж по договору без привязки"
            )
            pool.append(payment)
            continue

        paid[payment.work_id] += payment.amount
        sources[payment.work_id].append((payment.payment_id, payment.amount, "прямой"))
        capacity[payment.work_id] = max(ZERO, capacity[payment.work_id] - payment.amount)

    # Котёл распределяем в хронологическом порядке платежей: так в отчёте
    # видно, какой именно платёж оплатил какую работу.
    queue: deque[tuple[str, Decimal]] = deque(
        (p.payment_id, p.amount)
        for p in sorted(pool, key=lambda item: (item.paid_at, item.payment_id))
    )

    order = sorted(
        (work for work in works if statuses[work.work_id] != CANCELLED),
        key=lambda work: (
            {DONE: 0, PENDING: 1, PLANNED: 2}[statuses[work.work_id]],
            work.completed_at or date.max,
            work.work_id,
        ),
    )

    for work in order:
        while capacity[work.work_id] > ZERO and queue:
            payment_id, rest = queue[0]
            take = min(rest, capacity[work.work_id])
            paid[work.work_id] += take
            capacity[work.work_id] -= take
            sources[work.work_id].append((payment_id, take, "из платежа по договору"))
            rest -= take
            if rest > ZERO:
                queue[0] = (payment_id, rest)
            else:
                queue.popleft()

    unallocated = sum((rest for _, rest in queue), ZERO)
    return paid, sources, unallocated


@dataclass(frozen=True)
class WorkRow:
    """Строка отчёта по работе."""

    contract_id: str
    work_id: str
    title: str
    status: str
    source_status: str
    status_label: str
    price: Decimal
    payable: Decimal
    paid: Decimal
    paid_direct: Decimal
    paid_from_contract: Decimal
    remaining: Decimal
    overpay: Decimal
    sources: tuple[tuple[str, Decimal, str], ...]
    note: str


@dataclass(frozen=True)
class ContractRow:
    """Строка отчёта по договору."""

    contract_id: str
    contractor: str
    signed_at: date
    amount: Decimal
    works_total: Decimal
    cancelled_total: Decimal
    payable: Decimal
    accepted_total: Decimal
    accepted_paid: Decimal
    accepted_remaining: Decimal
    advance_ahead: Decimal
    paid_total: Decimal
    remaining: Decimal
    overpaid: Decimal
    queued_total: Decimal
    refunded_total: Decimal
    unallocated: Decimal


@dataclass(frozen=True)
class PaymentRow:
    """Строка отчёта по платежу (для проверки бухгалтером)."""

    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: Decimal
    status: str
    state: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None
    included: bool
    reason: str


@dataclass(frozen=True)
class Totals:
    """Итоги по всей выгрузке."""

    contracts: int
    works: int
    amount: Decimal
    works_total: Decimal
    cancelled_total: Decimal
    payable: Decimal
    accepted_total: Decimal
    accepted_paid: Decimal
    accepted_remaining: Decimal
    advance_ahead: Decimal
    paid_total: Decimal
    remaining: Decimal
    overpaid: Decimal
    queued_total: Decimal
    refunded_total: Decimal
    unallocated: Decimal


@dataclass(frozen=True)
class Report:
    """Готовый отчёт: данные + сверки + предупреждения."""

    as_of: date
    data_dir: str
    contracts: tuple[ContractRow, ...]
    works: tuple[WorkRow, ...]
    payments: tuple[PaymentRow, ...]
    totals: Totals
    checks: tuple[str, ...]
    warnings: tuple[str, ...]
    after_date: tuple[str, ...]


def _work_note(status: str, work: Work, overpay: Decimal, paid: Decimal) -> str:
    """Пояснение к строке работы: почему сумма такая, а не иная."""
    parts: list[str] = []
    if status == CANCELLED:
        parts.append(f"отменена {fmt_date(work.cancelled_at)} — оплате не подлежит")
    elif status == PENDING:
        parts.append(f"принята {fmt_date(work.completed_at)} — позже отчётной даты")
    if status in (PLANNED, PENDING) and paid > ZERO:
        # Деньги ушли вперёд приёмки: без пояснения строка читается как
        # «заплатили за невыполненную работу».
        parts.append(f"аванс вперёд приёмки {fmt_ru(paid)}")
    if overpay > ZERO:
        parts.append(f"переплата {fmt_ru(overpay)}")
    return "; ".join(parts)


def build_report(dataset: Dataset, as_of: date) -> Report:
    """Построить отчёт на дату ``as_of``."""
    warnings: list[str] = list(dataset.warnings)
    after_date: list[str] = []

    contract_rows: list[ContractRow] = []
    work_rows: list[WorkRow] = []
    payment_rows: list[PaymentRow] = []

    for contract in dataset.contracts.values():
        cid = contract.contract_id
        cworks = dataset.works_of(cid)
        cpayments = dataset.payments_of(cid)

        statuses = {work.work_id: effective_work_status(work, as_of) for work in cworks}

        included: list[Payment] = []
        payments_after_date: list[Payment] = []
        for payment in cpayments:
            state, reason = payment_state(payment, as_of)
            included_flag = state == PAID
            if included_flag:
                included.append(payment)
            elif state == AFTER:
                payments_after_date.append(payment)

            payment_rows.append(
                PaymentRow(
                    payment_id=payment.payment_id,
                    contract_id=cid,
                    work_id=payment.work_id,
                    purpose=payment.purpose,
                    amount=payment.amount,
                    status=payment.status,
                    state=state,
                    created_at=payment.created_at,
                    paid_at=payment.paid_at,
                    refunded_at=payment.refunded_at,
                    included=included_flag,
                    reason=reason,
                )
            )

            if state == AFTER:
                after_date.append(
                    f"{payment.payment_id} ({cid}): {fmt_ru(payment.amount)} — {reason}. "
                    f"В расчёт на {fmt_date(as_of)} не вошёл."
                )

        # Работы, принятые или отменённые позже отчётной даты, — тоже события
        # «после даты»: бухгалтеру важно видеть, что выгрузка их уже содержит.
        for work in cworks:
            if work.completed_at is not None and work.completed_at > as_of:
                after_date.append(
                    f"{work.work_id} ({cid}): принята {fmt_date(work.completed_at)} — позже отчётной даты, "
                    "в отчёте считается не принятой"
                )
            if work.cancelled_at is not None and work.cancelled_at > as_of:
                after_date.append(
                    f"{work.work_id} ({cid}): отменена {fmt_date(work.cancelled_at)} — позже отчётной даты, "
                    "в отчёте считается действующей"
                )

        paid, source_map, unallocated = _allocate(cworks, statuses, included, warnings)

        for work in cworks:
            status = statuses[work.work_id]
            paid_amount = paid[work.work_id]
            payable = ZERO if status == CANCELLED else work.price
            remaining = max(ZERO, payable - paid_amount)
            overpay = ZERO if status == CANCELLED else max(ZERO, paid_amount - work.price)
            paid_direct = sum((amount for _, amount, kind in source_map[work.work_id] if kind == "прямой"), ZERO)

            work_rows.append(
                WorkRow(
                    contract_id=cid,
                    work_id=work.work_id,
                    title=work.title,
                    status=status,
                    source_status=work.status,
                    status_label=WORK_STATUS_LABELS[status],
                    price=work.price,
                    payable=payable,
                    paid=paid_amount,
                    paid_direct=paid_direct,
                    paid_from_contract=paid_amount - paid_direct,
                    remaining=remaining,
                    overpay=overpay,
                    sources=tuple(source_map[work.work_id]),
                    note=_work_note(status, work, overpay, paid_amount),
                )
            )

            if overpay > ZERO:
                warnings.append(
                    f"{work.work_id}: выплачено {fmt_ru(paid_amount)} при цене {fmt_ru(work.price)} — "
                    f"переплата по работе {fmt_ru(overpay)}"
                )

        works_total = sum((work.price for work in cworks), ZERO)
        cancelled_total = sum((work.price for work in cworks if statuses[work.work_id] == CANCELLED), ZERO)
        payable = works_total - cancelled_total
        paid_total = sum((payment.amount for payment in included), ZERO)
        remaining = max(ZERO, payable - paid_total)
        overpaid = max(ZERO, paid_total - payable)
        allocated_total = sum(paid.values(), ZERO)

        accepted = [work for work in cworks if statuses[work.work_id] == DONE]
        accepted_total = sum((work.price for work in accepted), ZERO)
        accepted_paid = sum((min(paid[work.work_id], work.price) for work in accepted), ZERO)
        accepted_remaining = sum((max(ZERO, work.price - paid[work.work_id]) for work in accepted), ZERO)
        advance_ahead = max(ZERO, paid_total - accepted_paid)

        queued_total = sum(
            (p.amount for p in cpayments if p.status == "queued" and p.created_at <= as_of), ZERO
        )
        refunded_total = sum(
            (
                p.amount
                for p in cpayments
                if p.status == "refunded" and p.refunded_at is not None and p.refunded_at <= as_of
            ),
            ZERO,
        )

        # Инварианты расчёта: если они не выполняются, цифры в отчёте
        # бессмысленны, поэтому падаем громко.
        if paid_total != allocated_total + unallocated:
            raise InternalError(
                f"{cid}: выплачено {fmt_ru(paid_total)}, а по работам разложено "
                f"{fmt_ru(allocated_total)} и не распределено {fmt_ru(unallocated)}"
            )
        if payable != works_total - cancelled_total:
            raise InternalError(f"{cid}: к оплате {fmt_ru(payable)} не равно сумме работ минус отменённые")
        if remaining != max(ZERO, payable - paid_total):
            raise InternalError(f"{cid}: остаток {fmt_ru(remaining)} не сходится с к оплате минус выплачено")

        contract_rows.append(
            ContractRow(
                contract_id=cid,
                contractor=contract.contractor,
                signed_at=contract.signed_at,
                amount=contract.amount,
                works_total=works_total,
                cancelled_total=cancelled_total,
                payable=payable,
                accepted_total=accepted_total,
                accepted_paid=accepted_paid,
                accepted_remaining=accepted_remaining,
                advance_ahead=advance_ahead,
                paid_total=paid_total,
                remaining=remaining,
                overpaid=overpaid,
                queued_total=queued_total,
                refunded_total=refunded_total,
                unallocated=unallocated,
            )
        )

        if payments_after_date:
            extra = sum((p.amount for p in payments_after_date), ZERO)
            remaining_after = max(ZERO, payable - (paid_total + extra))
            overpaid_after = max(ZERO, (paid_total + extra) - payable)
            after_date.append(
                f"{cid}: с учётом платежей после отчётной даты ({fmt_ru(extra)}) выплачено стало бы "
                f"{fmt_ru(paid_total + extra)}, остаток {fmt_ru(remaining_after)}"
                + (f", переплата {fmt_ru(overpaid_after)}" if overpaid_after > ZERO else "")
            )

        if overpaid > ZERO:
            warnings.append(
                f"{cid}: выплачено {fmt_ru(paid_total)} при сумме к оплате {fmt_ru(payable)} — "
                f"переплата по договору {fmt_ru(overpaid)}"
            )

        # Сверка «остаток по работам» и «остаток по договору»: они равны,
        # пока нет переплат по отдельным работам.
        works_remaining_sum = sum((row.remaining for row in work_rows if row.contract_id == cid), ZERO)
        if works_remaining_sum != remaining:
            warnings.append(
                f"{cid}: сумма остатков по работам ({fmt_ru(works_remaining_sum)}) не равна "
                f"остатку по договору ({fmt_ru(remaining)}) — закрывайте переплаты по работам вручную"
            )

    totals = _totals(contract_rows, work_rows)
    checks = _checks(contract_rows, totals)

    return Report(
        as_of=as_of,
        data_dir=str(dataset.data_dir),
        contracts=tuple(contract_rows),
        works=tuple(work_rows),
        payments=tuple(payment_rows),
        totals=totals,
        checks=tuple(checks),
        warnings=tuple(warnings),
        after_date=tuple(after_date),
    )


def _totals(contracts: list[ContractRow], works: list[WorkRow]) -> Totals:
    def total(attr: str) -> Decimal:
        return sum((getattr(row, attr) for row in contracts), ZERO)

    totals = Totals(
        contracts=len(contracts),
        works=len(works),
        amount=total("amount"),
        works_total=total("works_total"),
        cancelled_total=total("cancelled_total"),
        payable=total("payable"),
        accepted_total=total("accepted_total"),
        accepted_paid=total("accepted_paid"),
        accepted_remaining=total("accepted_remaining"),
        advance_ahead=total("advance_ahead"),
        paid_total=total("paid_total"),
        remaining=total("remaining"),
        overpaid=total("overpaid"),
        queued_total=total("queued_total"),
        refunded_total=total("refunded_total"),
        unallocated=total("unallocated"),
    )

    # Итоги обязаны быть равны сумме строк: это защита от потери договора.
    for attr in (
        "amount",
        "works_total",
        "cancelled_total",
        "payable",
        "accepted_total",
        "paid_total",
        "remaining",
        "overpaid",
        "queued_total",
    ):
        expected = sum((getattr(row, attr) for row in contracts), ZERO)
        if getattr(totals, attr) != expected:
            raise InternalError(f"итог {attr} не равен сумме по договорам")

    works_paid = sum((row.paid for row in works), ZERO)
    if works_paid != totals.paid_total - totals.unallocated:
        raise InternalError(
            f"выплачено по работам {fmt_ru(works_paid)} не сходится с выплачено по договорам "
            f"{fmt_ru(totals.paid_total)} минус нераспределённое {fmt_ru(totals.unallocated)}"
        )
    return totals


def _checks(contracts: list[ContractRow], totals: Totals) -> list[str]:
    """Строки сверки: бухгалтер может проверить их руками."""
    checks: list[str] = []

    for row in contracts:
        allocated = row.paid_total - row.unallocated
        checks.append(
            f"{row.contract_id}: выплачено {fmt_ru(row.paid_total)} = "
            f"по работам {fmt_ru(allocated)} + не распределено {fmt_ru(row.unallocated)}"
        )
        checks.append(
            f"{row.contract_id}: к оплате {fmt_ru(row.payable)} = "
            f"принято {fmt_ru(row.accepted_total)} + не принято "
            f"(без отменённых) {fmt_ru(row.payable - row.accepted_total)}"
        )
        checks.append(
            f"{row.contract_id}: долг по принятым работам {fmt_ru(row.accepted_remaining)}, "
            f"аванс сверх принятого {fmt_ru(row.advance_ahead)}"
        )
        if row.queued_total > ZERO:
            # Платёж в очереди ещё не ушёл, но деньги на него уже зарезервированы:
            # бухгалтеру важнее «сколько останется после очереди», чем валовой остаток.
            after_queue = max(ZERO, row.remaining - row.queued_total)
            checks.append(
                f"{row.contract_id}: в очереди {fmt_ru(row.queued_total)}, "
                f"после её оплаты останется {fmt_ru(after_queue)}"
            )

    checks.append(
        f"Итого: договоров {totals.contracts}, работ {totals.works}; "
        f"сумма договоров {fmt_ru(totals.amount)}; к оплате {fmt_ru(totals.payable)}; "
        f"выплачено {fmt_ru(totals.paid_total)}; осталось оплатить {fmt_ru(totals.remaining)}"
    )
    if totals.overpaid > ZERO:
        checks.append(f"Итого переплата по договорам: {fmt_ru(totals.overpaid)}")
    if totals.queued_total > ZERO:
        checks.append(f"Итого в очереди на оплату (в выплаты не входит): {fmt_ru(totals.queued_total)}")
    if totals.unallocated > ZERO:
        checks.append(f"Итого не распределено по работам: {fmt_ru(totals.unallocated)}")

    # Перекрёстная сверка: сумма выплат по работам = сумма учтённых платежей
    # минус нераспределённый остаток.
    checks.append(
        "Сверка: выплачено по работам "
        f"{fmt_ru(totals.paid_total - totals.unallocated)} + не распределено "
        f"{fmt_ru(totals.unallocated)} = выплачено по договорам {fmt_ru(totals.paid_total)}"
    )
    return checks
