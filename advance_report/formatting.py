"""Разбор и печать денег и дат.

Деньги считаем только в ``decimal.Decimal``: двоичный float не хранит 0.1
точно, и на длинных суммах копейки «разъезжаются». Все суммы квантуются до
0.01 с округлением ``ROUND_HALF_UP`` — так округляют в бухгалтерии
(половина копейки идёт вверх).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .errors import DataError

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# Допустимая запись суммы: цифры, необязательный минус и не более двух знаков
# после точки. Запятая как десятичный разделитель и знак валюты намеренно не
# принимаются: в выгрузке их нет, а угадывать смысл чужой записи — способ
# тихо испортить отчёт. Пробелы-разделители тысяч (в том числе неразрывный)
# вычищаются: они не меняют значение числа.
_AMOUNT_RE = re.compile(r"^-?\d+(?:\.\d{1,2})?$")


def q2(value: Decimal) -> Decimal:
    """Привести сумму к копейкам (два знака, ROUND_HALF_UP)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(raw: str | None, where: str) -> Decimal:
    """Разобрать сумму из CSV.

    :param raw: значение ячейки как есть.
    :param where: описание места (файл, строка, колонка) для сообщения об ошибке.
    """
    text = (raw or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise DataError(f"{where}: пустая сумма")

    if not _AMOUNT_RE.match(text):
        raise DataError(
            f"{where}: сумма {raw!r} не распознана. Ожидается число рублей "
            "с точкой и не более чем двумя знаками после неё, например 150000.00"
        )

    value = Decimal(text)
    if value < 0:
        raise DataError(
            f"{where}: отрицательная сумма {raw!r}. Формат выгрузки не "
            "предусматривает отрицательных сумм: возврат оформляется статусом "
            "refunded, а не минусом."
        )
    return q2(value)


def fmt_plain(value: Decimal) -> str:
    """``315000.00`` -> ``'315000.00'`` (машинная запись, точка)."""
    return f"{q2(value):.2f}"


def fmt_csv(value: Decimal) -> str:
    """``315000.00`` -> ``'315000,00'`` (русская запись для Excel)."""
    return fmt_plain(value).replace(".", ",")


def fmt_ru(value: Decimal) -> str:
    """``315000.00`` -> ``'315 000,00'`` — как в отчёте для бухгалтера."""
    text = f"{q2(value):,.2f}"
    return text.replace(",", " ").replace(".", ",")


def fmt_date(value: date | None) -> str:
    """Дата в русском формате; пустая дата печатается прочерком."""
    return "—" if value is None else value.strftime("%d.%m.%Y")


def fmt_date_iso(value: date | None) -> str:
    """Дата в формате ISO (для CSV и JSON); пустая дата — пустая строка."""
    return "" if value is None else value.isoformat()


def parse_date(raw: str | None, where: str, column: str, *, required: bool) -> date | None:
    """Разобрать дату ``ГГГГ-ММ-ДД`` из CSV."""
    text = (raw or "").strip()
    if not text:
        if required:
            raise DataError(f"{where}: не заполнено обязательное поле {column}")
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DataError(
            f"{where}: поле {column} = {text!r} не является датой в формате ГГГГ-ММ-ДД"
        ) from exc
