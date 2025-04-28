# audio_toolkit/ui_widgets.py

import os
from typing import List, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QLineEdit, QListWidget, QAbstractItemView

# Import the scanner from utils
from utils import _scan_folder_recursive


class DropLineEdit(QLineEdit):
    """QLineEdit that accepts a single dropped folder path."""

    pathDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText("Drag & Drop Folder Here or Browse...")

    def dragEnterEvent(self, event: QDragEnterEvent):
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            urls = mime_data.urls()
            # Accept only if it's exactly one URL and it's a local directory
            if len(urls) == 1 and urls[0].isLocalFile():
                path = urls[0].toLocalFile()
                if os.path.isdir(path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        path = event.mimeData().urls()[0].toLocalFile()
        self.setText(path)
        self.pathDropped.emit(path)
        event.acceptProposedAction()


class DropListWidget(QListWidget):
    """QListWidget accepting dropped files and folders (expanding folders based on extensions)."""

    filesDropped = pyqtSignal(list)  # Emits list of valid FILE paths found

    def __init__(self, parent=None, accepted_extensions: Optional[List[str]] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Store accepted extensions (e.g., ['.wav'] or ['.mp3', '.flac'])
        self.accepted_extensions = (
            [ext.lower().lstrip(".") for ext in accepted_extensions]
            if accepted_extensions
            else None
        )
        self.setAlternatingRowColors(True)  # Improve readability

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            # Accept if any URL is a local file or directory
            if any(url.isLocalFile() for url in event.mimeData().urls()):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        all_file_paths = []
        dropped_folders = 0
        dropped_files_valid = 0
        dropped_files_invalid = 0

        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = os.path.normpath(url.toLocalFile())  # Normalize path
                if os.path.isdir(path):
                    dropped_folders += 1
                    if self.accepted_extensions:
                        found_in_folder = _scan_folder_recursive(
                            path, self.accepted_extensions
                        )
                        all_file_paths.extend(found_in_folder)
                    else:
                        # Log warning if folder dropped on list not expecting folders/extensions
                        print(
                            f"Warning: Folder dropped ({path}) but no accepted extensions defined for this list."
                        )
                elif os.path.isfile(path):
                    file_ext = os.path.splitext(path)[1].lower().lstrip(".")
                    if (
                        self.accepted_extensions is None
                        or file_ext in self.accepted_extensions
                    ):
                        all_file_paths.append(path)
                        dropped_files_valid += 1
                    else:
                        dropped_files_invalid += 1  # Track invalid file types dropped

        # Log summary (could be signal later)
        log_summary = f"Drop Event: Processed {dropped_files_valid + dropped_files_invalid} file(s), {dropped_folders} folder(s)."
        if dropped_folders > 0 and self.accepted_extensions:
            log_summary += f" Found {len(all_file_paths) - dropped_files_valid} file(s) in folder(s)."  # Files from scan
        if dropped_files_invalid > 0:
            log_summary += (
                f" Ignored {dropped_files_invalid} file(s) with wrong extension."
            )
        print(log_summary)  # Simple console log for widget action

        if all_file_paths:
            # Remove duplicates that might occur from overlapping drops/scans
            unique_file_paths = sorted(list(set(all_file_paths)))
            self.filesDropped.emit(unique_file_paths)
            event.acceptProposedAction()
        else:
            event.ignore()  # Ignore drop if no valid files resulted
