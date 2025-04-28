# audio_tool_gui.py
import sys
import os
import subprocess
import requests
import mutagen
import threading
import shutil
import functools
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3NoHeaderError, APIC
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any, Iterable

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QTextEdit, QTabWidget,
    QListWidget, QListWidgetItem, QAbstractItemView, QFormLayout, QMessageBox,
    QProgressDialog, QRadioButton, QGroupBox, QSizePolicy
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QUrl
# Added QFont, QPalette, QColor for styling
from PyQt5.QtGui import QPixmap, QIcon, QFont, QPalette, QColor

# --- Constants and Configuration ---
# (Keep existing constants: IMAGE_EXTENSIONS, VALID_MIME_TYPES, EDITABLE_TAGS, EDITABLE_TAGS_LOWER, CLEAR_TAG_PLACEHOLDER, CLEAR_TAG_DISPLAY_TEXT)
IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp']
VALID_MIME_TYPES = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp'
}
EDITABLE_TAGS = {
    'artist': 'Artist',
    'albumartist': 'Album Artist',
    'album': 'Album',
    'genre': 'Genre',
    'date': 'Year/Date (YYYY or YYYY-MM-DD)',
    'tracknumber': 'Track Number (e.g., 5 or 5/12)',
    'discnumber': 'Disc Number (e.g., 1 or 1/2)',
    'composer': 'Composer',
    'comment': 'Comment',
}
EDITABLE_TAGS_LOWER = {k.lower(): v for k, v in EDITABLE_TAGS.items()}
CLEAR_TAG_PLACEHOLDER = "__CLEAR_THIS_TAG__"
CLEAR_TAG_DISPLAY_TEXT = "<Marked for Clearing>"

# --- Helper Functions ---
# (Keep existing helpers: find_ffmpeg, log_message, download_cover, find_local_cover,
#  run_ffmpeg_command, process_flac_file_add_cover, convert_wav_to_flac_simple,
#  convert_wav_to_flac_with_mp3_meta, get_metadata, update_metadata)
# Added _scan_folder_recursive
def find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")

def log_message(text_edit: QTextEdit, message: str):
    if QApplication.instance().thread() != QThread.currentThread():
        print(f"LOG (non-GUI thread): {message}")
    else:
        text_edit.append(message)
        QApplication.processEvents()

def _scan_folder_recursive(folder_path: str, valid_extensions: Iterable[str]) -> List[str]:
    """Scans a folder recursively for files with given extensions."""
    found_files = []
    extensions_lower = {ext.lower().lstrip('.') for ext in valid_extensions}
    try:
        for root, _, files in os.walk(folder_path):
            for filename in files:
                file_ext = os.path.splitext(filename)[1].lower().lstrip('.')
                if file_ext in extensions_lower:
                    full_path = os.path.join(root, filename)
                    if os.path.isfile(full_path): # Double check it's a file
                        found_files.append(full_path)
    except Exception as e:
        # Log this error somewhere accessible, maybe via the main app's logger if possible
        print(f"Error scanning folder {folder_path}: {e}") # Basic console log
    return found_files

# (Keep download_cover, find_local_cover, run_ffmpeg_command, process_flac_file_add_cover,
#  convert_wav_to_flac_simple, convert_wav_to_flac_with_mp3_meta, get_metadata, update_metadata
#  They are unchanged from the previous version)
def download_cover(url: str, save_dir: str, logger, timeout: int = 10) -> Optional[str]:
    logger(f"Attempting to download cover from: {url}")
    try:
        response = requests.get(url, stream=True, timeout=timeout, headers={'User-Agent': 'AudioToolGUI/1.0'})
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
        logger(f"  Content-Type: {content_type}")
        if content_type not in VALID_MIME_TYPES:
            parsed_url = urlparse(url)
            path_ext = os.path.splitext(parsed_url.path)[1].lower().strip('.')
            if f'image/{path_ext}' in VALID_MIME_TYPES: ext = path_ext; logger(f"  Guessed extension from URL: {ext}")
            else: raise ValueError(f"Unsupported or unrecognized image type: {content_type}")
        else: ext = VALID_MIME_TYPES[content_type]
        temp_cover_path = os.path.join(save_dir, f"cover_download_temp.{ext}")
        with open(temp_cover_path, 'wb') as f:
            for chunk in response.iter_content(8192): f.write(chunk)
        logger(f"  Successfully downloaded to temporary file: {temp_cover_path}")
        return temp_cover_path
    except requests.exceptions.Timeout: logger(f"  Download timed out: {url}"); return None
    except requests.exceptions.RequestException as e: logger(f"  Download failed: {url} -> {str(e)}"); return None
    except ValueError as e: logger(f"  Download failed: {str(e)}"); return None
    except Exception as e: logger(f"  An unexpected error occurred during download: {url} -> {str(e)}"); return None

def find_local_cover(directory: str, logger) -> Optional[str]:
    logger(f"Searching for local cover in: {directory}")
    for ext in IMAGE_EXTENSIONS:
        cover_path = os.path.join(directory, f'cover.{ext}')
        if os.path.isfile(cover_path): logger(f"  Found local cover: {cover_path}"); return cover_path
    logger("  No local cover file found."); return None

def run_ffmpeg_command(cmd: List[str], logger) -> bool:
    logger(f"Executing FFmpeg: {' '.join(cmd)}")
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        stdout, stderr = process.communicate()
        if process.returncode != 0: logger(f"  FFmpeg Error (Return Code: {process.returncode}):\n  Stderr: {stderr.strip()}"); return False
        else: logger("  FFmpeg command successful."); return True
    except FileNotFoundError: logger("  FFmpeg Error: 'ffmpeg' command not found."); return False
    except Exception as e: logger(f"  FFmpeg Error: An unexpected error occurred: {str(e)}"); return False

