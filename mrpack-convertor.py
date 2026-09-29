import json
import os
import sys
import shutil
import hashlib
import zipfile
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

# User-Agent is required by Modrinth CDN to avoid HTTP 403 Forbidden
USER_AGENT = "MRPACK-Downloader/1.0 (Python-Script)"
MAX_CONCURRENT_DOWNLOADS = 6

MESSAGES = {
    "en": {
        "select_lang": "Choose language / Выберите язык (1: English, 2: Русский) [1]: ",
        "select_file": "Select .mrpack file",
        "select_output": "Select output folder (Cancel to use Desktop)",
        "opening_file": "\n[+] Opening file: {}",
        "file_not_found": "[-] Error: Specified file does not exist!",
        "invalid_zip": "[-] Error: Specified file is not a valid ZIP/MRPACK archive!",
        "index_not_found": "[-] Error: modrinth.index.json not found inside archive!",
        "pack_name": "[+] Modpack Name: {}",
        "target_folder": "[+] Output Directory: {}\n",
        "extracting_overrides": "[*] Extracting local files (overrides)...",
        "extracted_overrides": "[✓] Extracted config/override files: {}\n",
        "no_mods": "[!] No mod files found in manifest to download.",
        "download_start": "[*] Starting download of {} mods (threads: {})...\n",
        "dl_success": "[{}/{}] [SUCCESS] Downloaded: {}",
        "dl_error": "[{}/{}] [ERROR] {}",
        "summary_title": "[SUMMARY] Download process completed!",
        "summary_success": "       Successfully downloaded: {} of {}",
        "summary_errors": "       Errors: {}",
        "summary_path": "       Modpack folder: {}",
        "failed_header": "\nFailed downloads:",
        "critical_error": "[-] Critical error during execution: {}",
        "no_file_chosen": "[-] No file was selected. Exiting.",
        "skip_no_path": "Skipped element without path",
        "security_error": "Path security error: {}",
        "path_proc_error": "Path processing error: {}",
        "no_urls": "No download links available for: {}",
        "hash_mismatch": "Checksum verification failed (Hash Mismatch)",
        "press_enter": "\nPress ENTER to exit...",
    },
    "ru": {
        "select_lang": "Choose language / Выберите язык (1: English, 2: Русский) [2]: ",
        "select_file": "Выберите .mrpack файл",
        "select_output": "Выберите папку для сохранения (Отмена — на Рабочий стол)",
        "opening_file": "\n[+] Открытие файла: {}",
        "file_not_found": "[-] Ошибка: Указанный файл не существует!",
        "invalid_zip": "[-] Ошибка: Указанный файл не является корректным ZIP/MRPACK архивом!",
        "index_not_found": "[-] Ошибка: modrinth.index.json не найден внутри архива!",
        "pack_name": "[+] Название сборки: {}",
        "target_folder": "[+] Папка назначения: {}\n",
        "extracting_overrides": "[*] Извлечение локальных файлов (overrides)...",
        "extracted_overrides": "[✓] Извлечено файлов настроек/дополнений: {}\n",
        "no_mods": "[!] В манифесте не найдено модов для скачивания.",
        "download_start": "[*] Начинаем загрузку {} модов (потоков: {})...\n",
        "dl_success": "[{}/{}] [УСПЕХ] Скачан: {}",
        "dl_error": "[{}/{}] [ОШИБКА] {}",
        "summary_title": "[ИТОГ] Загрузка завершена!",
        "summary_success": "       Успешно скачано: {} из {}",
        "summary_errors": "       Ошибок: {}",
        "summary_path": "       Папка со сборкой: {}",
        "failed_header": "\nСписок нескачанных файлов:",
        "critical_error": "[-] Критическая ошибка при обработке: {}",
        "no_file_chosen": "[-] Файл не был выбран. Завершение работы.",
        "skip_no_path": "Пропущен элемент без пути",
        "security_error": "Ошибка безопасности пути: {}",
        "path_proc_error": "Ошибка обработки пути: {}",
        "no_urls": "Нет доступных ссылок для скачивания: {}",
        "hash_mismatch": "Ошибка проверки контрольной суммы (Hash Mismatch)",
        "press_enter": "\nНажмите ENTER для выхода...",
    }
}


def verify_hash(file_path: Path, hashes: dict) -> bool:
    """
    Checks if downloaded file matches expected SHA512 or SHA1 checksum.
    """
    if "sha512" in hashes:
        hasher = hashlib.sha512()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest().lower() == hashes["sha512"].lower()
    
    if "sha1" in hashes:
        hasher = hashlib.sha1()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest().lower() == hashes["sha1"].lower()
    
    return True


def download_single_file(file_info: dict, target_dir: Path, lang: str = "ru") -> tuple[bool, str]:
    """
    Downloads a single mod file using provided URLs or Modrinth API fallback.
    """
    txt = MESSAGES[lang]
    relative_path = file_info.get("path")
    downloads = list(file_info.get("downloads", []))
    hashes = file_info.get("hashes", {})
    
    if not relative_path:
        return False, txt["skip_no_path"]

    dest_path = target_dir / relative_path
    
    # Security check against Zip Slip path traversal
    try:
        resolved_dest = dest_path.resolve()
        resolved_target = target_dir.resolve()
        if not str(resolved_dest).startswith(str(resolved_target)):
            return False, f"{txt['security_error']}: {relative_path}"
    except Exception as e:
        return False, f"{txt['path_proc_error']}: {e}"

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Fallback to Modrinth API if no direct download URLs are specified
    if not downloads and ("sha512" in hashes or "sha1" in hashes):
        hash_val = hashes.get("sha512") or hashes.get("sha1")
        api_url = f"https://api.modrinth.com/v2/version_file/{hash_val}"
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if "url" in data:
                    downloads.append(data["url"])
        except Exception:
            pass

    if not downloads:
        return False, txt["no_urls"].format(relative_path)

    last_error = "Unknown Error"
    
    for url in downloads:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as response, open(dest_path, "wb") as out_file:
                shutil.copyfileobj(response, out_file)

            # Verify file integrity
            if hashes and not verify_hash(dest_path, hashes):
                if dest_path.exists():
                    dest_path.unlink()
                last_error = txt["hash_mismatch"]
                continue

            return True, relative_path
        except Exception as err:
            last_error = str(err)
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except OSError:
                    pass

    return False, f"{relative_path} — {last_error}"


