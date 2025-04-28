# audio_toolkit/utils.py

import os
import shutil
import subprocess
from typing import Optional, List, Iterable

from PyQt5.QtWidgets import QTextEdit
from PyQt5.QtCore import (
    QThread,
    QCoreApplication,
)  # Use QCoreApplication for thread check


def find_executable(name: str) -> Optional[str]:
    """Finds an executable in the system's PATH."""
    return shutil.which(name)


def find_ffmpeg() -> Optional[str]:
    """Finds the ffmpeg executable."""
    return find_executable("ffmpeg")


def find_ffprobe() -> Optional[str]:
    """Finds the ffprobe executable."""
    return find_executable("ffprobe")


def log_message(text_edit: QTextEdit, message: str):
    """Safely appends a message to the QTextEdit from any thread."""
    # Use QCoreApplication.instance() for thread checking if QApplication might not exist yet
    app_instance = QCoreApplication.instance()
    if app_instance and app_instance.thread() != QThread.currentThread():
        # In a real complex app, you'd use signals/slots here.
        # For simplicity, print as fallback if called from wrong thread early.
        print(f"LOG (non-GUI thread): {message}")
    elif text_edit:  # Check if text_edit is valid
        text_edit.append(message)
        # Process events sparingly to avoid sluggishness, maybe only on error?
        if "[!]" in message or "[Error]" in message:
            app_instance and app_instance.processEvents()
    else:
        # Fallback if text_edit isn't ready yet (e.g., early init)
        print(f"LOG (no GUI target): {message}")


def run_ffmpeg_command(cmd: List[str], logger) -> bool:
    """Executes an ffmpeg command and logs output."""
    # Use logger function provided (which should point to log_message)
    logger(f"Executing FFmpeg: {' '.join(cmd)}")
    try:
        # Use CREATE_NO_WINDOW on Windows to prevent console pop-up
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            startupinfo=startupinfo,  # Pass startupinfo here
        )
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            logger(f"  [!] FFmpeg Error (Return Code: {process.returncode}):")
            # Log relevant part of stderr, can be long
            error_lines = stderr.strip().splitlines()
            logger(
                "\n".join(f"    {line}" for line in error_lines[-15:])
            )  # Log last 15 lines
            return False
        else:
            logger("  FFmpeg command successful.")
            # Optionally log info from stderr if needed (ffmpeg often logs info there)
            # logger(f"  FFmpeg Info:\n{stderr.strip()}")
            return True
    except FileNotFoundError:
        logger(
            f"  [!] FFmpeg Error: '{cmd[0]}' command not found. Ensure FFmpeg/FFprobe is installed and in your PATH."
        )
        return False
    except Exception as e:
        logger(f"  [!] FFmpeg Error: An unexpected error occurred: {str(e)}")
        return False


def _scan_folder_recursive(
    folder_path: str, valid_extensions: Iterable[str]
) -> List[str]:
    """Scans a folder recursively for files with given extensions."""
    found_files = []
    extensions_lower = {ext.lower().lstrip(".") for ext in valid_extensions}
    try:
        for root, _, files in os.walk(folder_path):
            for filename in files:
                file_ext = os.path.splitext(filename)[1].lower().lstrip(".")
                if file_ext in extensions_lower:
                    full_path = os.path.join(root, filename)
                    if os.path.isfile(full_path):
                        found_files.append(
                            os.path.normpath(full_path)
                        )  # Normalize path
    except Exception as e:
        print(
            f"Error scanning folder {folder_path}: {e}"
        )  # Basic console log for util errors
    return found_files


def safe_delete(file_path: str, logger):
    """Attempts to delete a file and logs the outcome."""
    logger(f"    - Attempting to delete: {file_path}")
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger(f"      - Successfully deleted.")
        else:
            logger(f"      - File not found, skipping deletion.")
    except Exception as e:
        logger(f"      [!] Failed to delete {os.path.basename(file_path)}: {e}")