def process_flac_file_add_cover(flac_path: str, cover_image_path: str, logger) -> bool:
    logger(f"Processing (Add Cover): {os.path.basename(flac_path)}")
    temp_output = f"{os.path.splitext(flac_path)[0]}.tmp_cover.flac"
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path: logger("FFmpeg not found."); return False
    cmd = [ ffmpeg_path, '-i', flac_path, '-i', cover_image_path, '-map', '0:a', '-map', '1', '-c', 'copy', '-map_metadata', '0', '-metadata:s:v', 'title=Album cover', '-metadata:s:v', 'comment=Cover (front)', '-disposition:v', 'attached_pic', '-y', temp_output ]
    success = run_ffmpeg_command(cmd, logger)
    if success:
        try: os.replace(temp_output, flac_path); logger(f"  Successfully embedded cover in: {os.path.basename(flac_path)}"); return True
        except Exception as e: logger(f"  Error replacing original file: {str(e)}"); return False
    else:
        logger(f"  Failed to process: {os.path.basename(flac_path)}")
        if os.path.exists(temp_output):
            try: os.remove(temp_output)
            except OSError: pass
        return False

def convert_wav_to_flac_simple(wav_path: str, logger) -> bool:
     logger(f"Processing (WAV->FLAC Simple): {os.path.basename(wav_path)}")
     flac_path = f"{os.path.splitext(wav_path)[0]}.flac"
     ffmpeg_path = find_ffmpeg()
     if not ffmpeg_path: logger("FFmpeg not found."); return False
     if os.path.exists(flac_path): logger(f"  Skipping: FLAC file already exists: {os.path.basename(flac_path)}"); return True
     cmd = [ ffmpeg_path, '-i', wav_path, '-c:a', 'flac', '-y', flac_path ]
     success = run_ffmpeg_command(cmd, logger)
     if success: logger(f"  Successfully converted to: {os.path.basename(flac_path)}"); return True
     else: logger(f"  Failed to convert: {os.path.basename(wav_path)}"); return False

def convert_wav_to_flac_with_mp3_meta(wav_path: str, logger) -> bool:
    logger(f"Processing (WAV->FLAC w/ MP3 Meta): {os.path.basename(wav_path)}")
    base_name = os.path.splitext(os.path.basename(wav_path))[0]
    directory = os.path.dirname(wav_path)
    mp3_path = os.path.join(directory, f"{base_name}.mp3")
    flac_path = os.path.join(directory, f"{base_name}.flac")
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path: logger("FFmpeg not found."); return False
    if not os.path.exists(mp3_path): logger(f"  Skipping: Corresponding MP3 not found: {os.path.basename(mp3_path)}"); return False
    if os.path.exists(flac_path): logger(f"  Skipping: FLAC file already exists: {os.path.basename(flac_path)}"); return True
    cmd = [ ffmpeg_path, '-i', wav_path, '-i', mp3_path, '-map', '0:a', '-map', '1:v?', '-map_metadata', '1', '-c:a', 'flac', '-c:v', 'copy', '-disposition:v', 'attached_pic', '-y', flac_path ]
    success = run_ffmpeg_command(cmd, logger)
    if success: logger(f"  Successfully converted and copied metadata to: {os.path.basename(flac_path)}"); return True
    else: logger(f"  Failed to convert/copy metadata for: {os.path.basename(wav_path)}"); return False

def get_metadata(file_path: str, logger) -> Dict[str, Any]:
    metadata = {}
    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is None: return {'error': 'Unsupported format or read error'}
        for key in EDITABLE_TAGS_LOWER: metadata[key] = "; ".join(audio[key]) if key in audio else ""
        metadata['title'] = "; ".join(audio['title']) if 'title' in audio else ""
        cover_info = "No"
        try:
            audio_raw = mutagen.File(file_path)
            if isinstance(audio_raw, FLAC) and audio_raw.pictures: cover_info = f"Yes ({len(audio_raw.pictures)} image(s))"
            elif isinstance(audio_raw, mutagen.mp3.MP3) and any(tag.startswith("APIC") for tag in audio_raw.tags.keys()): cover_info = "Yes"
            metadata['cover_art'] = cover_info
        except Exception: metadata['cover_art'] = "N/A"
    except ID3NoHeaderError:
        logger(f"  Warning: No ID3 header found in MP3: {os.path.basename(file_path)}")
        metadata = {k: "" for k in EDITABLE_TAGS_LOWER}; metadata['title'] = ""; metadata['cover_art'] = "N/A"; metadata['error'] = "No ID3 Header"
    except Exception as e: logger(f"  Error reading metadata for {os.path.basename(file_path)}: {e}"); metadata['error'] = str(e)
    return metadata

