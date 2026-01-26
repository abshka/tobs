import os
import sys

# Настройки
OUTPUT_FILE = "project_dump.md"

# Папки, которые нужно игнорировать (рекурсивно)
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "env",
    ".idea",
    ".vscode",
    "build",
    "dist",
    "egg-info",
    "export",
    "sessions",
    "cache",
    ".monitoring",
}

# Расширения файлов, которые нужно игнорировать
IGNORE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".dylib",
    ".db",
    ".sqlite",
    ".session",
    ".session-journal",  # ВАЖНО: не сливаем сессии!
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".mp4",
    ".mp3",
    ".zip",
    ".tar.gz",
    ".lock",  # uv.lock обычно огромный и бесполезный для анализа логики
    ".log",
}

# Конкретные файлы для игнора
IGNORE_FILES = {
    OUTPUT_FILE,
    ".DS_Store",
    "snapshot.py",  # Не включаем сам скрипт
}


def is_text_file(filepath):
    """Простая проверка, является ли файл текстовым (читаем первые 1024 байта)"""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(1024)
            if not chunk:
                return True  # Пустой файл считаем текстовым
            if b"\0" in chunk:
                return False  # Нулевой байт обычно признак бинарника
            return True
    except Exception:
        return False


def main():
    root_dir = os.getcwd()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as outfile:
        # Сначала запишем структуру проекта (Tree)
        outfile.write("### Project Structure ###\n\n")
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Фильтрация папок in-place
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

            level = dirpath.replace(root_dir, "").count(os.sep)
            indent = " " * 4 * (level)
            outfile.write(f"{indent}{os.path.basename(dirpath)}/\n")
            subindent = " " * 4 * (level + 1)
            for f in filenames:
                if (
                    not any(f.endswith(ext) for ext in IGNORE_EXTENSIONS)
                    and f not in IGNORE_FILES
                ):
                    outfile.write(f"{subindent}{f}\n")

        outfile.write("\n\n### File Contents ###\n\n")

        # Теперь содержимое файлов
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Фильтрация папок
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

            for filename in filenames:
                # Пропускаем игнорируемые файлы
                if filename in IGNORE_FILES:
                    continue
                if any(filename.endswith(ext) for ext in IGNORE_EXTENSIONS):
                    continue

                filepath = os.path.join(dirpath, filename)

                # Получаем относительный путь для заголовка
                rel_path = os.path.relpath(filepath, root_dir)

                if not is_text_file(filepath):
                    print(f"Skipping binary file: {rel_path}")
                    continue

                try:
                    with open(
                        filepath, "r", encoding="utf-8", errors="ignore"
                    ) as infile:
                        content = infile.read()

                        # Формат, который вы просили
                        outfile.write(f"--- {rel_path} ---\n")
                        outfile.write(content)
                        # Добавляем перенос строки, если в конце файла его нет
                        if content and not content.endswith("\n"):
                            outfile.write("\n")
                        outfile.write(f"--- end {rel_path} ---\n\n")

                        print(f"Added: {rel_path}")
                except Exception as e:
                    print(f"Error reading {rel_path}: {e}")

    print(f"\nDone! Snapshot saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
