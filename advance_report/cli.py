"""Командный интерфейс: построить отчёт и записать его в нужном формате.

Коды возврата:

* ``0`` — отчёт построен (предупреждения не мешают);
* ``2`` — данные противоречат формату, отчёт не строился;
* ``3`` — нарушен внутренний инвариант расчёта (дефект программы);
* ``4`` — не удалось записать файлы.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .engine import build_report
from .errors import DataError, InternalError
from .loading import load_dataset
from .render import render_console, render_json, render_markdown, write_csv_files

#: По умолчанию берём ``data/`` рядом с решением (если решение лежит внутри
#: проекта). Путь можно задать явно через ``--data``.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"дата {value!r} не в формате ГГГГ-ММ-ДД (например, 2026-11-10)"
        ) from exc


def _configure_stdout() -> None:
    """Настроить вывод так, чтобы отчёт читался и в файле, и в консоли.

    В интерактивной консоли Windows Python пишет текст через консольный
    Unicode-API, менять кодировку не нужно. При перенаправлении в файл или
    другой процесс Python берёт кодировку локали (на русской Windows это
    cp1251), и сохранённый отчёт ломается при чтении как UTF-8. Поэтому для
    перенаправленного вывода принудительно включаем UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report.py",
        description=(
            "Отчёт по выгрузке из учёта: по каждой работе — цена, выплачено, "
            "осталось оплатить; по каждому договору — итоги."
        ),
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"каталог с contracts.csv, works.csv, payments.csv (по умолчанию {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--date",
        "--as-of",
        dest="as_of",
        type=_parse_date_arg,
        default=date.today(),
        metavar="ГГГГ-ММ-ДД",
        help="отчётная дата; день учитывается включительно (по умолчанию сегодня)",
    )
    parser.add_argument(
        "--format",
        choices=("console", "md", "csv", "json", "all"),
        default="console",
        help="куда выводить: console — на экран, остальное — файлы в --out (all — все файлы)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="каталог для файлов отчёта (по умолчанию ./out)",
    )
    parser.add_argument(
        "--csv-delimiter",
        default=";",
        help="разделитель колонок в CSV (по умолчанию ';' — так открывает Excel)",
    )
    parser.add_argument(
        "--csv-dot",
        action="store_true",
        help="в CSV писать точку как десятичный разделитель вместо запятой",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = _build_parser().parse_args(argv)

    try:
        dataset = load_dataset(args.data)
        report = build_report(dataset, args.as_of)
    except DataError as exc:
        print(f"Ошибка в данных: {exc}", file=sys.stderr)
        return 2
    except InternalError as exc:
        print(f"Внутренняя ошибка расчёта: {exc}", file=sys.stderr)
        return 3

    suffix = report.as_of.isoformat()

    try:
        if args.format == "console":
            sys.stdout.write(render_console(report))
            written: list[Path] = []
        else:
            written = []
            args.out.mkdir(parents=True, exist_ok=True)
            if args.format in ("md", "all"):
                path = args.out / f"report_{suffix}.md"
                path.write_text(render_markdown(report), encoding="utf-8")
                written.append(path)
            if args.format in ("csv", "all"):
                written.extend(
                    write_csv_files(
                        report,
                        args.out,
                        delimiter=args.csv_delimiter,
                        decimal_comma=not args.csv_dot,
                    )
                )
            if args.format in ("json", "all"):
                path = args.out / f"report_{suffix}.json"
                path.write_text(render_json(report), encoding="utf-8")
                written.append(path)
    except OSError as exc:
        print(f"Не удалось записать отчёт: {exc}", file=sys.stderr)
        return 4

    for path in written:
        print(f"Записан {path}")

    if report.warnings:
        print(f"Предупреждений: {len(report.warnings)} — подробности в отчёте.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover — удобно запускать файл напрямую
    raise SystemExit(main())