def update_metadata(file_path: str, metadata_updates: Dict[str, str], logger) -> bool:
    logger(f"Updating metadata for: {os.path.basename(file_path)}")
    try:
        audio = mutagen.File(file_path) # Use non-easy for tag check/delete potentially
        if audio is None: logger(f"  [!] Cannot load file: {os.path.basename(file_path)}"); return False
        if audio.tags is None:
            try:
                logger(f"  [*] Adding tags structure to {os.path.basename(file_path)}."); audio.add_tags(); audio.save(); audio = mutagen.File(file_path)
                if audio.tags is None: logger(f"  [!] Failed to add tags structure."); return False
            except Exception as e_add: logger(f"  [!] Error adding tags structure: {e_add}"); return False

        audio_easy = mutagen.File(file_path, easy=True) # Use easy for setting values
        if audio_easy is None or audio_easy.tags is None: logger(f"  [!] Could not reopen file in easy mode."); return False

        needs_save = False; updated_count = 0; deleted_count = 0
        for key, value in metadata_updates.items():
            l_key = key.lower()
            if l_key not in EDITABLE_TAGS_LOWER: continue
            current_value_str = "; ".join(audio_easy[l_key]) if l_key in audio_easy else ""

            if value == "": # Delete request
                if l_key in audio_easy:
                    try:
                        del audio_easy[l_key]; logger(f"      - Deleted tag {EDITABLE_TAGS_LOWER.get(l_key, l_key)}"); needs_save = True; deleted_count += 1
                    except Exception as e_del: logger(f"      - Failed to delete tag {l_key}: {e_del}")
            elif value != current_value_str: # Update request
                try:
                    new_value_list = [v.strip() for v in value.split(';') if v.strip()]
                    if new_value_list: audio_easy[l_key] = new_value_list; logger(f"      - Set {EDITABLE_TAGS_LOWER.get(l_key, l_key)} = '{value}'"); needs_save = True; updated_count += 1
                    elif l_key in audio_easy: # Delete if new value is empty string but tag exists
                         del audio_easy[l_key]; logger(f"      - Deleted tag {EDITABLE_TAGS_LOWER.get(l_key, l_key)} (new value was empty)"); needs_save = True; deleted_count += 1
                except Exception as e_set: logger(f"  [!] Error setting tag '{l_key}': {e_set}")

        if needs_save:
            try: audio_easy.save(); logger(f"      - Saved changes. ({updated_count} updated, {deleted_count} deleted)"); return True
            except Exception as e_save: logger(f"  [!] Error saving file {os.path.basename(file_path)}: {e_save}"); return False
        else: logger("      - No changes needed."); return True
    except Exception as e: logger(f"  [!] Error processing file {os.path.basename(file_path)}: {e}"); return False

# --- Worker Threads (Unchanged) ---
class Worker(QObject):
    finished = pyqtSignal(bool, str); progress = pyqtSignal(str)
    def __init__(self, task_func, *args, **kwargs): super().__init__(); self.task_func = task_func; self.args = args; self.kwargs = kwargs
    def run(self):
        try: result, message = self.task_func(self.progress.emit, *self.args, **self.kwargs); self.finished.emit(result, message)
        except Exception as e: self.finished.emit(False, f"Worker thread error: {str(e)}")

# --- Task Functions for Workers (Unchanged) ---
# (Keep task_add_covers, task_convert_wav, task_edit_metadata)
def task_add_covers(progress_callback, folder_path, cover_url):
    progress_callback(f"Starting cover embedding process for folder: {folder_path}")
    if cover_url: progress_callback(f"Will attempt to use remote cover: {cover_url}")
    files_processed, files_succeeded, files_failed, folders_skipped = 0, 0, 0, 0; all_flac_files = []
    for root, dirs, files in os.walk(folder_path):
        progress_callback(f"\nScanning directory: {root}")
        current_flac_files = [os.path.join(root, f) for f in files if f.lower().endswith('.flac')]
        if not current_flac_files: continue
        all_flac_files.extend(current_flac_files)
        progress_callback(f"  Found {len(current_flac_files)} FLAC file(s).")
        cover_to_use, downloaded_temp_cover = None, None
        if cover_url:
            downloaded_temp_cover = download_cover(cover_url, root, progress_callback)
            if downloaded_temp_cover: cover_to_use = downloaded_temp_cover; progress_callback(f"  Using downloaded cover: {os.path.basename(cover_to_use)}")
            else: progress_callback("  Download failed. Checking local...")
        if not cover_to_use:
            local_cover = find_local_cover(root, progress_callback)
            if local_cover: cover_to_use = local_cover; progress_callback(f"  Using local cover: {os.path.basename(cover_to_use)}")
            else: progress_callback(f"  Skipping directory (no cover found/downloaded): {root}"); folders_skipped += 1; continue
        for flac_path in current_flac_files:
            files_processed += 1
            if process_flac_file_add_cover(flac_path, cover_to_use, progress_callback): files_succeeded += 1
            else: files_failed += 1
        if downloaded_temp_cover and os.path.exists(downloaded_temp_cover):
            try: os.remove(downloaded_temp_cover)
            except Exception as e: progress_callback(f"  Warning: Could not remove temp cover {downloaded_temp_cover}: {e}")
    if not all_flac_files: return False, "No FLAC files found."
    summary = (f"Cover embedding finished.\nTotal FLAC files found: {len(all_flac_files)}\nFiles processed: {files_processed}\nSucceeded: {files_succeeded}, Failed: {files_failed}\nFolders skipped (no cover): {folders_skipped}")
    return files_succeeded > 0 or files_failed == 0, summary

def task_convert_wav(progress_callback, wav_files: List[str], mode: str):
    progress_callback(f"Starting WAV conversion process ({mode})...")
    files_processed, files_succeeded, files_failed = 0, 0, 0
    if not wav_files: return False, "No WAV files selected."
    for wav_path in wav_files:
        files_processed += 1; success = False
        if mode == "simple": success = convert_wav_to_flac_simple(wav_path, progress_callback)
        elif mode == "mp3_meta": success = convert_wav_to_flac_with_mp3_meta(wav_path, progress_callback)
        else: progress_callback(f"  Skipping {os.path.basename(wav_path)}: Invalid mode '{mode}'"); success = False
        if success: files_succeeded += 1
        else: files_failed += 1
    summary = (f"WAV conversion ({mode}) finished.\nFiles processed: {files_processed}\nSucceeded/Skipped(Exists): {files_succeeded}, Failed: {files_failed}")
    return files_succeeded > 0 or files_failed == 0, summary

