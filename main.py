# audio_toolkit/main_app.py

import functools
import os
import sys
from typing import List

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
    QCheckBox,  # Added ComboBox, CheckBox
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
    task_split_cue,
)


class AudioToolApp(QMainWindow):
    log_signal = pyqtSignal(str)  # For messages from non-main threads if needed

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Toolkit GUI")
        self.setGeometry(100, 100, 900, 750)  # Slightly wider/taller for new tab

        self.ffmpeg_path = find_ffmpeg()  # Check early
        self.current_metadata_cache = {}
        self.edit_meta_clear_buttons = {}
        self.edit_meta_inputs = {}

        # --- Central Widget and Main Layout ---
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # --- Tabs ---
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        # --- Log Area ---
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFixedHeight(170)
        self.log_area.setFontFamily(
            "Courier New"
        )  # Use Courier New for better cross-platform fixed width
        self.main_layout.addWidget(QLabel("Log Output:"))
        self.main_layout.addWidget(self.log_area)

        # --- Setup Tabs ---
        self._setup_cue_splitter_tab()  # Add new tab first for visibility
        self._setup_add_cover_tab()
        self._setup_wav_converter_tab()
        self._setup_metadata_editor_tab()

        # --- Final Checks ---
        if not self.ffmpeg_path:
            self.log_message(
                "[Warning] FFmpeg/FFprobe not found in PATH. Some functions may fail.",
                error=True,
            )
            # Warning dialog shown when function requiring it is called

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
        # (Unchanged from previous version, ensure imports correct)
        self.tab_add_cover = QWidget()
        self.tabs.addTab(self.tab_add_cover, "Embed Cover (FLAC)")
        layout = QVBoxLayout(self.tab_add_cover)
        layout.setSpacing(10)
        folder_layout = QHBoxLayout()
        folder_label = QLabel("Target Folder (contains FLACs):")
        self.cover_folder_edit = DropLineEdit()
        self.cover_folder_edit.pathDropped.connect(
            lambda path: self.log_message(f"Folder selected via drop: {path}")
        )
        browse_folder_button = QPushButton("Browse...")
        browse_folder_button.clicked.connect(self._select_cover_folder)
        folder_layout.addWidget(folder_label)
        folder_layout.addWidget(self.cover_folder_edit)
        folder_layout.addWidget(browse_folder_button)
        layout.addLayout(folder_layout)
        url_layout = QHBoxLayout()
        url_label = QLabel("Cover Image URL (Optional):")
        self.cover_url_edit = QLineEdit()
        self.cover_url_edit.setPlaceholderText(
            "Leave blank to use local 'cover.png/jpg/...'"
        )
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.cover_url_edit)
        layout.addLayout(url_layout)
        layout.addWidget(
            QLabel(
                "If URL is provided, it will be downloaded. If download fails or no URL is given,\nthe tool searches for 'cover.png', 'cover.jpg', etc. in each FLAC's directory."
            )
        )
        self.run_add_cover_button = QPushButton("Start Embedding Covers")
        self.run_add_cover_button.setFixedHeight(35)
        self.run_add_cover_button.clicked.connect(self._run_add_cover)
        layout.addWidget(self.run_add_cover_button, alignment=Qt.AlignCenter)
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
        # (Unchanged from previous version, ensure imports correct)
        self.tab_metadata_editor = QWidget()
        self.tabs.addTab(self.tab_metadata_editor, "Metadata Editor (MP3/FLAC)")
        main_layout = QHBoxLayout(self.tab_metadata_editor)
        left_layout = QVBoxLayout()
        folder_layout = QHBoxLayout()
        folder_label = QLabel("Audio Folder:")
        self.meta_folder_edit = DropLineEdit()
        self.meta_folder_edit.pathDropped.connect(self._load_meta_files_from_drop)
        browse_meta_folder_button = QPushButton("Browse...")
        browse_meta_folder_button.clicked.connect(self._select_meta_folder)
        folder_layout.addWidget(folder_label)
        folder_layout.addWidget(self.meta_folder_edit)
        folder_layout.addWidget(browse_meta_folder_button)
        left_layout.addLayout(folder_layout)
        refresh_button = QPushButton("Load/Refresh Files from Folder")
        refresh_button.clicked.connect(self._load_meta_files)
        left_layout.addWidget(refresh_button)
        self.meta_file_list = DropListWidget(accepted_extensions=AUDIO_META_EXTENSIONS)
        self.meta_file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.meta_file_list.itemSelectionChanged.connect(
            self._display_metadata_for_selection
        )
        self.meta_file_list.filesDropped.connect(self._add_meta_files_from_drop)
        self.meta_file_list.setToolTip(
            "Drag & Drop MP3/FLAC files or folders containing them here"
        )
        left_layout.addWidget(QLabel("Select file(s) to view/edit metadata:"))
        left_layout.addWidget(self.meta_file_list)
        main_layout.addLayout(left_layout, 1)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        main_layout.addWidget(right_widget, 2)
        current_group = QGroupBox("Current Metadata (Selected File)")
        self.current_meta_layout = QFormLayout()
        current_group.setLayout(self.current_meta_layout)
        right_layout.addWidget(current_group)
        self.current_meta_labels = {}
        self._setup_current_meta_display()
        edit_group = QGroupBox("Edit Metadata (Applied to Selected Files)")
        self.edit_meta_v_layout = QVBoxLayout()
        edit_group.setLayout(self.edit_meta_v_layout)
        right_layout.addWidget(edit_group)
        self._setup_edit_meta_inputs()
        self.run_edit_meta_button = QPushButton(
            "Apply Metadata Changes to Selected Files"
        )
        self.run_edit_meta_button.setFixedHeight(35)
        self.run_edit_meta_button.clicked.connect(self._run_edit_metadata)
        right_layout.addWidget(self.run_edit_meta_button, alignment=Qt.AlignCenter)
        right_layout.addStretch()

    # --- Child Widget Setup ---
    def _setup_current_meta_display(self):
        # (Unchanged from previous version, ensure imports correct)
        for label in self.current_meta_labels.values():
            label.deleteLater()
        self.current_meta_labels = {}
        while self.current_meta_layout.count():
            child = self.current_meta_layout.takeAt(0)
            widget = child.widget()
            widget and widget.deleteLater()
        title_label = QLabel("-")
        title_label.setWordWrap(True)
        self.current_meta_layout.addRow("Title:", title_label)
        self.current_meta_labels["title"] = title_label
        for key, display_name in EDITABLE_TAGS_LOWER.items():
            value_label = QLabel("-")
            value_label.setWordWrap(True)
            self.current_meta_layout.addRow(f"{display_name}:", value_label)
            self.current_meta_labels[key] = value_label
        cover_label = QLabel("-")
        self.current_meta_layout.addRow("Cover Art:", cover_label)
        self.current_meta_labels["cover_art"] = cover_label

    def _setup_edit_meta_inputs(self):
        # (Unchanged from previous version, ensure imports correct)
        for key in list(self.edit_meta_inputs.keys()):
            self.edit_meta_inputs[key].deleteLater()
            self.edit_meta_clear_buttons[key].deleteLater()
            del self.edit_meta_inputs[key]
            del self.edit_meta_clear_buttons[key]
        while self.edit_meta_v_layout.count():
            item = self.edit_meta_v_layout.takeAt(0)
            widget = item.widget()
            layout = item.layout()
            if widget:
                widget.deleteLater()
            if layout:
                while layout.count():
                    sub_item = layout.takeAt(0)
                    sub_widget = sub_item.widget()
                    sub_widget and sub_widget.deleteLater()
                    layout.deleteLater()
        for key, display_name in EDITABLE_TAGS_LOWER.items():
            row_layout = QHBoxLayout()
            label = QLabel(f"{display_name}:")
            label.setMinimumWidth(100)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            input_widget = QLineEdit()
            input_widget.setPlaceholderText("Leave unchanged or enter new value")
            input_widget.setProperty("originalValue", "")
            input_widget.textChanged.connect(
                functools.partial(self._reset_clear_visual_state, input_widget)
            )
            clear_button = QPushButton("Clear")
            clear_button.setFixedWidth(50)
            clear_button.setToolTip(f"Mark '{display_name}' to be cleared/removed")
            clear_button.clicked.connect(
                functools.partial(self._handle_clear_button_click, key)
            )
            row_layout.addWidget(label)
            row_layout.addWidget(input_widget)
            row_layout.addWidget(clear_button)
            self.edit_meta_v_layout.addLayout(row_layout)
            self.edit_meta_inputs[key] = input_widget
            self.edit_meta_clear_buttons[key] = clear_button
        help_label = QLabel(
            "<small><i>Edit fields show common value for multiple selections. Enter new value to change.\nUse 'Clear' button to remove a tag. Empty fields are ignored if not cleared.</i></small>"
        )
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

    # --- Worker Thread Management ---
    def _run_operation(self, task_function, button_to_disable: QPushButton, *args):
        """Generic method to run a task in a worker thread."""
        self.log_message(f"Starting task: {task_function.__name__}...")
        button_to_disable.setEnabled(False)

        self.thread = QThread()
        # Pass the logger function to the worker
        self.worker = Worker(task_function, *args, logger_func=self.log_message)
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self.log_message)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.finished.connect(
            lambda success, msg: self._operation_finished(
                success, msg, button_to_disable, task_function.__name__
            )
        )

        self.thread.start()

    def _operation_finished(
        self, success: bool, message: str, button_to_enable: QPushButton, task_name: str
    ):
        """Handles the completion of a worker thread task."""
        final_log = f"Task '{task_name}' finished." + (
            "" if success else " (with errors)"
        )
        self.log_message(final_log)
        self.log_message(f"Details:\n-------\n{message}\n-------")

        msg_box = QMessageBox.information if success else QMessageBox.warning
        title = "Operation Complete" if success else "Operation Finished with Issues"
        msg_box(self, title, f"{final_log}\n\nDetails:\n{message}")

        button_to_enable.setEnabled(True)
        # Refresh relevant view if necessary
        if task_name == "task_edit_metadata":
            self._display_metadata_for_selection()
        # Could add refresh for CUE list if needed, but cleanup happens in task

    def closeEvent(self, event):
        # Check if the thread attribute exists and is running
        if (
            hasattr(self, "thread")
            and self.thread is not None
            and self.thread.isRunning()
        ):
            reply = QMessageBox.question(
                self,
                "Confirm Exit",
                "An operation is in progress. Are you sure you want to exit?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.log_message("Attempting to stop running task...")
                # Ask thread to quit, but don't wait indefinitely as it might hang
                self.thread.quit()
                self.thread.wait(500)  # Wait briefly
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


# --- Main Execution ---
if __name__ == "__main__":
    # Set App ID for Windows Taskbar Icon (optional but good practice)
    if os.name == "nt":
        import ctypes

        myappid = "mycompany.audiotoolkit.1.0"  # arbitrary string
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