def extract_overrides(zip_ref: zipfile.ZipFile, target_dir: Path) -> int:
    """
    Extracts local config, shader, or mod overrides included directly in mrpack.
    """
    extracted_count = 0
    for file_info in zip_ref.infolist():
        filename = file_info.filename
        prefix = None
        if filename.startswith("overrides/"):
            prefix = "overrides/"
        elif filename.startswith("client-overrides/"):
            prefix = "client-overrides/"

        if prefix and not file_info.is_dir():
            rel_path = filename[len(prefix):]
            if not rel_path:
                continue
                
            out_path = target_dir / rel_path
            
            # Security check
            if not str(out_path.resolve()).startswith(str(target_dir.resolve())):
                continue

            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zip_ref.open(file_info) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted_count += 1

    return extracted_count


def process_mrpack(mrpack_path: Path, custom_output_dir: Path = None, lang: str = "ru"):
    """
    Main execution workflow for processing .mrpack file.
    """
    txt = MESSAGES[lang]
    print(txt["opening_file"].format(mrpack_path.name))

    if not mrpack_path.exists():
        print(txt["file_not_found"])
        return

    if not zipfile.is_zipfile(mrpack_path):
        print(txt["invalid_zip"])
        return

    # Determine Base Directory (Custom Directory or Desktop fallback)
    if custom_output_dir and custom_output_dir.exists():
        base_dir = custom_output_dir
    else:
        base_dir = Path.home() / "Desktop"
        if not base_dir.exists():
            base_dir = Path.home() / "Рабочий стол"
            if not base_dir.exists():
                base_dir = Path.home()

    try:
        with zipfile.ZipFile(mrpack_path, 'r') as zip_ref:
            if "modrinth.index.json" not in zip_ref.namelist():
                print(txt["index_not_found"])
                return

            with zip_ref.open("modrinth.index.json") as manifest_file:
                index_data = json.load(manifest_file)

            pack_name = index_data.get("name", mrpack_path.stem)
            safe_folder_name = "".join(c for c in pack_name if c.isalnum() or c in (" ", "_", "-")).strip()
            target_folder = base_dir / f"Modpack_{safe_folder_name}"
            target_folder.mkdir(parents=True, exist_ok=True)

            print(txt["pack_name"].format(pack_name))
            print(txt["target_folder"].format(target_folder))

            # Extract Overrides
            print(txt["extracting_overrides"])
            extracted_overrides = extract_overrides(zip_ref, target_folder)
            print(txt["extracted_overrides"].format(extracted_overrides))

            files_list = index_data.get("files", [])
            total_files = len(files_list)

            if total_files == 0:
                print(txt["no_mods"])
                return

            print(txt["download_start"].format(total_files, MAX_CONCURRENT_DOWNLOADS))

            successful = 0
            failed = []

            # Multi-threaded download
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
                futures = {executor.submit(download_single_file, f_info, target_folder, lang): f_info for f_info in files_list}
                
                for idx, future in enumerate(as_completed(futures), start=1):
                    success, result = future.result()
                    if success:
                        successful += 1
                        print(txt["dl_success"].format(idx, total_files, result))
                    else:
                        failed.append(result)
                        print(txt["dl_error"].format(idx, total_files, result))

            # Summary report
            print("\n" + "="*50)
            print(txt["summary_title"])
            print(txt["summary_success"].format(successful, total_files))
            print(txt["summary_errors"].format(len(failed)))
            print(txt["summary_path"].format(target_folder))
            print("="*50)

            if failed:
                print(txt["failed_header"])
                for err in failed:
                    print(f" - {err}")

    except Exception as e:
        print(txt["critical_error"].format(e))


def main():
    root = tk.Tk()
    root.withdraw()  # Hide root GUI window

    # Language selection
    print("Choose Language / Выберите язык:")
    print(" 1. English")
    print(" 2. Русский")
    choice = input("Enter choice / Введите номер (1-2) [Default 2]: ").strip()
    
    lang = "en" if choice == "1" else "ru"
    txt = MESSAGES[lang]

    mrpack_path_str = None
    custom_output_dir = None

    # CLI Argument check
    if len(sys.argv) > 1:
        mrpack_path_str = sys.argv[1]
    else:
        # Ask user via File Dialog
        print(f"\n[*] {txt['select_file']}...")
        mrpack_path_str = filedialog.askopenfilename(
            title=txt["select_file"],
            filetypes=[("Modrinth Pack Files", "*.mrpack"), ("All Files", "*.*")]
        )

    if not mrpack_path_str:
        print(txt["no_file_chosen"])
        return

    # Ask for target folder (optional)
    out_dir_str = filedialog.askdirectory(title=txt["select_output"])
    if out_dir_str:
        custom_output_dir = Path(out_dir_str)

    mrpack_path = Path(mrpack_path_str)
    process_mrpack(mrpack_path, custom_output_dir=custom_output_dir, lang=lang)

    input(txt["press_enter"])


if __name__ == "__main__":
    main()