def task_edit_metadata(progress_callback, file_paths: List[str], metadata_updates: Dict[str, str]):
    progress_callback("Starting metadata editing process...")
    files_processed, files_succeeded, files_failed = 0, 0, 0
    if not file_paths: return False, "No audio files selected."
    if not metadata_updates: return False, "No metadata changes specified."
    progress_callback(f"Applying changes to {len(file_paths)} file(s):")
    for key, value in metadata_updates.items():
         action = f"'{value}'" if value else "[Clear Tag]"; progress_callback(f"  - {EDITABLE_TAGS_LOWER.get(key.lower(), key)}: {action}")
    for file_path in file_paths:
        files_processed += 1
        if update_metadata(file_path, metadata_updates, progress_callback): files_succeeded += 1
        else: files_failed += 1
    summary = (f"Metadata editing finished.\nFiles processed: {files_processed}\nSucceeded (or no changes needed): {files_succeeded}\nFailed: {files_failed}")
    return files_succeeded > 0 or files_failed == 0, summary

# --- Drag and Drop Widgets ---
# (DropLineEdit is unchanged)
class DropLineEdit(QLineEdit):
    pathDropped = pyqtSignal(str)
    def __init__(self, parent=None): super().__init__(parent); self.setAcceptDrops(True); self.setPlaceholderText("Drag & Drop Folder Here or Browse...")
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].isLocalFile() and os.path.isdir(urls[0].toLocalFile()): event.acceptProposedAction(); return
        event.ignore()
    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and os.path.isdir(urls[0].toLocalFile()): path = urls[0].toLocalFile(); self.setText(path); self.pathDropped.emit(path)

