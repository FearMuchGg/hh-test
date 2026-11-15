"""Экспорт журнала сессии в Markdown-транскрипт работы.

Задание просит приложить полный транскрипт работы (в Claude Code — экспорт
командой ``/export``). Работа выполнялась в DeepSeek Harness, у которого есть
свой журнал сессии; этот скрипт механически конвертирует журнал в читаемый
Markdown, не меняя содержание переписки.

Что намеренно не выгружается:

* служебные сообщения платформы (внутренние инструкции, заголовки запросов);
* скрытые рассуждения модели — только видимые сообщения и вызовы инструментов;
* секреты, похожие на ключи и пароли, маскируются (это разрешено заданием);
* очень длинные выводы инструментов (свыше 20 000 символов) помечаются и
  сокращаются, чтобы файл оставался открываемым.

Запуск::

    python tools/export_transcript.py --out TRANSCRIPT.md
    python tools/export_transcript.py --session <путь к session.v4.jsonl.zstd> --out TRANSCRIPT.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_TOOL_OUTPUT = 20_000
SKIP_TYPES = {
    "system/message",
    "request/header",
    "request/context",
    "session/title-llm-request",
    "agent/inbox/spliced",
    "turn/start",
    "step/start",
    "step/end",
    "job/registered",
    "job/settled",
    "permission/preset",
    "sandbox/mode",
    "approval/policy",
    "team/mode",
}

# Ключи и пароли: задание разрешает вырезать их из транскрипта.
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(password|passwd|token|secret)\s*[:=]\s*\S+"),
]

# Служебные блоки платформы внутри пользовательского сообщения — не часть
# переписки с человеком, поэтому в транскрипт не попадают.
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def _timestamp(value) -> str:
    """Отформатировать время журнала (мс от эпохи) для шапки."""
    try:
        from datetime import datetime

        return datetime.fromtimestamp(int(value) / 1000).strftime("%d.%m.%Y %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value)


def find_session(workspace: Path) -> Path:
    """Найти журнал текущей сессии для рабочего каталога.

    Каталог сессий платформы кодирует путь проекта в имени: ``--C-Users-...--``.
    Берём самый свежий файл журнала среди подходящих по имени каталога.
    """
    root = Path.home() / ".dsh" / "sessions"
    if not root.is_dir():
        raise SystemExit(f"Не найден каталог журналов сессий: {root}")

    slug = "--" + str(workspace).replace(":", "").replace("\\", "-").replace("/", "-") + "--"
    candidates = [
        path
        for path in root.glob("*/session-*/session.v4.jsonl*")
        if path.parent.parent.name == slug
    ]
    if not candidates:
        candidates = list(root.glob("*/session-*/session.v4.jsonl*"))
    if not candidates:
        raise SystemExit(f"В {root} нет файлов журнала сессий")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_records(path: Path) -> list[dict]:
    """Прочитать журнал: zstd-сжатый JSONL или обычный JSONL."""
    raw = path.read_bytes()
    if path.suffix == ".zstd":
        try:
            import compression.zstd as zstd  # Python 3.14+
        except ImportError as exc:  # pragma: no cover — зависит от версии Python
            raise SystemExit(
                "Для чтения журнала нужен Python 3.14+ (модуль compression.zstd). "
                "Можно указать распакованный .jsonl через --session"
            ) from exc
        raw = zstd.decompress(raw)

    records: list[dict] = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def sanitize(text: str) -> str:
    """Замаскировать то, что похоже на ключи и пароли."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


def _parts_text(content) -> str:
    """Собрать текст из content сообщения (строка или список блоков)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def _fence(text: str, lang: str = "text") -> list[str]:
    """Обернуть текст в блок кода, подобрав длину забора."""
    fence = "```"
    while fence in text:
        fence += "`"
    return [fence + lang, text.rstrip("\n"), fence]


def _pretty_arguments(arguments) -> str:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    return json.dumps(arguments, ensure_ascii=False, indent=2)


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return (
        f"{head}\n\n… [сокращено при экспорте: всего {len(text)} символов, "
        f"показаны начало и конец] …\n\n{tail}"
    )


def render(records: list[dict], session_path: Path) -> str:
    workspace = ""
    started = ""
    turn = None

    limit_text = f"{MAX_TOOL_OUTPUT:,}".replace(",", " ")

    lines: list[str] = [
        "# Транскрипт работы",
        "",
        "Задание просит приложить полный транскрипт работы с Claude Code (экспорт командой `/export`).",
        "Работа выполнялась в DeepSeek Harness, поэтому здесь — эквивалент: механический экспорт",
        "журнала сессии, без правки переписки. Отличия от `/export` Claude Code:",
        "",
        "- служебные сообщения платформы (внутренние инструкции) не выгружаются;",
        "- служебные блоки `<system-reminder>` внутри запросов удалены;",
        "- скрытые рассуждения модели не выгружаются — только видимые сообщения и вызовы инструментов;",
        f"- длинные выводы инструментов (свыше {limit_text} символов) помечаются и сокращаются;",
        "- строки, похожие на ключи и пароли, маскируются.",
        "",
    ]

    # Метаданные сессии: первый служебный record с полем cwd.
    for record in records:
        if record.get("type") == "session":
            workspace = str(record.get("cwd", ""))
            started = str(record.get("createdAt", ""))
            break

    lines.extend(
        [
            f"**Сессия:** `{session_path.name}`",
            f"**Рабочий каталог:** `{workspace}`",
            f"**Начало сессии:** {_timestamp(started)}",
            f"**Записей в журнале:** {len(records)}",
            "",
            "---",
            "",
        ]
    )

    for record in records:
        kind = record.get("type")
        if kind in SKIP_TYPES or kind == "session":
            continue
        data = record.get("data") or {}

        if kind == "user/message":
            text = SYSTEM_REMINDER_RE.sub("", _parts_text(data.get("content"))).strip()
            if not text:
                continue
            lines.extend(["## Пользователь", "", sanitize(text), ""])

        elif kind == "assistant/message":
            message = data.get("message") or {}
            current_turn = data.get("turn")
            if current_turn != turn:
                turn = current_turn
                lines.extend([f"### Тур {turn}" if turn is not None else "### Ассистент", ""])
            text = _parts_text(message.get("content"))
            if text.strip():
                lines.extend(["**Ассистент:**", "", sanitize(text), ""])

        elif kind == "tool/call":
            name = data.get("name", "?")
            args = _pretty_arguments(data.get("arguments"))
            lines.extend([f"**Вызов инструмента `{name}`:**", ""])
            lines.extend(_fence(sanitize(_truncate(args)), "json"))
            lines.append("")

        elif kind == "tool/result":
            message = data.get("message") or {}
            text = _parts_text(message.get("content"))
            is_error = bool(message.get("isError"))
            title = "**Результат инструмента" + (" (ошибка)" if is_error else "") + ":**"
            lines.extend([title, ""])
            lines.extend(_fence(sanitize(_truncate(text)) if text.strip() else "(пусто)"))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    if not sys.stdout.isatty():  # перенаправленный вывод — пишем UTF-8
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description="Экспорт журнала сессии в Markdown-транскрипт.")
    parser.add_argument("--session", type=Path, help="файл journal (session.v4.jsonl.zstd)")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="рабочий каталог проекта")
    parser.add_argument("--out", type=Path, default=Path("TRANSCRIPT.md"), help="куда записать транскрипт")
    args = parser.parse_args(argv)

    session = args.session or find_session(args.workspace.resolve())
    records = load_records(session)
    text = render(records, session)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"Транскрипт: {args.out} ({len(text):,} символов, записей {len(records)})".replace(",", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
