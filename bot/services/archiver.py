import os
import zipfile
import logging
from pathlib import Path
from typing import List, Tuple

# Используем относительный импорт для config
from ..config import MAX_ARCHIVE_SIZE_BYTES, DEFAULT_ARCHIVE_NAME_PREFIX, ARCHIVES_DIR

logger = logging.getLogger(__name__)

def _get_dir_size_and_files(dir_path: Path) -> Tuple[int, List[Tuple[Path, int]]]:
    """
    Рекурсивно подсчитывает общий размер файлов в директории и собирает
    список всех файлов с их индивидуальными размерами.
    Возвращает (общий размер, список_кортежей[(путь_к_файлу, размер_файла)]).
    """
    total_size = 0
    files_with_sizes: List[Tuple[Path, int]] = []
    for item in dir_path.rglob('*'):
        if item.is_file():
            try:
                size = item.stat().st_size
                total_size += size
                files_with_sizes.append((item, size))
            except FileNotFoundError:
                logger.warning(f"File {item} not found during size calculation, skipping.")
                continue
    return total_size, files_with_sizes

def _create_zip_for_files_batch(
    files_to_archive: List[Tuple[Path, int]],
    base_source_path: Path,
    archive_path: Path,
    compress: bool
) -> bool:
    """
    Создает один zip-файл из предоставленного списка файлов.
    """
    compression_method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, 'w', compression_method, allowZip64=True) as zf:
            for file_path, _ in files_to_archive:
                if file_path.exists() and file_path.is_file():
                    arcname = file_path.relative_to(base_source_path)
                    zf.write(file_path, arcname=arcname)
                else:
                    logger.warning(f"File {file_path} for archive {archive_path} not found or is not a file, skipping.")

        actual_size = archive_path.stat().st_size
        logger.info(f"Created archive: {archive_path} (Compression: {compress}, Actual Size: {actual_size} bytes)")
        return True
    except Exception as e:
        logger.error(f"Failed to create archive {archive_path}: {e}", exc_info=True)
        if archive_path.exists():
            try:
                archive_path.unlink()
            except OSError as ose:
                logger.error(f"Could not remove partially created archive {archive_path}: {ose}")
        return False