# --- Modified DropListWidget to handle folders ---
class DropListWidget(QListWidget):
    """QListWidget accepting dropped files and folders (expanding folders)."""
    filesDropped = pyqtSignal(list) # Emits list of FILE paths

    def __init__(self, parent=None, accepted_extensions: List[str] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Store accepted extensions (e.g., ['.wav'] or ['.mp3', '.flac'])
        self.accepted_extensions = [ext.lower().lstrip('.') for ext in accepted_extensions] if accepted_extensions else None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
             # Accept if any URL is a local file or directory
            if any(url.isLocalFile() for url in event.mimeData().urls()):
                 event.acceptProposedAction()
                 return
        event.ignore()

    def dropEvent(self, event):
        all_file_paths = []
        dropped_folders = 0
        dropped_files = 0

        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isdir(path):
                    dropped_folders += 1
                    # Scan folder if extensions are defined for this list
                    if self.accepted_extensions:
                        found_in_folder = _scan_folder_recursive(path, self.accepted_extensions)
                        all_file_paths.extend(found_in_folder)
                    else:
                         # If no extensions specified, maybe log a warning or ignore folder?
                         # For now, just ignore folders if no extensions defined.
                         print(f"Warning: Folder dropped ({path}) but no accepted extensions defined for this list.")
                elif os.path.isfile(path):
                    dropped_files += 1
                    # Check extension if defined
                    file_ext = os.path.splitext(path)[1].lower().lstrip('.')
                    if self.accepted_extensions is None or file_ext in self.accepted_extensions:
                        all_file_paths.append(path)
                    # else: Ignore file with wrong extension

        if all_file_paths:
             # Log details (optional, could connect a signal to the main app logger)
             print(f"Processing drop: {dropped_files} files, {dropped_folders} folders. Emitting {len(all_file_paths)} valid file paths.")
             self.filesDropped.emit(all_file_paths)


# --- Main Application Window ---

class AudioToolApp(QMainWindow):
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Toolkit GUI")
        self.setGeometry(100, 100, 850, 700)

        self.ffmpeg_path = find_ffmpeg()
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True); self.log_area.setFixedHeight(150); self.log_area.setFontFamily("Courier")
        self.main_layout.addWidget(QLabel("Log Output:"))
        self.main_layout.addWidget(self.log_area)
        self.log_signal.connect(self._log_message_slot)

        self.current_metadata_cache = {}
        self.edit_meta_clear_buttons = {}
        self.edit_meta_inputs = {} # Defined here, populated in setup

        self._setup_add_cover_tab()
        self._setup_wav_converter_tab()
        self._setup_metadata_editor_tab()

        if not self.ffmpeg_path:
            self.log_message("WARNING: FFmpeg not found in PATH. Some functions will not work.", error=True)
            # QMessageBox is shown when trying to use the function

    def _log_message_slot(self, message): self.log_message(message)
    def log_message(self, message: str, error: bool = False):
        prefix = "[Error] " if error else ""; log_message(self.log_area, prefix + message)

    # --- Tab Setup Methods ---
    # (_setup_add_cover_tab is unchanged)
    def _setup_add_cover_tab(self):
        self.tab_add_cover = QWidget(); self.tabs.addTab(self.tab_add_cover, "Embed Cover (FLAC)")
        layout = QVBoxLayout(self.tab_add_cover); layout.setSpacing(10)
        folder_layout = QHBoxLayout(); folder_label = QLabel("Target Folder (contains FLACs):"); self.cover_folder_edit = DropLineEdit(); self.cover_folder_edit.pathDropped.connect(lambda path: self.log_message(f"Folder selected via drop: {path}"))
        browse_folder_button = QPushButton("Browse..."); browse_folder_button.clicked.connect(self._select_cover_folder)
        folder_layout.addWidget(folder_label); folder_layout.addWidget(self.cover_folder_edit); folder_layout.addWidget(browse_folder_button); layout.addLayout(folder_layout)
        url_layout = QHBoxLayout(); url_label = QLabel("Cover Image URL (Optional):"); self.cover_url_edit = QLineEdit(); self.cover_url_edit.setPlaceholderText("Leave blank to use local 'cover.png/jpg/...'")
        url_layout.addWidget(url_label); url_layout.addWidget(self.cover_url_edit); layout.addLayout(url_layout)
        layout.addWidget(QLabel("If URL is provided, it will be downloaded. If download fails or no URL is given,\nthe tool searches for 'cover.png', 'cover.jpg', etc. in each FLAC's directory."))
        self.run_add_cover_button = QPushButton("Start Embedding Covers"); self.run_add_cover_button.setFixedHeight(35); self.run_add_cover_button.clicked.connect(self._run_add_cover); layout.addWidget(self.run_add_cover_button, alignment=Qt.AlignCenter); layout.addStretch()

    # --- Modified WAV Converter Setup (uses new DropListWidget) ---
    def _setup_wav_converter_tab(self):
        self.tab_wav_converter = QWidget()
        self.tabs.addTab(self.tab_wav_converter, "WAV Converter")
        layout = QVBoxLayout(self.tab_wav_converter); layout.setSpacing(10)
        list_layout = QHBoxLayout()
        # Define accepted extensions for WAV tab list
        self.wav_list_widget = DropListWidget(accepted_extensions=['.wav'])
        self.wav_list_widget.filesDropped.connect(self._add_wav_files_from_drop) # Connect the signal
        self.wav_list_widget.setToolTip("Drag & Drop WAV files or folders containing WAVs here")
        list_layout.addWidget(self.wav_list_widget)
        list_button_layout = QVBoxLayout()
        add_files_button = QPushButton("Add WAV Files..."); add_files_button.clicked.connect(self._select_wav_files)
        add_folder_button = QPushButton("Add WAVs from Folder..."); add_folder_button.clicked.connect(self._select_wav_folder)
        clear_list_button = QPushButton("Clear List"); clear_list_button.clicked.connect(self._clear_wav_list)
        list_button_layout.addWidget(add_files_button); list_button_layout.addWidget(add_folder_button)
        list_button_layout.addStretch(); list_button_layout.addWidget(clear_list_button)
        list_layout.addLayout(list_button_layout); layout.addLayout(list_layout)
        options_group = QGroupBox("Conversion Mode"); options_layout = QVBoxLayout(); options_group.setLayout(options_layout)
        self.radio_simple_flac = QRadioButton("Convert to FLAC (Lossless, no metadata copy)"); self.radio_simple_flac.setChecked(True)
        self.radio_meta_flac = QRadioButton("Convert to FLAC (Copy metadata/cover from matching .MP3)")
        options_layout.addWidget(self.radio_simple_flac); options_layout.addWidget(self.radio_meta_flac); layout.addWidget(options_group)
        self.run_convert_wav_button = QPushButton("Start Conversion"); self.run_convert_wav_button.setFixedHeight(35); self.run_convert_wav_button.clicked.connect(self._run_convert_wav); layout.addWidget(self.run_convert_wav_button, alignment=Qt.AlignCenter); layout.addStretch()

    # --- Modified Metadata Editor Setup (uses new DropListWidget) ---
    def _setup_metadata_editor_tab(self):
        self.tab_metadata_editor = QWidget()
        self.tabs.addTab(self.tab_metadata_editor, "Metadata Editor (MP3/FLAC)")
        main_layout = QHBoxLayout(self.tab_metadata_editor)
        left_layout = QVBoxLayout()
        folder_layout = QHBoxLayout(); folder_label = QLabel("Audio Folder:"); self.meta_folder_edit = DropLineEdit(); self.meta_folder_edit.pathDropped.connect(self._load_meta_files_from_drop)
        browse_meta_folder_button = QPushButton("Browse..."); browse_meta_folder_button.clicked.connect(self._select_meta_folder)
        folder_layout.addWidget(folder_label); folder_layout.addWidget(self.meta_folder_edit); folder_layout.addWidget(browse_meta_folder_button); left_layout.addLayout(folder_layout)
        refresh_button = QPushButton("Load/Refresh Files from Folder"); refresh_button.clicked.connect(self._load_meta_files); left_layout.addWidget(refresh_button)

        # Define accepted extensions for Metadata tab list
        self.meta_file_list = DropListWidget(accepted_extensions=['.mp3', '.flac'])
        self.meta_file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.meta_file_list.itemSelectionChanged.connect(self._display_metadata_for_selection)
        self.meta_file_list.filesDropped.connect(self._add_meta_files_from_drop) # Connect the signal
        self.meta_file_list.setToolTip("Drag & Drop MP3/FLAC files or folders containing them here")
        left_layout.addWidget(QLabel("Select file(s) to view/edit metadata:"))
        left_layout.addWidget(self.meta_file_list)
        main_layout.addLayout(left_layout, 1)

        right_widget = QWidget(); right_layout = QVBoxLayout(right_widget); main_layout.addWidget(right_widget, 2)
        current_group = QGroupBox("Current Metadata (Selected File)"); self.current_meta_layout = QFormLayout(); current_group.setLayout(self.current_meta_layout); right_layout.addWidget(current_group)
        self.current_meta_labels = {}; self._setup_current_meta_display()
        edit_group = QGroupBox("Edit Metadata (Applied to Selected Files)"); self.edit_meta_v_layout = QVBoxLayout(); edit_group.setLayout(self.edit_meta_v_layout); right_layout.addWidget(edit_group)
        # Input/clear button dicts are initialized in __init__ now
        self._setup_edit_meta_inputs()
        self.run_edit_meta_button = QPushButton("Apply Metadata Changes to Selected Files"); self.run_edit_meta_button.setFixedHeight(35); self.run_edit_meta_button.clicked.connect(self._run_edit_metadata); right_layout.addWidget(self.run_edit_meta_button, alignment=Qt.AlignCenter); right_layout.addStretch()

    def _setup_current_meta_display(self):
        # (Unchanged from previous version)
        for label in self.current_meta_labels.values(): label.deleteLater()
        self.current_meta_labels = {}
        while self.current_meta_layout.count(): child = self.current_meta_layout.takeAt(0); widget = child.widget(); widget and widget.deleteLater()
        title_label = QLabel("-"); title_label.setWordWrap(True); self.current_meta_layout.addRow("Title:", title_label); self.current_meta_labels['title'] = title_label
        for key, display_name in EDITABLE_TAGS_LOWER.items(): value_label = QLabel("-"); value_label.setWordWrap(True); self.current_meta_layout.addRow(f"{display_name}:", value_label); self.current_meta_labels[key] = value_label
        cover_label = QLabel("-"); self.current_meta_layout.addRow("Cover Art:", cover_label); self.current_meta_labels['cover_art'] = cover_label

    def _setup_edit_meta_inputs(self):
        # (Unchanged from previous version - includes clear buttons)
        for key in list(self.edit_meta_inputs.keys()): self.edit_meta_inputs[key].deleteLater(); self.edit_meta_clear_buttons[key].deleteLater(); del self.edit_meta_inputs[key]; del self.edit_meta_clear_buttons[key]
        while self.edit_meta_v_layout.count():
             item = self.edit_meta_v_layout.takeAt(0); widget = item.widget(); layout = item.layout()
             if widget: widget.deleteLater()
             if layout:
                 while (layout.count()): sub_item = layout.takeAt(0); sub_widget = sub_item.widget(); sub_widget and sub_widget.deleteLater(); layout.deleteLater()
        for key, display_name in EDITABLE_TAGS_LOWER.items():
            row_layout = QHBoxLayout(); label = QLabel(f"{display_name}:"); label.setMinimumWidth(100); label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            input_widget = QLineEdit(); input_widget.setPlaceholderText("Leave unchanged or enter new value"); input_widget.setProperty("originalValue", ""); input_widget.textChanged.connect(functools.partial(self._reset_clear_visual_state, input_widget))
            clear_button = QPushButton("Clear"); clear_button.setFixedWidth(50); clear_button.setToolTip(f"Mark '{display_name}' to be cleared/removed"); clear_button.clicked.connect(functools.partial(self._handle_clear_button_click, key))
            row_layout.addWidget(label); row_layout.addWidget(input_widget); row_layout.addWidget(clear_button); self.edit_meta_v_layout.addLayout(row_layout)
            self.edit_meta_inputs[key] = input_widget; self.edit_meta_clear_buttons[key] = clear_button
        help_label = QLabel("<small><i>Edit fields show common value for multiple selections. Enter new value to change.\nUse 'Clear' button to remove a tag. Empty fields are ignored if not cleared.</i></small>"); help_label.setWordWrap(True); self.edit_meta_v_layout.addWidget(help_label)

    def _handle_clear_button_click(self, key: str):
        # (Unchanged from previous version)
        if key in self.edit_meta_inputs:
            input_widget = self.edit_meta_inputs[key]; input_widget.setProperty("valueToSet", CLEAR_TAG_PLACEHOLDER); input_widget.setText(CLEAR_TAG_DISPLAY_TEXT)
            font = input_widget.font(); font.setItalic(True); input_widget.setFont(font)
            palette = input_widget.palette(); palette.setColor(QPalette.Text, QColor('gray')); input_widget.setPalette(palette)
            self.log_message(f"Marked '{EDITABLE_TAGS_LOWER.get(key, key)}' for clearing.")

    def _reset_clear_visual_state(self, input_widget: QLineEdit):
        # (Unchanged from previous version)
        font = input_widget.font()
        if font.italic():
             font.setItalic(False); input_widget.setFont(font); input_widget.setPalette(QApplication.style().standardPalette())
             if not input_widget.text(): input_widget.setPlaceholderText("Leave unchanged or enter new value") # Restore placeholder if cleared by typing
        if input_widget.property("valueToSet") == CLEAR_TAG_PLACEHOLDER: input_widget.setProperty("valueToSet", None)


    # --- Event Handlers / Slots ---
    # (_select_folder, _select_cover_folder, _select_meta_folder, _load_meta_files_from_drop,
    # _select_wav_files, _select_wav_folder, _clear_wav_list, _load_meta_files are unchanged)
    def _select_folder(self, line_edit_widget: QLineEdit):
        current_path = line_edit_widget.text() or os.path.expanduser("~"); folder = QFileDialog.getExistingDirectory(self, "Select Folder", current_path)
        if folder: line_edit_widget.setText(folder); self.log_message(f"Folder selected: {folder}"); return folder
        return None
    def _select_cover_folder(self): self._select_folder(self.cover_folder_edit)
    def _select_meta_folder(self): folder = self._select_folder(self.meta_folder_edit); folder and self._load_meta_files()
    def _load_meta_files_from_drop(self, folder_path): self.log_message(f"Folder selected via drop: {folder_path}"); self._load_meta_files()
    def _select_wav_files(self): files, _ = QFileDialog.getOpenFileNames(self, "Select WAV Files", os.path.expanduser("~"), "WAV Files (*.wav)"); files and self._add_wav_files(files)
    def _select_wav_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing WAV Files", os.path.expanduser("~"))
        if folder:
            self.log_message(f"Scanning folder for WAV files: {folder}")
            wav_files = _scan_folder_recursive(folder, ['.wav']) # Use the recursive scanner
            if wav_files: self._add_wav_files(wav_files)
            else: self.log_message("No WAV files found in the selected folder or subfolders.")
    def _clear_wav_list(self): self.wav_list_widget.clear(); self.log_message("WAV file list cleared.")
    def _load_meta_files(self):
        folder = self.meta_folder_edit.text()
        if not folder or not os.path.isdir(folder): self.log_message("Please select a valid folder first.", error=True); return
        self.meta_file_list.clear(); self.log_message(f"Scanning for MP3/FLAC files in: {folder}"); count = 0
        try:
            # Use the recursive scanner for consistency, although original only scanned top level
            audio_files = _scan_folder_recursive(folder, ['.mp3', '.flac'])
            for full_path in sorted(audio_files): # Sort by full path might mix subdirs, sort by basename if preferred
                filename = os.path.basename(full_path)
                item = QListWidgetItem(filename); item.setData(Qt.UserRole, full_path); item.setToolTip(full_path)
                self.meta_file_list.addItem(item); count += 1
            self.log_message(f"Found {count} audio files (including subfolders).")
            self._clear_metadata_display_and_inputs()
        except Exception as e: self.log_message(f"Error scanning folder {folder}: {e}", error=True); QMessageBox.warning(self, "Folder Scan Error", f"Could not scan files in folder:\n{e}")

    # --- Modified Add Files Methods to handle incoming list from DropListWidget ---
    def _add_files_to_list(self, list_widget: QListWidget, file_paths: List[str], type_name: str):
        """Generic method to add unique files to a QListWidget."""
        current_files = set(list_widget.item(i).data(Qt.UserRole) for i in range(list_widget.count()))
        added_count = 0
        skipped_count = 0
        for file_path in file_paths:
            # Basic check if it's a file path (DropListWidget should ensure this)
            if os.path.isfile(file_path):
                 if file_path not in current_files:
                    item = QListWidgetItem(os.path.basename(file_path))
                    item.setData(Qt.UserRole, file_path)
                    item.setToolTip(file_path)
                    list_widget.addItem(item)
                    current_files.add(file_path)
                    added_count += 1
                 else:
                      skipped_count +=1 # Duplicate
            # else: Should not happen if DropListWidget works correctly

        log_msg = f"Added {added_count} new {type_name} file(s)."
        if skipped_count > 0:
             log_msg += f" Skipped {skipped_count} duplicates."
        # Check if any files were provided at all
        if not file_paths and added_count == 0:
             log_msg = f"No {type_name} files found in the dropped items."
        elif added_count == 0 and skipped_count > 0:
             log_msg = f"No new {type_name} files added (all were duplicates)."

        self.log_message(log_msg)


    def _add_wav_files(self, file_paths: List[str]):
        """Adds WAV files provided in a list."""
        # Filter just in case, though DropListWidget should pre-filter
        wav_files_filtered = [p for p in file_paths if p.lower().endswith('.wav')]
        self._add_files_to_list(self.wav_list_widget, wav_files_filtered, "WAV")

    def _add_wav_files_from_drop(self, file_paths: List[str]):
        """Handles file list emitted from DropListWidget for WAV tab."""
        self.log_message(f"Processing {len(file_paths)} dropped/scanned item(s) for WAV tab.")
        self._add_wav_files(file_paths) # Pass the list, filtering happens inside

    def _add_meta_files(self, file_paths: List[str]):
        """Adds MP3/FLAC files provided in a list."""
        # Filter just in case
        meta_files_filtered = [p for p in file_paths if p.lower().endswith(('.mp3', '.flac'))]
        self._add_files_to_list(self.meta_file_list, meta_files_filtered, "MP3/FLAC")

    def _add_meta_files_from_drop(self, file_paths: List[str]):
         """Handles file list emitted from DropListWidget for Metadata tab."""
         self.log_message(f"Processing {len(file_paths)} dropped/scanned item(s) for Metadata tab.")
         self._add_meta_files(file_paths) # Pass the list, filtering happens inside

    def _clear_metadata_display_and_inputs(self):
        # (Unchanged from previous version)
        for key, label in self.current_meta_labels.items(): label.setText("-"); label.setToolTip("")
        for key, input_widget in self.edit_meta_inputs.items():
            input_widget.blockSignals(True); input_widget.clear(); input_widget.setProperty("originalValue", ""); input_widget.setProperty("valueToSet", None)
            input_widget.setPlaceholderText("Leave unchanged or enter new value"); font = input_widget.font(); font.setItalic(False); input_widget.setFont(font)
            input_widget.setPalette(QApplication.style().standardPalette()); input_widget.blockSignals(False)
        self.current_metadata_cache = {}


    # --- Modified Metadata Display Logic for Inputs ---
    def _display_metadata_for_selection(self):
        selected_items = self.meta_file_list.selectedItems()
        self._clear_metadata_display_and_inputs() # Clear everything first

        if not selected_items: return

        # --- Common logic for single and multiple ---
        all_metadata = []
        has_error = False
        common_values = {} # Store common values found across files
        is_single_selection = (len(selected_items) == 1)

        # --- Get metadata for all selected files ---
        log_prefix = "Loading metadata for:" if is_single_selection else f"Loading common metadata for {len(selected_items)} files..."
        self.log_message(log_prefix)
        for i, item in enumerate(selected_items):
            file_path = item.data(Qt.UserRole)
            if i == 0 and is_single_selection: self.log_message(f"  - {os.path.basename(file_path)}") # Log filename only for single
            meta = get_metadata(file_path, lambda msg: None) # Suppress logs for individual reads here
            if meta.get('error'):
                 self.log_message(f"Error reading metadata for {os.path.basename(file_path)}: {meta['error']}", error=True)
                 has_error = True
            all_metadata.append(meta)

        if has_error and not all_metadata: # Total failure
             self.current_meta_labels['title'].setText("<font color='red'>Error reading file(s)</font>")
             return

        # --- Determine Common Values (runs even for single selection) ---
        if all_metadata:
            first_meta = all_metadata[0]
            for key in list(EDITABLE_TAGS_LOWER.keys()) + ['title', 'cover_art']: # Include non-editable for display
                 first_value = first_meta.get(key, "")
                 all_same = all(meta.get(key, "") == first_value for meta in all_metadata[1:])
                 if all_same:
                      common_values[key] = first_value
                 else:
                      common_values[key] = "<Multiple Values>" # Placeholder

        # --- Update Display Labels ---
        if has_error: self.current_meta_labels['title'].setText("<font color='red'>Error reading some files</font>")
        for key, label in self.current_meta_labels.items():
             value = common_values.get(key, "")
             is_multiple = (value == "<Multiple Values>")
             display_value = "<i><Multiple Values></i>" if is_multiple else (value if value else "-")
             tooltip = "Different values across selected files" if is_multiple else str(value)
             label.setText(str(display_value)); label.setToolTip(tooltip)

        # --- Update Input Fields ---
        for key, input_widget in self.edit_meta_inputs.items():
             value = common_values.get(key, "") # Get common value for editable tags
             input_widget.blockSignals(True)
             input_widget.setProperty("valueToSet", None) # Reset clear intent
             self._reset_clear_visual_state(input_widget) # Reset visuals

             if value == "<Multiple Values>":
                  input_widget.clear()
                  input_widget.setPlaceholderText("<Multiple Values - Enter new value to apply to all>")
                  input_widget.setProperty("originalValue", "<Multiple Values>")
             else: # Single value (could be empty string)
                  input_widget.setText(value)
                  input_widget.setPlaceholderText("Leave unchanged or enter new value")
                  input_widget.setProperty("originalValue", value)
             input_widget.blockSignals(False)

        # Store cache only for single selection
        self.current_metadata_cache = all_metadata[0] if is_single_selection and not has_error else {}


    # (_run_operation, _operation_finished, _run_add_cover, _run_convert_wav, _run_edit_metadata, closeEvent are unchanged)
    def _run_operation(self, task_function, button_to_disable: QPushButton, *args):
        if not self.ffmpeg_path and ("convert" in task_function.__name__ or "add_cover" in task_function.__name__): QMessageBox.critical(self, "FFmpeg Error", "FFmpeg not found. This operation cannot proceed."); return
        button_to_disable.setEnabled(False); self.log_message("Starting operation...")
        self.thread = QThread(); self.worker = Worker(task_function, *args); self.worker.moveToThread(self.thread)
        self.worker.progress.connect(self.log_message); self.thread.started.connect(self.worker.run); self.worker.finished.connect(self.thread.quit); self.worker.finished.connect(self.worker.deleteLater); self.thread.finished.connect(self.thread.deleteLater)
        self.worker.finished.connect(lambda success, msg: self._operation_finished(success, msg, button_to_disable)); self.thread.start()
    def _operation_finished(self, success: bool, message: str, button_to_enable: QPushButton):
        self.log_message(message, error=not success)
        msg_box = QMessageBox.information if success else QMessageBox.warning
        msg_box(self, "Operation Complete" if success else "Operation Finished with Issues", f"Operation finished.\n\nDetails:\n{message}")
        button_to_enable.setEnabled(True)
        if button_to_enable == self.run_edit_meta_button: self._display_metadata_for_selection()
    def _run_add_cover(self):
        folder = self.cover_folder_edit.text(); cover_url = self.cover_url_edit.text().strip() or None
        if not folder or not os.path.isdir(folder): QMessageBox.warning(self, "Input Error", "Please select a valid target folder."); return
        self._run_operation(task_add_covers, self.run_add_cover_button, folder, cover_url)
    def _run_convert_wav(self):
        wav_files = [self.wav_list_widget.item(i).data(Qt.UserRole) for i in range(self.wav_list_widget.count())]
        if not wav_files: QMessageBox.warning(self, "Input Error", "Please add WAV files to the list first."); return
        mode = "mp3_meta" if self.radio_meta_flac.isChecked() else "simple"; self._run_operation(task_convert_wav, self.run_convert_wav_button, wav_files, mode)
    def _run_edit_metadata(self):
        selected_items = self.meta_file_list.selectedItems()
        if not selected_items: QMessageBox.warning(self, "Input Error", "Please select one or more audio files to modify."); return
        file_paths = [item.data(Qt.UserRole) for item in selected_items]; is_single_selection = (len(selected_items) == 1)
        metadata_updates = {}; changes_list_for_confirm = []
        for key, input_widget in self.edit_meta_inputs.items():
            current_text = input_widget.text(); clear_button_pressed = (input_widget.property("valueToSet") == CLEAR_TAG_PLACEHOLDER)
            original_value = input_widget.property("originalValue") # This holds single value or "<Multiple Values>"
            if clear_button_pressed:
                metadata_updates[key] = ""; changes_list_for_confirm.append(f"- {EDITABLE_TAGS_LOWER.get(key, key)}: [Clear Tag]")
            elif current_text != CLEAR_TAG_DISPLAY_TEXT: # Ignore if it still shows the clearing placeholder
                is_changed = False
                if original_value == "<Multiple Values>": is_changed = bool(current_text.strip()) # Change if *any* text entered for multi
                else: is_changed = (current_text != original_value) # Change if text differs from single original
                if is_changed:
                     new_value = current_text.strip(); metadata_updates[key] = new_value; changes_list_for_confirm.append(f"- {EDITABLE_TAGS_LOWER.get(key, key)}: Set to '{new_value}'")
        if not metadata_updates: QMessageBox.information(self, "No Changes", "No changes detected.\nEnter new values or use the 'Clear' button to modify metadata."); return
        confirm_msg = f"Apply the following changes to {len(file_paths)} selected file(s)?\n\n" + "\n".join(changes_list_for_confirm)
        reply = QMessageBox.question(self, 'Confirm Metadata Update', confirm_msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes: self._run_operation(task_edit_metadata, self.run_edit_meta_button, file_paths, metadata_updates)
        else: self.log_message("Metadata update cancelled by user.")
    def closeEvent(self, event):
        if hasattr(self, 'thread') and self.thread and self.thread.isRunning():
            reply = QMessageBox.question(self, 'Confirm Exit', "Operation in progress. Exit anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes: self.thread.quit(); self.thread.wait(500); event.accept()
            else: event.ignore()
        else: event.accept()


# --- Main Execution ---

if __name__ == '__main__':
    app = QApplication(sys.argv)

    # --- Set Font ---
    font = QFont("Calibri", 10) # Or 11, adjust size as needed
    app.setFont(font)
    # --- End Set Font ---

    app.setStyle('Fusion') # Optional styling
    main_window = AudioToolApp()
    main_window.show()
    sys.exit(app.exec_())