# audio_toolkit/main_app.py

import functools
import os
import sys
import json # To display fetched data
import re   # For parsing ID input
from typing import List, Optional, Dict, Any

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPalette, QColor
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QFileDialog,
    QTextEdit,
    QTabWidget,
    QListWidgetItem,
    QAbstractItemView,
    QFormLayout,
    QMessageBox,
    QRadioButton,
    QGroupBox,
    QComboBox,
    QCheckBox, QPlainTextEdit,  # Added ComboBox, CheckBox
)

from constants import (  # Import necessary constants
    AUDIO_META_EXTENSIONS,
    AUDIO_WAV_EXTENSIONS,
    CUE_EXTENSIONS,
    EDITABLE_TAGS_LOWER,
    CLEAR_TAG_PLACEHOLDER,
    CLEAR_TAG_DISPLAY_TEXT,
    MULTIPLE_VALUES_PLACEHOLDER,
    MULTIPLE_VALUES_DISPLAY,
    CUE_OUTPUT_FORMATS,
    CUE_COLLECTION_OPTS,
    CUE_OVERWRITE_MODES,
)
from vgmdb_scraper import _parse_date, _get_preferred_lang
# Import local modules
from ui_widgets import DropLineEdit, DropListWidget
from utils import (
    log_message,
    find_ffmpeg,
    _scan_folder_recursive,
    find_ffprobe,
)  # Import necessary utils
from worker_tasks import (
    Worker,
    task_add_covers,
    task_convert_wav,
    task_edit_metadata,
    task_split_cue, task_fetch_vgmdb,
)