def create_archive_from_directory(source_dir_path: str, archive_name_prefix: str = DEFAULT_ARCHIVE_NAME_PREFIX) -> List[str]:
    """
    Создает архивы из source_dir_path согласно указанной логике:
    1. Если общий нежатый размер < MAX_ARCHIVE_SIZE_BYTES: один архив без сжатия.
    2. Иначе: пробует один архив со сжатием.
       a. Если он < MAX_ARCHIVE_SIZE_BYTES: использует его.
       b. Иначе: делит на части (каждая часть сжимается).
    """
    source_path = Path(source_dir_path)
    if not source_path.is_dir():
        logger.error(f"Source path {source_dir_path} is not a directory or does not exist.")
        return []

    output_dir = Path(ARCHIVES_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    total_uncompressed_size, all_files_with_sizes = _get_dir_size_and_files(source_path)

    if not all_files_with_sizes:
        logger.info(f"No files found in {source_dir_path} to archive.")
        return []

    logger.info(f"Total uncompressed size of files in '{source_dir_path}': {total_uncompressed_size} bytes. Number of files: {len(all_files_with_sizes)}.")
    logger.info(f"Max archive size (MAX_ARCHIVE_SIZE_BYTES): {MAX_ARCHIVE_SIZE_BYTES} bytes.")

    created_archives_paths: List[str] = []

    if total_uncompressed_size < MAX_ARCHIVE_SIZE_BYTES:
        archive_path = output_dir / f"{archive_name_prefix}_stored.zip"
        logger.info(f"Total uncompressed size ({total_uncompressed_size} B) < limit. Attempting stored (no compression) archive: {archive_path}")
        if _create_zip_for_files_batch(all_files_with_sizes, source_path, archive_path, compress=False):
            if archive_path.stat().st_size < MAX_ARCHIVE_SIZE_BYTES:
                created_archives_paths.append(str(archive_path))
                logger.info(f"Stored archive '{archive_path}' created successfully and is within size limits.")
                return created_archives_paths
            else:
                logger.warning(f"Stored archive '{archive_path}' ({archive_path.stat().st_size} B) exceeded limit despite uncompressed source being smaller. Deleting and trying compression/splitting.")
                archive_path.unlink(missing_ok=True)
        else:
            logger.warning(f"Failed to create stored archive '{archive_path}'. Will try compression/splitting.")

    if not created_archives_paths:
        logger.info(f"Attempting single compressed archive for all files.")
        compressed_archive_path = output_dir / f"{archive_name_prefix}_compressed_single.zip"
        if _create_zip_for_files_batch(all_files_with_sizes, source_path, compressed_archive_path, compress=True):
            if compressed_archive_path.stat().st_size < MAX_ARCHIVE_SIZE_BYTES:
                created_archives_paths.append(str(compressed_archive_path))
                logger.info(f"Single compressed archive '{compressed_archive_path}' created successfully and is within size limits.")
                return created_archives_paths
            else:
                logger.info(f"Single compressed archive '{compressed_archive_path}' ({compressed_archive_path.stat().st_size} B) is too large. Deleting and proceeding to splitting.")
                compressed_archive_path.unlink(missing_ok=True)
        else:
            logger.warning(f"Failed to create single compressed archive '{compressed_archive_path}'. Will proceed to splitting if possible.")

    if not created_archives_paths:
        logger.info("Splitting content into multiple compressed archive parts.")
        created_archives_paths = []

        current_part_files_batch: List[Tuple[Path, int]] = []
        current_part_uncompressed_accumulated_size: int = 0
        target_uncompressed_size_per_part = int(MAX_ARCHIVE_SIZE_BYTES * 0.9)
        part_number = 1

        all_files_with_sizes.sort(key=lambda x: x[0])

        for file_path, file_uncompressed_size in all_files_with_sizes:
            if current_part_files_batch and \
               (current_part_uncompressed_accumulated_size + file_uncompressed_size > target_uncompressed_size_per_part) and \
               current_part_uncompressed_accumulated_size > 0:

                part_archive_path = output_dir / f"{archive_name_prefix}_part{part_number}.zip"
                logger.info(f"Creating part {part_number} with {len(current_part_files_batch)} files (uncompressed size: {current_part_uncompressed_accumulated_size} B).")
                if _create_zip_for_files_batch(current_part_files_batch, source_path, part_archive_path, compress=True):
                    actual_part_size = part_archive_path.stat().st_size
                    if actual_part_size < MAX_ARCHIVE_SIZE_BYTES:
                        created_archives_paths.append(str(part_archive_path))
                    else:
                        logger.warning(f"Archive part {part_archive_path} (Actual: {actual_part_size} B) "
                                       f"EXCEEDS limit from {current_part_uncompressed_accumulated_size} B uncompressed data. "
                                       f"Adding it as is.")
                        created_archives_paths.append(str(part_archive_path))

                part_number += 1
                current_part_files_batch = []
                current_part_uncompressed_accumulated_size = 0

            current_part_files_batch.append((file_path, file_uncompressed_size))
            current_part_uncompressed_accumulated_size += file_uncompressed_size

        if current_part_files_batch:
            part_archive_path = output_dir / f"{archive_name_prefix}_part{part_number}.zip"
            logger.info(f"Creating final part {part_number} with {len(current_part_files_batch)} files (uncompressed size: {current_part_uncompressed_accumulated_size} B).")
            if _create_zip_for_files_batch(current_part_files_batch, source_path, part_archive_path, compress=True):
                actual_part_size = part_archive_path.stat().st_size
                if actual_part_size < MAX_ARCHIVE_SIZE_BYTES:
                    created_archives_paths.append(str(part_archive_path))
                else:
                    logger.warning(f"Final archive part {part_archive_path} (Actual: {actual_part_size} B) "
                                   f"EXCEEDS limit from {current_part_uncompressed_accumulated_size} B uncompressed data. "
                                   f"Adding it as is.")
                    created_archives_paths.append(str(part_archive_path))

    if not created_archives_paths and any(source_path.iterdir()):
        logger.error(f"Archiving process for '{source_dir_path}' completed, but NO archive files were successfully produced or met criteria.")
    elif not created_archives_paths and not any(source_path.iterdir()):
        logger.info(f"Source directory '{source_dir_path}' was empty or became empty; no archives created.")

    return created_archives_paths
```