class AudioToolApp(QMainWindow):
    log_signal = pyqtSignal(str)  # For messages from non-main threads if needed

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Toolkit GUI")
        self.setGeometry(100, 100, 900, 800)  # Increased height slightly

        self.ffmpeg_path = find_ffmpeg()
        self.current_metadata_cache = {}
        self.edit_meta_clear_buttons = {}
        self.edit_meta_inputs = {}
        self.fetched_vgmdb_data: Optional[Dict] = None  # To store fetched data

        # --- Central Widget and Main Layout ---
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # --- Tabs ---
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        # --- Log Area ---
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True);
        self.log_area.setFixedHeight(170);
        self.log_area.setFontFamily("Courier New")
        self.main_layout.addWidget(QLabel("Log Output:"))
        self.main_layout.addWidget(self.log_area)

        # --- Setup Tabs ---
        self._setup_vgmdb_fetch_tab()  # Add new tab
        self._setup_cue_splitter_tab()
        self._setup_add_cover_tab()
        self._setup_wav_converter_tab()
        self._setup_metadata_editor_tab()

        # --- Final Checks ---
        if not self.ffmpeg_path:
            self.log_message("[Warning] FFmpeg/FFprobe not found in PATH. Some functions may fail.", error=True)

    # --- Logging ---
    def _log_message_slot(self, message):
        self.log_message(message)

    def log_message(self, message: str, error: bool = False):
        prefix = (
            "[Error] " if error else "[Info] " if not message.startswith("[") else ""
        )
        # Use the utility log_message
        log_message(self.log_area, prefix + message.strip())

    # --- Tab Setup Methods ---
    def _setup_vgmdb_fetch_tab(self):
        self.tab_vgmdb = QWidget()
        self.tabs.addTab(self.tab_vgmdb, "VGMdb Fetch")
        layout = QVBoxLayout(self.tab_vgmdb);
        layout.setSpacing(10)

        # Input Area
        input_layout = QHBoxLayout()
        input_label = QLabel("VGMdb Album ID or URL:")
        self.vgmdb_id_edit = QLineEdit()
        self.vgmdb_id_edit.setPlaceholderText("e.g., 116981 or https://vgmdb.net/album/116981")
        self.fetch_vgmdb_button = QPushButton("Fetch Data")
        self.fetch_vgmdb_button.clicked.connect(self._run_fetch_vgmdb)
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.vgmdb_id_edit, 1)  # Allow ID edit to stretch
        input_layout.addWidget(self.fetch_vgmdb_button)
        layout.addLayout(input_layout)

        # Display Area
        display_label = QLabel("Fetched Data:")
        self.vgmdb_display_area = QPlainTextEdit()  # Use PlainText for better performance with large text
        self.vgmdb_display_area.setReadOnly(True)
        #self.vgmdb_display_area.setFontFamily("Courier New")
        self.vgmdb_display_area.setPlaceholderText("Album data will appear here after fetching...")
        layout.addWidget(display_label)
        layout.addWidget(self.vgmdb_display_area)

        # Action Buttons Area
        action_layout = QHBoxLayout()
        self.apply_vgmdb_meta_button = QPushButton("Apply Data to Metadata Tab Inputs")
        self.apply_vgmdb_meta_button.clicked.connect(self._apply_vgmdb_to_metadata)
        self.apply_vgmdb_meta_button.setEnabled(False)  # Disabled initially

        self.apply_vgmdb_cover_button = QPushButton("Apply Cover URL to Cover Tab")
        self.apply_vgmdb_cover_button.clicked.connect(self._apply_vgmdb_to_cover_url)
        self.apply_vgmdb_cover_button.setEnabled(False)  # Disabled initially

        action_layout.addStretch()
        action_layout.addWidget(self.apply_vgmdb_meta_button)
        action_layout.addWidget(self.apply_vgmdb_cover_button)
        action_layout.addStretch()
        layout.addLayout(action_layout)

    def _setup_cue_splitter_tab(self):
        self.tab_cue_splitter = QWidget()
        self.tabs.addTab(self.tab_cue_splitter, "CUE Splitter")
        layout = QVBoxLayout(self.tab_cue_splitter)
        layout.setSpacing(10)

        # Input Files List (No changes needed here)
        list_label = QLabel("Drag & Drop CUE files or folders containing them:")
        self.cue_list_widget = DropListWidget(accepted_extensions=CUE_EXTENSIONS)
        self.cue_list_widget.filesDropped.connect(self._add_cue_files_from_drop)
        self.cue_list_widget.setFixedHeight(150)

        list_button_layout = QVBoxLayout()
        list_button_layout.setSpacing(5)
        add_cue_files_button = QPushButton("Add CUE Files...")
        add_cue_files_button.clicked.connect(self._select_cue_files)
        add_cue_folder_button = QPushButton("Add CUEs from Folder...")
        add_cue_folder_button.clicked.connect(self._select_cue_folder)
        clear_cue_list_button = QPushButton("Clear List")
        clear_cue_list_button.clicked.connect(self._clear_cue_list)
        list_button_layout.addWidget(add_cue_files_button)
        list_button_layout.addWidget(add_cue_folder_button)
        list_button_layout.addStretch()
        list_button_layout.addWidget(clear_cue_list_button)

        list_area_layout = QHBoxLayout()
        list_area_layout.addWidget(self.cue_list_widget, 3)
        list_area_layout.addLayout(list_button_layout, 1)

        layout.addWidget(list_label)
        layout.addLayout(list_area_layout)

        # Options Group
        options_group = QGroupBox("Splitting Options")
        options_layout = QFormLayout(options_group)
        options_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        options_layout.setLabelAlignment(Qt.AlignRight)

        # Output Format (No changes)
        self.cue_format_combo = QComboBox()
        self.cue_format_combo.addItems(CUE_OUTPUT_FORMATS)
        self.cue_format_combo.setCurrentText("flac")
        options_layout.addRow("Output Format:", self.cue_format_combo)

        # Collection Subdirectory (No changes)
        self.cue_collection_combo = QComboBox()
        self.cue_collection_combo.addItems(CUE_COLLECTION_OPTS.keys())
        self.cue_collection_combo.setCurrentText("None")
        options_layout.addRow("Create Subdirectory:", self.cue_collection_combo)

        # --- MODIFICATION START ---
        # Output Directory - Use DropLineEdit instead of QLineEdit
        output_dir_layout = QHBoxLayout()
        self.cue_outputdir_edit = DropLineEdit()  # Use DropLineEdit
        self.cue_outputdir_edit.setPlaceholderText(
            "Default: Same directory as CUE file (or drop folder)"
        )
        # Optionally connect the signal for logging
        self.cue_outputdir_edit.pathDropped.connect(
            lambda path: self.log_message(f"Output directory set via drop: {path}")
        )
        browse_output_button = QPushButton("Browse...")
        browse_output_button.clicked.connect(self._select_cue_output_dir)
        output_dir_layout.addWidget(self.cue_outputdir_edit)
        output_dir_layout.addWidget(browse_output_button)
        options_layout.addRow("Output Directory (Optional):", output_dir_layout)
        # --- MODIFICATION END ---

        # Overwrite Option (No changes)
        self.cue_overwrite_checkbox = QCheckBox(
            "Overwrite existing split files in destination"
        )
        self.cue_overwrite_checkbox.setChecked(False)
        options_layout.addRow("", self.cue_overwrite_checkbox)

        layout.addWidget(options_group)

        # Action Button (No changes)
        self.run_split_cue_button = QPushButton("Start Splitting CUE Files")
        self.run_split_cue_button.setFixedHeight(35)
        self.run_split_cue_button.clicked.connect(self._run_split_cue)
        layout.addWidget(self.run_split_cue_button, alignment=Qt.AlignCenter)

        layout.addStretch()

    def _setup_add_cover_tab(self):
        self.tab_add_cover = QWidget();
        self.tabs.addTab(self.tab_add_cover, "Embed Cover (FLAC)")
        layout = QVBoxLayout(self.tab_add_cover);
        layout.setSpacing(10);
        folder_layout = QHBoxLayout();
        folder_label = QLabel("Target Folder (contains FLACs):");
        self.cover_folder_edit = DropLineEdit();
        self.cover_folder_edit.pathDropped.connect(lambda path: self.log_message(f"Folder selected via drop: {path}"));
        browse_folder_button = QPushButton("Browse...");
        browse_folder_button.clicked.connect(self._select_cover_folder);
        folder_layout.addWidget(folder_label);
        folder_layout.addWidget(self.cover_folder_edit);
        folder_layout.addWidget(browse_folder_button);
        layout.addLayout(folder_layout);
        url_layout = QHBoxLayout();
        url_label = QLabel("Cover Image URL (Optional):");
        self.cover_url_edit = QLineEdit();
        self.cover_url_edit.setPlaceholderText("Leave blank to use local 'cover.png/jpg/...'");
        url_layout.addWidget(url_label);
        url_layout.addWidget(self.cover_url_edit);
        layout.addLayout(url_layout);
        layout.addWidget(QLabel(
            "If URL is provided, it will be downloaded. If download fails or no URL is given,\nthe tool searches for 'cover.png', 'cover.jpg', etc. in each FLAC's directory."));
        self.run_add_cover_button = QPushButton("Start Embedding Covers");
        self.run_add_cover_button.setFixedHeight(35);
        self.run_add_cover_button.clicked.connect(self._run_add_cover);
        layout.addWidget(self.run_add_cover_button, alignment=Qt.AlignCenter);
        layout.addStretch()

    def _setup_wav_converter_tab(self):
        # (Unchanged from previous version, ensure imports correct)
        self.tab_wav_converter = QWidget()
        self.tabs.addTab(self.tab_wav_converter, "WAV Converter")
        layout = QVBoxLayout(self.tab_wav_converter)
        layout.setSpacing(10)
        list_layout = QHBoxLayout()
        self.wav_list_widget = DropListWidget(accepted_extensions=AUDIO_WAV_EXTENSIONS)
        self.wav_list_widget.filesDropped.connect(self._add_wav_files_from_drop)
        self.wav_list_widget.setToolTip(
            "Drag & Drop WAV files or folders containing WAVs here"
        )
        list_layout.addWidget(self.wav_list_widget)
        list_button_layout = QVBoxLayout()
        add_files_button = QPushButton("Add WAV Files...")
        add_files_button.clicked.connect(self._select_wav_files)
        add_folder_button = QPushButton("Add WAVs from Folder...")
        add_folder_button.clicked.connect(self._select_wav_folder)
        clear_list_button = QPushButton("Clear List")
        clear_list_button.clicked.connect(self._clear_wav_list)
        list_button_layout.addWidget(add_files_button)
        list_button_layout.addWidget(add_folder_button)
        list_button_layout.addStretch()
        list_button_layout.addWidget(clear_list_button)
        list_layout.addLayout(list_button_layout)
        layout.addLayout(list_layout)
        options_group = QGroupBox("Conversion Mode")
        options_layout = QVBoxLayout()
        options_group.setLayout(options_layout)
        self.radio_simple_flac = QRadioButton(
            "Convert to FLAC (Lossless, no metadata copy)"
        )
        self.radio_simple_flac.setChecked(True)
        self.radio_meta_flac = QRadioButton(
            "Convert to FLAC (Copy metadata/cover from matching .MP3)"
        )
        options_layout.addWidget(self.radio_simple_flac)
        options_layout.addWidget(self.radio_meta_flac)
        layout.addWidget(options_group)
        self.run_convert_wav_button = QPushButton("Start Conversion")
        self.run_convert_wav_button.setFixedHeight(35)
        self.run_convert_wav_button.clicked.connect(self._run_convert_wav)
        layout.addWidget(self.run_convert_wav_button, alignment=Qt.AlignCenter)
        layout.addStretch()

    def _setup_metadata_editor_tab(self):
        self.tab_metadata_editor = QWidget(); self.tabs.addTab(self.tab_metadata_editor, "Metadata Editor (MP3/FLAC)")
        main_layout = QHBoxLayout(self.tab_metadata_editor); left_layout = QVBoxLayout(); folder_layout = QHBoxLayout(); folder_label = QLabel("Audio Folder:"); self.meta_folder_edit = DropLineEdit(); self.meta_folder_edit.pathDropped.connect(self._load_meta_files_from_drop); browse_meta_folder_button = QPushButton("Browse..."); browse_meta_folder_button.clicked.connect(self._select_meta_folder); folder_layout.addWidget(folder_label); folder_layout.addWidget(self.meta_folder_edit); folder_layout.addWidget(browse_meta_folder_button); left_layout.addLayout(folder_layout); refresh_button = QPushButton("Load/Refresh Files from Folder"); refresh_button.clicked.connect(self._load_meta_files); left_layout.addWidget(refresh_button); self.meta_file_list = DropListWidget(accepted_extensions=AUDIO_META_EXTENSIONS); self.meta_file_list.setSelectionMode(QAbstractItemView.ExtendedSelection); self.meta_file_list.itemSelectionChanged.connect(self._display_metadata_for_selection); self.meta_file_list.filesDropped.connect(self._add_meta_files_from_drop); self.meta_file_list.setToolTip("Drag & Drop MP3/FLAC files or folders containing them here"); left_layout.addWidget(QLabel("Select file(s) to view/edit metadata:")); left_layout.addWidget(self.meta_file_list); main_layout.addLayout(left_layout, 1); right_widget = QWidget(); right_layout = QVBoxLayout(right_widget); main_layout.addWidget(right_widget, 2); current_group = QGroupBox("Current Metadata (Selected File)"); self.current_meta_layout = QFormLayout(); current_group.setLayout(self.current_meta_layout); right_layout.addWidget(current_group); self.current_meta_labels = {}; self._setup_current_meta_display(); edit_group = QGroupBox("Edit Metadata (Applied to Selected Files)"); self.edit_meta_v_layout = QVBoxLayout(); edit_group.setLayout(self.edit_meta_v_layout); right_layout.addWidget(edit_group); self._setup_edit_meta_inputs(); self.run_edit_meta_button = QPushButton("Apply Metadata Changes to Selected Files"); self.run_edit_meta_button.setFixedHeight(35); self.run_edit_meta_button.clicked.connect(self._run_edit_metadata); right_layout.addWidget(self.run_edit_meta_button, alignment=Qt.AlignCenter); right_layout.addStretch()

    # --- Child Widget Setup ---
    def _setup_current_meta_display(self):
        # Clear previous labels
        for label in self.current_meta_labels.values():
            label.deleteLater()
        self.current_meta_labels = {}
        while self.current_meta_layout.count():
            child = self.current_meta_layout.takeAt(0)
            widget = child.widget()
            if widget: widget.deleteLater()

        # Add display rows based on EDITABLE_TAGS_LOWER order + cover art
        # Use the display names from the original EDITABLE_TAGS for labels
        from constants import EDITABLE_TAGS  # Import temporarily for display names
        for key, display_name in EDITABLE_TAGS.items():  # Iterate using original dict for order/casing
            l_key = key.lower()
            value_label = QLabel("-")
            value_label.setWordWrap(True)
            # Use display_name (potentially with format hint) for the label
            self.current_meta_layout.addRow(f"{display_name}:", value_label)
            self.current_meta_labels[l_key] = value_label  # Store using lower key

        # Add cover art info separately
        cover_label = QLabel("-")
        self.current_meta_layout.addRow("Cover Art:", cover_label)
        self.current_meta_labels['cover_art'] = cover_label

    def _setup_edit_meta_inputs(self):
        # Clear previous widgets
        for key in list(self.edit_meta_inputs.keys()):
            if key in self.edit_meta_inputs: self.edit_meta_inputs[key].deleteLater()
            if key in self.edit_meta_clear_buttons: self.edit_meta_clear_buttons[key].deleteLater()
            # Assuming label was part of a layout that gets cleared
            if key in self.edit_meta_inputs: del self.edit_meta_inputs[key]
            if key in self.edit_meta_clear_buttons: del self.edit_meta_clear_buttons[key]

        while self.edit_meta_v_layout.count():
            item = self.edit_meta_v_layout.takeAt(0)
            widget = item.widget()
            layout = item.layout()
            if widget: widget.deleteLater()
            if layout:  # Recursively delete layout contents
                while layout.count():
                    sub_item = layout.takeAt(0)
                    sub_widget = sub_item.widget()
                    if sub_widget: sub_widget.deleteLater()
                layout.deleteLater()

        self.edit_meta_inputs = {}  # Reset dictionaries
        self.edit_meta_clear_buttons = {}

        # Create input fields based on EDITABLE_TAGS_LOWER order
        from constants import EDITABLE_TAGS  # Import temporarily for display names
        for key, display_name in EDITABLE_TAGS.items():  # Iterate using original dict for order/casing
            l_key = key.lower()
            row_layout = QHBoxLayout()

            # Use display_name (potentially with format hint) for the label
            label = QLabel(f"{display_name}:")
            label.setMinimumWidth(100)  # Ensure alignment
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            input_widget = QLineEdit()
            input_widget.setPlaceholderText("Leave unchanged or enter new value")
            input_widget.setProperty("originalValue", "")
            input_widget.textChanged.connect(functools.partial(self._reset_clear_visual_state, input_widget))

            clear_button = QPushButton("Clear")
            clear_button.setFixedWidth(50)
            clear_button.setToolTip(f"Mark '{display_name}' to be cleared/removed")
            clear_button.clicked.connect(functools.partial(self._handle_clear_button_click, l_key))  # Pass lower key

            row_layout.addWidget(label)
            row_layout.addWidget(input_widget)
            row_layout.addWidget(clear_button)

            self.edit_meta_v_layout.addLayout(row_layout)
            self.edit_meta_inputs[l_key] = input_widget  # Store using lower key
            self.edit_meta_clear_buttons[l_key] = clear_button  # Store using lower key

        # Add a helper label at the bottom
        help_label = QLabel(
            "<small><i>Edit fields show common value for multiple selections. Enter new value to change.\nUse 'Clear' button to remove a tag. Empty fields are ignored if not cleared.</i></small>")
        help_label.setWordWrap(True)
        self.edit_meta_v_layout.addWidget(help_label)

    # --- Event Handlers / Slots ---

    # --- Generic File/Folder Selection ---
    def _select_folder(self, line_edit_widget: QLineEdit, title="Select Folder"):
        current_path = line_edit_widget.text() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, title, current_path)
        if folder:
            line_edit_widget.setText(folder)
            self.log_message(f"Folder selected: {folder}")
            return folder
        return None

    def _select_files(
        self, title="Select Files", start_dir=None, file_filter="All Files (*)"
    ):
        if start_dir is None:
            start_dir = os.path.expanduser("~")
        files, _ = QFileDialog.getOpenFileNames(self, title, start_dir, file_filter)
        return files

    def _add_files_to_list(
        self, list_widget: DropListWidget, file_paths: List[str], type_name: str
    ):
        """Generic method to add unique files to a QListWidget."""
        current_files = set(
            list_widget.item(i).data(Qt.UserRole) for i in range(list_widget.count())
        )
        added_count = 0
        skipped_count = 0
        for file_path in file_paths:
            if os.path.isfile(file_path):  # Ensure it's a file
                norm_path = os.path.normpath(file_path)
                if norm_path not in current_files:
                    # Display relative path if it's within a known base dir? Simpler: just basename
                    item = QListWidgetItem(os.path.basename(norm_path))
                    item.setData(Qt.UserRole, norm_path)  # Store normalized full path
                    item.setToolTip(norm_path)
                    list_widget.addItem(item)
                    current_files.add(norm_path)
                    added_count += 1
                else:
                    skipped_count += 1  # Duplicate
        log_msg = f"Added {added_count} new {type_name} file(s)."
        if skipped_count > 0:
            log_msg += f" Skipped {skipped_count} duplicates."
        if not file_paths and added_count == 0:
            log_msg = f"No valid {type_name} files found or provided."
        elif added_count == 0 and skipped_count > 0:
            log_msg = f"No new {type_name} files added (all were duplicates)."
        self.log_message(log_msg)

    # --- CUE Splitter Slots ---
    def _select_cue_files(self):
        files = self._select_files(
            title="Select CUE Files", file_filter="CUE Files (*.cue)"
        )
        if files:
            self._add_files_to_list(self.cue_list_widget, files, "CUE")

    def _select_cue_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder Containing CUE Files", os.path.expanduser("~")
        )
        if folder:
            self.log_message(f"Scanning folder for CUE files: {folder}")
            cue_files = _scan_folder_recursive(folder, CUE_EXTENSIONS)
            self._add_files_to_list(self.cue_list_widget, cue_files, "CUE")

    def _add_cue_files_from_drop(self, file_paths: List[str]):
        self.log_message(
            f"Processing {len(file_paths)} dropped/scanned item(s) for CUE tab."
        )
        self._add_files_to_list(self.cue_list_widget, file_paths, "CUE")

    def _clear_cue_list(self):
        self.cue_list_widget.clear()
        self.log_message("CUE file list cleared.")

    def _select_cue_output_dir(self):
        self._select_folder(
            self.cue_outputdir_edit, title="Select Output Directory for Split Files"
        )

    def _run_split_cue(self):
        cue_files = [
            self.cue_list_widget.item(i).data(Qt.UserRole)
            for i in range(self.cue_list_widget.count())
        ]
        if not cue_files:
            QMessageBox.warning(
                self, "Input Error", "Please add CUE files to the list first."
            )
            return

        # Check for required executables before starting thread
        if not find_ffmpeg() or not find_ffprobe():
            QMessageBox.critical(
                self,
                "Dependency Error",
                "FFmpeg and/or FFprobe not found in system PATH.\nCannot proceed with splitting.",
            )
            return

        output_dir = self.cue_outputdir_edit.text().strip() or None  # None if empty
        output_format = self.cue_format_combo.currentText()
        collection_key = self.cue_collection_combo.currentText()
        collection_arg = CUE_COLLECTION_OPTS.get(collection_key, "")
        overwrite_checked = self.cue_overwrite_checkbox.isChecked()
        overwrite_mode = CUE_OVERWRITE_MODES[overwrite_checked]

        self._run_operation(
            task_split_cue,
            self.run_split_cue_button,
            cue_files,
            output_dir,
            output_format,
            collection_arg,
            overwrite_mode,
        )

    # --- Cover Art Slots ---
    def _select_cover_folder(self):
        self._select_folder(self.cover_folder_edit)

    def _run_add_cover(self):
        folder = self.cover_folder_edit.text()
        cover_url = self.cover_url_edit.text().strip() or None
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(
                self, "Input Error", "Please select a valid target folder."
            )
            return
        if not find_ffmpeg():
            QMessageBox.critical(self, "Dependency Error", "FFmpeg not found in PATH.")
            return
        self._run_operation(
            task_add_covers, self.run_add_cover_button, folder, cover_url
        )

    # --- WAV Converter Slots ---
    def _select_wav_files(self):
        files = self._select_files(
            title="Select WAV Files", file_filter="WAV Files (*.wav)"
        )
        if files:
            self._add_files_to_list(self.wav_list_widget, files, "WAV")

    def _select_wav_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder Containing WAV Files", os.path.expanduser("~")
        )
        if folder:
            self.log_message(f"Scanning folder for WAV files: {folder}")
            wav_files = _scan_folder_recursive(folder, AUDIO_WAV_EXTENSIONS)
            self._add_files_to_list(self.wav_list_widget, wav_files, "WAV")

    def _add_wav_files_from_drop(self, file_paths: List[str]):
        self.log_message(
            f"Processing {len(file_paths)} dropped/scanned item(s) for WAV tab."
        )
        self._add_files_to_list(self.wav_list_widget, file_paths, "WAV")

    def _clear_wav_list(self):
        self.wav_list_widget.clear()
        self.log_message("WAV file list cleared.")

    def _run_convert_wav(self):
        wav_files = [
            self.wav_list_widget.item(i).data(Qt.UserRole)
            for i in range(self.wav_list_widget.count())
        ]
        if not wav_files:
            QMessageBox.warning(
                self, "Input Error", "Please add WAV files to the list first."
            )
            return
        if not find_ffmpeg():
            QMessageBox.critical(self, "Dependency Error", "FFmpeg not found in PATH.")
            return
        mode = "mp3_meta" if self.radio_meta_flac.isChecked() else "simple"
        self._run_operation(
            task_convert_wav, self.run_convert_wav_button, wav_files, mode
        )

    # --- Metadata Editor Slots ---
    def _select_meta_folder(self):
        folder = self._select_folder(self.meta_folder_edit)
        folder and self._load_meta_files()

    def _load_meta_files_from_drop(self, folder_path):
        self.log_message(f"Folder selected via drop: {folder_path}")
        self.meta_folder_edit.setText(folder_path)
        self._load_meta_files()

    def _load_meta_files(self):
        folder = self.meta_folder_edit.text()
        if not folder or not os.path.isdir(folder):
            self.log_message("Please select a valid folder first.", error=True)
            return
        self.meta_file_list.clear()
        self.log_message(f"Scanning for MP3/FLAC files in: {folder}")
        audio_files = _scan_folder_recursive(folder, AUDIO_META_EXTENSIONS)
        self._add_files_to_list(self.meta_file_list, audio_files, "MP3/FLAC")
        self._clear_metadata_display_and_inputs()  # Clear display after loading list

    def _add_meta_files_from_drop(self, file_paths: List[str]):
        self.log_message(
            f"Processing {len(file_paths)} dropped/scanned item(s) for Metadata tab."
        )
        self._add_files_to_list(self.meta_file_list, file_paths, "MP3/FLAC")
        # If folder input is empty, try to set it from first dropped file's dir
        if not self.meta_folder_edit.text() and file_paths:
            first_dir = os.path.dirname(file_paths[0])
            if os.path.isdir(first_dir):
                self.meta_folder_edit.setText(first_dir)

    def _clear_metadata_display_and_inputs(self):
        # (Unchanged from previous version)
        for key, label in self.current_meta_labels.items():
            label.setText("-")
            label.setToolTip("")
        for key, input_widget in self.edit_meta_inputs.items():
            input_widget.blockSignals(True)
            input_widget.clear()
            input_widget.setProperty("originalValue", "")
            input_widget.setProperty("valueToSet", None)
            input_widget.setPlaceholderText("Leave unchanged or enter new value")
            font = input_widget.font()
            font.setItalic(False)
            input_widget.setFont(font)
            input_widget.setPalette(QApplication.style().standardPalette())
            input_widget.blockSignals(False)
        self.current_metadata_cache = {}

    def _display_metadata_for_selection(self):
        # (Unchanged from previous version)
        selected_items = self.meta_file_list.selectedItems()
        self._clear_metadata_display_and_inputs()
        if not selected_items:
            return
        all_metadata = []
        has_error = False
        common_values = {}
        is_single_selection = len(selected_items) == 1
        log_prefix = (
            "Loading metadata..."
            if is_single_selection
            else f"Loading common metadata for {len(selected_items)} files..."
        )
        self.log_message(log_prefix)
        for i, item in enumerate(selected_items):
            file_path = item.data(Qt.UserRole)
            # if i == 0 and is_single_selection: self.log_message(f"  - {os.path.basename(file_path)}") # Verbose
            from worker_tasks import get_metadata  # Import locally for clarity

            meta = get_metadata(file_path, lambda msg: None)
            if meta.get("error"):
                self.log_message(
                    f"Error reading metadata for {os.path.basename(file_path)}: {meta['error']}",
                    error=True,
                )
                has_error = True
            all_metadata.append(meta)
        if has_error and not all_metadata:
            self.current_meta_labels["title"].setText(
                "<font color='red'>Error reading file(s)</font>"
            )
            return
        if all_metadata:
            first_meta = all_metadata[0]
            for key in list(EDITABLE_TAGS_LOWER.keys()) + ["title", "cover_art"]:
                first_value = first_meta.get(key, "")
                all_same = all(
                    meta.get(key, "") == first_value for meta in all_metadata[1:]
                )
                common_values[key] = (
                    first_value if all_same else MULTIPLE_VALUES_PLACEHOLDER
                )
        if has_error:
            self.current_meta_labels["title"].setText(
                "<font color='red'>Error reading some files</font>"
            )
        for key, label in self.current_meta_labels.items():
            value = common_values.get(key, "")
            is_multiple = value == MULTIPLE_VALUES_PLACEHOLDER
            display_value = (
                MULTIPLE_VALUES_DISPLAY if is_multiple else (value if value else "-")
            )
            tooltip = (
                "Different values across selected files" if is_multiple else str(value)
            )
            label.setText(str(display_value))
            label.setToolTip(tooltip)
        for key, input_widget in self.edit_meta_inputs.items():
            value = common_values.get(key, "")
            input_widget.blockSignals(True)
            input_widget.setProperty("valueToSet", None)
            self._reset_clear_visual_state(input_widget)
            if value == MULTIPLE_VALUES_PLACEHOLDER:
                input_widget.clear()
                input_widget.setPlaceholderText(
                    "<Multiple Values - Enter value to apply>"
                )
                input_widget.setProperty("originalValue", MULTIPLE_VALUES_PLACEHOLDER)
            else:
                input_widget.setText(value)
                input_widget.setPlaceholderText("Leave unchanged or enter new value")
                input_widget.setProperty("originalValue", value)
            input_widget.blockSignals(False)
        self.current_metadata_cache = (
            all_metadata[0] if is_single_selection and not has_error else {}
        )

    def _handle_clear_button_click(self, key: str):
        # (Unchanged from previous version)
        if key in self.edit_meta_inputs:
            input_widget = self.edit_meta_inputs[key]
            input_widget.setProperty("valueToSet", CLEAR_TAG_PLACEHOLDER)
            input_widget.setText(CLEAR_TAG_DISPLAY_TEXT)
            font = input_widget.font()
            font.setItalic(True)
            input_widget.setFont(font)
            palette = input_widget.palette()
            palette.setColor(QPalette.Text, QColor("gray"))
            input_widget.setPalette(palette)
            self.log_message(
                f"Marked '{EDITABLE_TAGS_LOWER.get(key, key)}' for clearing."
            )

    def _reset_clear_visual_state(self, input_widget: QLineEdit):
        # (Unchanged from previous version)
        font = input_widget.font()
        if font.italic():
            font.setItalic(False)
            input_widget.setFont(font)
            input_widget.setPalette(QApplication.style().standardPalette())
            input_widget.setPlaceholderText("Leave unchanged or enter new value")
        if input_widget.property("valueToSet") == CLEAR_TAG_PLACEHOLDER:
            input_widget.setProperty("valueToSet", None)

    def _run_edit_metadata(self):
        # (Unchanged from previous version)
        selected_items = self.meta_file_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "Input Error", "Please select one or more audio files to modify."
            )
            return
        file_paths = [item.data(Qt.UserRole) for item in selected_items]
        metadata_updates = {}
        changes_list_for_confirm = []
        for key, input_widget in self.edit_meta_inputs.items():
            current_text = input_widget.text()
            clear_button_pressed = (
                input_widget.property("valueToSet") == CLEAR_TAG_PLACEHOLDER
            )
            original_value = input_widget.property("originalValue")
            if clear_button_pressed:
                metadata_updates[key] = ""
                changes_list_for_confirm.append(
                    f"- {EDITABLE_TAGS_LOWER.get(key, key)}: [Clear Tag]"
                )
            elif current_text != CLEAR_TAG_DISPLAY_TEXT:
                is_changed = (
                    original_value == MULTIPLE_VALUES_PLACEHOLDER
                    and bool(current_text.strip())
                ) or (
                    original_value != MULTIPLE_VALUES_PLACEHOLDER
                    and current_text != original_value
                )
                if is_changed:
                    new_value = current_text.strip()
                    metadata_updates[key] = new_value
                    changes_list_for_confirm.append(
                        f"- {EDITABLE_TAGS_LOWER.get(key, key)}: Set to '{new_value}'"
                    )
        if not metadata_updates:
            QMessageBox.information(self, "No Changes", "No changes detected.")
            return
        confirm_msg = (
            f"Apply the following changes to {len(file_paths)} selected file(s)?\n\n"
            + "\n".join(changes_list_for_confirm)
        )
        reply = QMessageBox.question(
            self,
            "Confirm Metadata Update",
            confirm_msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._run_operation(
                task_edit_metadata,
                self.run_edit_meta_button,
                file_paths,
                metadata_updates,
            )
        else:
            self.log_message("Metadata update cancelled by user.")

    def _run_fetch_vgmdb(self):
        input_text = self.vgmdb_id_edit.text().strip()
        if not input_text:
            QMessageBox.warning(self, "Input Error", "Please enter a VGMdb Album ID or URL.")
            return

        # Extract ID from URL or use directly
        album_id = None
        url_match = re.search(r'vgmdb\.net/album/(\d+)', input_text)
        id_match = re.match(r'^(\d+)$', input_text)

        if url_match:
            album_id = url_match.group(1)
        elif id_match:
            album_id = id_match.group(1)

        if not album_id:
            QMessageBox.warning(self, "Input Error", "Could not parse a valid VGMdb Album ID from the input.")
            return

        # Clear previous results and disable apply buttons
        self.vgmdb_display_area.clear()
        self.fetched_vgmdb_data = None
        self.apply_vgmdb_meta_button.setEnabled(False)
        self.apply_vgmdb_cover_button.setEnabled(False)

        # Run the fetch task
        self._run_operation(task_fetch_vgmdb, self.fetch_vgmdb_button, album_id)

    def _apply_vgmdb_to_metadata(self):
        if not self.fetched_vgmdb_data:
            self.log_message("No VGMdb data fetched to apply.", error=True)
            QMessageBox.warning(self, "No Data", "Fetch VGMdb data first before applying.")
            return

        self.log_message("Applying fetched VGMdb data to Metadata tab inputs...")

        data = self.fetched_vgmdb_data
        pref_lang = ['ja', 'ja-Latn', 'en']  # Japanese preferred

        # Map VGMdb data to our metadata input fields (keys are lowercase)
        mappings = {}

        # Title & Album (Prefer Japanese)
        mappings['title'] = _get_preferred_lang(data.get('titles', {}), pref_lang)
        mappings['album'] = mappings['title']  # Often the same for VGMdb

        # Artist & Album Artist (Use first Publisher or Label - adapt as needed)
        # This is heuristic, VGMdb doesn't have a single 'artist' field typically
        orgs = data.get('organizations', {})
        primary_org_list = orgs.get('publishers') or orgs.get('labels') or orgs.get('manufacturers')
        if primary_org_list:
            first_org_names = primary_org_list[0]  # Take the first organization listed
            artist_name = _get_preferred_lang(first_org_names, pref_lang)
            mappings['artist'] = artist_name
            mappings['albumartist'] = artist_name  # Set both
        else:
            mappings['artist'] = None  # Or fetch from Performer if needed?
            mappings['albumartist'] = None

        # Date / Year
        release_date_raw = data.get('release_date')
        release_date_parsed = _parse_date(release_date_raw)  # YYYYMMDD or YYYY or original
        if release_date_parsed:
            if len(release_date_parsed) == 8:  # YYYYMMDD
                mappings['date'] = f"{release_date_parsed[:4]}-{release_date_parsed[4:6]}-{release_date_parsed[6:]}"
                mappings['year'] = release_date_parsed[:4]
            elif len(release_date_parsed) == 4:  # YYYY only
                mappings['year'] = release_date_parsed
                mappings['date'] = None  # Clear full date if only year found
            else:  # Keep original if unparsed
                mappings['date'] = release_date_parsed
                mappings['year'] = None  # Cannot extract year reliably
        else:
            mappings['date'] = None
            mappings['year'] = None

        # Genre (from Classification)
        mappings['genre'] = data.get('classification')

        # Comment (Add Catalog number?)
        catalog = data.get('catalog_number')
        if catalog:
            mappings['comment'] = f"Catalog: {catalog}"
        else:
            mappings['comment'] = None

        # --- Apply to input fields ---
        applied_count = 0
        for key, value in mappings.items():
            if key in self.edit_meta_inputs and value is not None:
                input_widget = self.edit_meta_inputs[key]
                # Prevent marking as "cleared" visually if we set an empty string
                input_widget.blockSignals(True)
                self._reset_clear_visual_state(input_widget)  # Reset visuals first
                input_widget.setText(str(value))  # Set the actual value
                # We don't set originalValue here, let user confirm the changes
                input_widget.blockSignals(False)
                self.log_message(f"  - Set '{key}' input to: '{value}'")
                applied_count += 1

        if applied_count > 0:
            self.log_message(
                f"Applied {applied_count} fields. Review in Metadata tab and click 'Apply Metadata Changes'.")
            # Switch to the Metadata Editor tab
            for i in range(self.tabs.count()):
                if self.tabs.widget(i) == self.tab_metadata_editor:
                    self.tabs.setCurrentIndex(i)
                    break
        else:
            self.log_message("No relevant metadata found or fields available to apply.")

    def _apply_vgmdb_to_cover_url(self):
        if not self.fetched_vgmdb_data or not self.fetched_vgmdb_data.get('cover_image'):
            self.log_message("No VGMdb cover URL fetched to apply.", error=True)
            QMessageBox.warning(self, "No Data", "Fetch VGMdb data with a cover image first.")
            return

        cover_url = self.fetched_vgmdb_data['cover_image']
        self.cover_url_edit.setText(cover_url)
        self.log_message(f"Set Cover URL input field to: {cover_url}")

        # Switch to the Embed Cover tab
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.tab_add_cover:
                self.tabs.setCurrentIndex(i)
                break

    # --- Worker Thread Management ---
    def _run_operation(self, task_function, button_to_disable: QPushButton, *args):
        """Generic method to run a task in a worker thread."""
        task_name = task_function.__name__
        self.log_message(f"Starting task: {task_name}...")
        button_to_disable.setEnabled(False)

        self.thread = QThread()
        self.worker = Worker(task_function, *args, logger_func=self.log_message)
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self.log_message)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        # Pass task_name to the finished handler
        self.worker.finished.connect(
            lambda success, result: self._operation_finished(success, result, button_to_disable, task_name)
        )

        self.thread.start()

    def _operation_finished(self, success: bool, result: Any, button_to_enable: QPushButton, task_name: str):
        """Handles the completion of a worker thread task."""
        final_log = f"Task '{task_name}' finished." + ("" if success else " (with errors/warnings)")
        self.log_message(final_log)

        details_message = ""  # The message string to show in the dialog/log
        show_dialog = True

        # --- Handle VGMdb Fetch Result ---
        if task_name == "task_fetch_vgmdb":
            self.fetched_vgmdb_data = None  # Clear previous
            vgmdb_display_text = ""

            if isinstance(result, dict):
                # Display the raw data regardless of success/failure if it's a dict
                vgmdb_display_text = json.dumps(result, indent=2, ensure_ascii=False)
                self.vgmdb_display_area.setPlainText(vgmdb_display_text)

                if success:  # Success means no network or critical parse error
                    self.fetched_vgmdb_data = result  # Store successful data
                    self.apply_vgmdb_meta_button.setEnabled(True)
                    self.apply_vgmdb_cover_button.setEnabled(bool(result.get('cover_image')))
                    details_message = "Data fetched successfully. See display area."
                    show_dialog = False  # Don't show popup if data is displayed
                elif result.get("_error"):  # Failure, but partial data exists
                    # Extract the error string from the dict for the message
                    details_message = f"Fetching failed: {result['_error']}\nPartial data (if any) shown in display area."
                else:  # Dict received, but success is False and no _error key? Unexpected.
                    details_message = "Fetching failed with partial data but no specific error message. See display area and logs."

            elif isinstance(result, str):  # Failure, and result is just an error string
                details_message = str(result)
                vgmdb_display_text = f"Error:\n{details_message}"
                self.vgmdb_display_area.setPlainText(vgmdb_display_text)
            else:  # Unexpected result type
                details_message = f"Received unexpected result type: {type(result)}"
                vgmdb_display_text = f"Error:\n{details_message}"
                self.vgmdb_display_area.setPlainText(vgmdb_display_text)

            # Log the details derived above
            self.log_message(f"Details: {details_message}")

        # --- Handle other task results (expect string messages) ---
        else:
            # Assume result is a string for other tasks
            details_message = str(result)
            self.log_message(f"Details:\n-------\n{details_message}\n-------")

        # --- Show Dialog (if appropriate) ---
        if show_dialog:
            msg_box = QMessageBox.information if success else QMessageBox.warning
            title = "Operation Complete" if success else "Operation Finished with Issues"
            # Use the derived details_message string here
            msg_box(self, title, f"{final_log}\n\n{details_message}")

        button_to_enable.setEnabled(True)

        # Refresh relevant view if necessary
        if task_name == "task_edit_metadata": self._display_metadata_for_selection()

    def closeEvent(self, event):
        if hasattr(self, 'thread') and self.thread is not None and self.thread.isRunning():
            reply = QMessageBox.question(self, 'Confirm Exit', "Operation in progress. Exit anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No);
            if reply == QMessageBox.Yes: self.thread.quit(); self.thread.wait(500); event.accept()
            else: event.ignore()
        else: event.accept()


# --- Main Execution ---
if __name__ == "__main__":
    # Set App ID for Windows Taskbar Icon (optional but good practice)
    if os.name == "nt":
        import ctypes

        myappid = "bai0012.audiotoolkitgui.1.0"  # arbitrary string
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    app = QApplication(sys.argv)

    # --- Set Font ---
    font = QFont("Calibri", 10)
    app.setFont(font)

    # --- Set Icon (Optional) ---
    # Create a simple icon file 'icon.png' or use a path
    # icon_path = "icon.png"
    # if os.path.exists(icon_path):
    #    app.setWindowIcon(QIcon(icon_path))

    app.setStyle("Fusion")
    main_window = AudioToolApp()
    main_window.show()
    sys.exit(app.exec_())
