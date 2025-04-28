# audio_toolkit/worker_tasks.py

import os
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import mutagen
import requests
from PyQt5.QtCore import QObject, pyqtSignal

# Import constants
from constants import (
    IMAGE_EXTENSIONS,
    VALID_MIME_TYPES,
    EDITABLE_TAGS_LOWER,
    FFMPEG_LOG_LEVEL,
    FFCS_PROG_LOG_LEVEL,
)

# Import helpers from utils
from utils import (  # Ensure safe_delete is imported
    find_ffmpeg,
    find_ffprobe,
    run_ffmpeg_command,
    safe_delete,
)


# --- Worker Infrastructure ---
class Worker(QObject):
    """Generic worker thread for running tasks."""

    finished = pyqtSignal(bool, str)  # Signal(success_bool, message_str)
    progress = pyqtSignal(str)  # Signal(progress_message_str)

    def __init__(self, task_func, *args, **kwargs):
        super().__init__()
        self.task_func = task_func
        self.args = args
        self.kwargs = kwargs
        # Store logger reference from main app if passed
        self.logger = kwargs.pop(
            "logger_func", print
        )  # Default to print if no logger passed

    def run(self):
        try:
            # Pass the progress signal emitter (which uses the logger) to the task
            result, message = self.task_func(
                self.progress.emit, *self.args, **self.kwargs
            )
            self.finished.emit(result, message)
        except ImportError as e:
            # Specific handling for missing libraries like ffcuesplitter
            self.logger(f"[!] Worker thread error: Required library missing - {e}")
            self.finished.emit(
                False,
                f"Worker thread error: Required library missing.\nPlease ensure '{e.name}' is installed (`pip install {e.name}`).",
            )
        except Exception as e:
            self.logger(
                f"[!] Worker thread error: {type(e).__name__} - {str(e)}"
            )  # Log exception type and msg
            # Optionally include traceback for debugging (might be too verbose for user)
            # import traceback
            # self.logger(traceback.format_exc())
            self.finished.emit(
                False, f"Worker thread error: {type(e).__name__} - {str(e)}"
            )


# --- Task Functions ---

# (Existing task_add_covers, task_convert_wav, task_edit_metadata need slight mods
#  to use the logger correctly if they call helper functions that need it,
#  and import helpers/constants from new locations)


# --- Metadata Helpers (Pulled from old main file) ---
def get_metadata(file_path: str, logger) -> Dict[str, Any]:
    # (Code from previous version, ensure imports are correct)
    metadata = {}
    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is None:
            return {"error": "Unsupported format or read error"}
        for key in EDITABLE_TAGS_LOWER:
            metadata[key] = "; ".join(audio[key]) if key in audio else ""
        metadata["title"] = "; ".join(audio["title"]) if "title" in audio else ""
        cover_info = "No"
        try:
            audio_raw = mutagen.File(file_path)
            if isinstance(audio_raw, mutagen.flac.FLAC) and audio_raw.pictures:
                cover_info = f"Yes ({len(audio_raw.pictures)} image(s))"
            elif isinstance(audio_raw, mutagen.mp3.MP3) and any(
                tag.startswith("APIC") for tag in audio_raw.tags.keys()
            ):
                cover_info = "Yes"
            metadata["cover_art"] = cover_info
        except Exception:
            metadata["cover_art"] = "N/A"
    except mutagen.id3.ID3NoHeaderError:
        logger(f"  Warning: No ID3 header found in MP3: {os.path.basename(file_path)}")
        metadata = {k: "" for k in EDITABLE_TAGS_LOWER}
        metadata["title"] = ""
        metadata["cover_art"] = "N/A"
        metadata["error"] = "No ID3 Header"
    except Exception as e:
        logger(f"  Error reading metadata for {os.path.basename(file_path)}: {e}")
        metadata["error"] = str(e)
    return metadata


def update_metadata(file_path: str, metadata_updates: Dict[str, str], logger) -> bool:
    # (Code from previous version, ensure imports/constants are correct)
    logger(f"Updating metadata for: {os.path.basename(file_path)}")
    try:
        audio = mutagen.File(file_path)  # Use non-easy for tag check/delete potentially
        if audio is None:
            logger(f"  [!] Cannot load file: {os.path.basename(file_path)}")
            return False
        if audio.tags is None:
            try:
                logger(f"  [*] Adding tags structure...")
                audio.add_tags()
                audio.save()
                audio = mutagen.File(file_path)
            except Exception as e_add:
                logger(f"  [!] Error adding tags structure: {e_add}")
                return False
            if audio.tags is None:
                logger(f"  [!] Failed to add tags structure.")
                return False

        audio_easy = mutagen.File(file_path, easy=True)  # Use easy for setting values
        if audio_easy is None or audio_easy.tags is None:
            logger(f"  [!] Could not reopen file in easy mode.")
            return False

        needs_save = False
        updated_count = 0
        deleted_count = 0
        for key, value in metadata_updates.items():
            l_key = key.lower()
            if l_key not in EDITABLE_TAGS_LOWER:
                continue
            current_value_str = (
                "; ".join(audio_easy[l_key]) if l_key in audio_easy else ""
            )
            if value == "":  # Delete request
                if l_key in audio_easy:
                    try:
                        del audio_easy[l_key]
                        logger(
                            f"      - Deleted tag {EDITABLE_TAGS_LOWER.get(l_key, l_key)}"
                        )
                        needs_save = True
                        deleted_count += 1
                    except Exception as e_del:
                        logger(f"      - Failed to delete tag {l_key}: {e_del}")
            elif value != current_value_str:  # Update request
                try:
                    new_value_list = [v.strip() for v in value.split(";") if v.strip()]
                    if new_value_list:
                        audio_easy[l_key] = new_value_list
                        logger(
                            f"      - Set {EDITABLE_TAGS_LOWER.get(l_key, l_key)} = '{value}'"
                        )
                        needs_save = True
                        updated_count += 1
                    elif l_key in audio_easy:
                        del audio_easy[l_key]
                        logger(
                            f"      - Deleted tag {EDITABLE_TAGS_LOWER.get(l_key, l_key)} (new value empty)"
                        )
                        needs_save = True
                        deleted_count += 1
                except Exception as e_set:
                    logger(f"  [!] Error setting tag '{l_key}': {e_set}")

        if needs_save:
            try:
                audio_easy.save()
                logger(
                    f"      - Saved changes. ({updated_count} updated, {deleted_count} deleted)"
                )
                return True
            except Exception as e_save:
                logger(
                    f"  [!] Error saving file {os.path.basename(file_path)}: {e_save}"
                )
                return False
        else:
            logger("      - No changes needed.")
            return True
    except Exception as e:
        logger(f"  [!] Error processing file {os.path.basename(file_path)}: {e}")
        return False


# --- Cover Art Helpers (Pulled from old main file) ---
def download_cover(url: str, save_dir: str, logger, timeout: int = 10) -> Optional[str]:
    # (Code from previous version, ensure imports are correct)
    logger(f"Attempting to download cover from: {url}")
    try:
        response = requests.get(
            url,
            stream=True,
            timeout=timeout,
            headers={"User-Agent": "AudioToolGUI/1.0"},
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
        # logger(f"  Content-Type: {content_type}") # Verbose
        if content_type not in VALID_MIME_TYPES:
            parsed_url = urlparse(url)
            path_ext = os.path.splitext(parsed_url.path)[1].lower().strip(".")
            if f"image/{path_ext}" in VALID_MIME_TYPES:
                ext = path_ext
                logger(f"  Guessed extension from URL: {ext}")
            else:
                raise ValueError(f"Unsupported image type: {content_type} / {path_ext}")
        else:
            ext = VALID_MIME_TYPES[content_type]
        temp_cover_path = os.path.join(save_dir, f"cover_download_temp.{ext}")
        with open(temp_cover_path, "wb") as f:
            for chunk in response.iter_content(8192):
                f.write(chunk)
        logger(
            f"  Successfully downloaded cover to: {os.path.basename(temp_cover_path)}"
        )
        return temp_cover_path
    except requests.exceptions.Timeout:
        logger(f"  [!] Download timed out: {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger(f"  [!] Download failed: {url} -> {str(e)}")
        return None
    except ValueError as e:
        logger(f"  [!] Download failed: {str(e)}")
        return None
    except Exception as e:
        logger(f"  [!] An unexpected error occurred during download: {url} -> {str(e)}")
        return None


def find_local_cover(directory: str, logger) -> Optional[str]:
    # (Code from previous version, ensure imports/constants are correct)
    # logger(f"Searching for local cover in: {directory}") # Can be verbose
    for ext in IMAGE_EXTENSIONS:
        cover_path = os.path.join(directory, f"cover.{ext}")
        if os.path.isfile(cover_path):
            logger(f"  Found local cover: {cover_path}")
            return cover_path
    # logger("  No local cover file found.")
    return None


def process_flac_file_add_cover(flac_path: str, cover_image_path: str, logger) -> bool:
    # (Code from previous version, ensure imports/utils are correct)
    logger(f"Processing (Add Cover): {os.path.basename(flac_path)}")
    temp_output = f"{os.path.splitext(flac_path)[0]}.tmp_cover.flac"
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        logger("  [!] FFmpeg not found. Cannot process file.")
        return False
    cmd = [
        ffmpeg_path,
        "-i",
        flac_path,
        "-i",
        cover_image_path,
        "-map",
        "0:a",
        "-map",
        "1",
        "-c",
        "copy",
        "-map_metadata",
        "0",
        "-metadata:s:v",
        "title=Album cover",
        "-metadata:s:v",
        "comment=Cover (front)",
        "-disposition:v",
        "attached_pic",
        "-y",
        temp_output,
    ]
    success = run_ffmpeg_command(cmd, logger)  # Pass logger
    if success:
        try:
            os.replace(temp_output, flac_path)
            logger(f"  Successfully embedded cover in: {os.path.basename(flac_path)}")
            return True
        except Exception as e:
            logger(f"  [!] Error replacing original file: {str(e)}")
            return False
    else:
        logger(f"  [!] Failed to process: {os.path.basename(flac_path)}")
    # Cleanup failed temp file
    if os.path.exists(temp_output):
        safe_delete(temp_output, logger)
    return False


# --- Task Function Implementations ---


def task_add_covers(progress_callback, folder_path, cover_url):
    # (Code from previous version, ensure imports/utils/helpers are correct)
    progress_callback(f"Starting cover embedding process for folder: {folder_path}")
    if cover_url:
        progress_callback(f"Will attempt to use remote cover: {cover_url}")
    files_processed, files_succeeded, files_failed, folders_skipped = 0, 0, 0, 0
    all_flac_files = []
    for root, dirs, files in os.walk(folder_path):
        # progress_callback(f"Scanning directory: {root}") # Verbose
        current_flac_files = [
            os.path.join(root, f) for f in files if f.lower().endswith(".flac")
        ]
        if not current_flac_files:
            continue
        all_flac_files.extend(current_flac_files)
        # progress_callback(f"  Found {len(current_flac_files)} FLAC file(s) in {os.path.basename(root)}.") # Verbose
        cover_to_use, downloaded_temp_cover = None, None
        if cover_url:
            downloaded_temp_cover = download_cover(
                cover_url, root, progress_callback
            )  # Use helper
            if downloaded_temp_cover:
                cover_to_use = downloaded_temp_cover
                progress_callback(
                    f"  Using downloaded cover for: {os.path.basename(root)}"
                )
            # else: progress_callback("  Download failed. Checking local...") # Verbose
        if not cover_to_use:
            local_cover = find_local_cover(root, progress_callback)  # Use helper
            if local_cover:
                cover_to_use = local_cover
                progress_callback(f"  Using local cover for: {os.path.basename(root)}")
            else:
                progress_callback(
                    f"  Skipping directory (no cover found/downloaded): {os.path.basename(root)}"
                )
                folders_skipped += 1
                continue
        for flac_path in current_flac_files:
            files_processed += 1
            if process_flac_file_add_cover(flac_path, cover_to_use, progress_callback):
                files_succeeded += 1  # Use helper
            else:
                files_failed += 1
        if downloaded_temp_cover:
            safe_delete(downloaded_temp_cover, progress_callback)  # Use safe delete
    if not all_flac_files:
        return False, "No FLAC files found."
    summary = f"Cover embedding finished.\nTotal FLAC files found: {len(all_flac_files)}\nFiles processed: {files_processed}\nSucceeded: {files_succeeded}, Failed: {files_failed}\nFolders skipped (no cover): {folders_skipped}"
    return files_succeeded > 0 or files_failed == 0, summary


def task_convert_wav(progress_callback, wav_files: List[str], mode: str):
    # (Code from previous version, ensure imports/utils are correct)
    # --- Helpers moved outside ---
    def convert_wav_to_flac_simple(wav_path: str, logger) -> bool:
        logger(f"Processing (WAV->FLAC Simple): {os.path.basename(wav_path)}")
        flac_path = f"{os.path.splitext(wav_path)[0]}.flac"
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            logger("  [!] FFmpeg not found.")
            return False
        if os.path.exists(flac_path):
            logger(
                f"  Skipping: FLAC file already exists: {os.path.basename(flac_path)}"
            )
            return True
        cmd = [
            ffmpeg_path,
            "-i",
            wav_path,
            "-c:a",
            "flac",
            "-y",
            "-loglevel",
            FFMPEG_LOG_LEVEL,
            flac_path,
        ]
        success = run_ffmpeg_command(cmd, logger)
        if success:
            logger(f"  Successfully converted to: {os.path.basename(flac_path)}")
            return True
        else:
            logger(f"  [!] Failed to convert: {os.path.basename(wav_path)}")
            return False

    def convert_wav_to_flac_with_mp3_meta(wav_path: str, logger) -> bool:
        logger(f"Processing (WAV->FLAC w/ MP3 Meta): {os.path.basename(wav_path)}")
        base_name = os.path.splitext(os.path.basename(wav_path))[0]
        directory = os.path.dirname(wav_path)
        mp3_path = os.path.join(directory, f"{base_name}.mp3")
        flac_path = os.path.join(directory, f"{base_name}.flac")
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            logger("  [!] FFmpeg not found.")
            return False
        if not os.path.exists(mp3_path):
            logger(
                f"  Skipping: Corresponding MP3 not found: {os.path.basename(mp3_path)}"
            )
            return False
        if os.path.exists(flac_path):
            logger(
                f"  Skipping: FLAC file already exists: {os.path.basename(flac_path)}"
            )
            return True
        cmd = [
            ffmpeg_path,
            "-i",
            wav_path,
            "-i",
            mp3_path,
            "-map",
            "0:a",
            "-map",
            "1:v?",
            "-map_metadata",
            "1",
            "-c:a",
            "flac",
            "-c:v",
            "copy",
            "-disposition:v",
            "attached_pic",
            "-y",
            "-loglevel",
            FFMPEG_LOG_LEVEL,
            flac_path,
        ]
        success = run_ffmpeg_command(cmd, logger)
        if success:
            logger(
                f"  Successfully copied metadata and converted to: {os.path.basename(flac_path)}"
            )
            return True
        else:
            logger(
                f"  [!] Failed to convert/copy metadata for: {os.path.basename(wav_path)}"
            )
            return False

    # --- Main task logic ---
    progress_callback(f"Starting WAV conversion process ({mode})...")
    files_processed, files_succeeded, files_failed = 0, 0, 0
    if not wav_files:
        return False, "No WAV files selected."
    for wav_path in wav_files:
        files_processed += 1
        success = False
        if mode == "simple":
            success = convert_wav_to_flac_simple(wav_path, progress_callback)
        elif mode == "mp3_meta":
            success = convert_wav_to_flac_with_mp3_meta(wav_path, progress_callback)
        else:
            progress_callback(
                f"  Skipping {os.path.basename(wav_path)}: Invalid mode '{mode}'"
            )
            success = False
        if success:
            files_succeeded += 1
        else:
            files_failed += 1
    summary = f"WAV conversion ({mode}) finished.\nFiles processed: {files_processed}\nSucceeded/Skipped(Exists): {files_succeeded}, Failed: {files_failed}"
    return files_succeeded > 0 or files_failed == 0, summary


def task_edit_metadata(
    progress_callback, file_paths: List[str], metadata_updates: Dict[str, str]
):
    # (Code from previous version, ensure imports/utils/helpers are correct)
    progress_callback("Starting metadata editing process...")
    files_processed, files_succeeded, files_failed = 0, 0, 0
    if not file_paths:
        return False, "No audio files selected."
    if not metadata_updates:
        return False, "No metadata changes specified."
    progress_callback(f"Applying changes to {len(file_paths)} file(s):")
    for key, value in metadata_updates.items():
        action = f"'{value}'" if value else "[Clear Tag]"
        progress_callback(f"  - {EDITABLE_TAGS_LOWER.get(key.lower(), key)}: {action}")
    for file_path in file_paths:
        files_processed += 1
        if update_metadata(file_path, metadata_updates, progress_callback):
            files_succeeded += 1  # Use helper
        else:
            files_failed += 1
    summary = f"Metadata editing finished.\nFiles processed: {files_processed}\nSucceeded (or no changes needed): {files_succeeded}\nFailed: {files_failed}"
    return files_succeeded > 0 or files_failed == 0, summary


# --- NEW Task Function for CUE Splitting ---
def task_split_cue(
    progress_callback,
    cue_files: List[str],
    output_dir: Optional[str],
    output_format: str,
    collection: str,
    overwrite_mode: str,
):
    progress_callback("Starting CUE splitting process...")
    processed_count = 0
    success_count = 0
    fail_count = 0
    skipped_count = 0

    try:
        from ffcuesplitter.cuesplitter import FFCueSplitter
        from ffcuesplitter.user_service import FileSystemOperations
    except ImportError as e:
        progress_callback(f"[!] Error: Required library '{e.name}' not found.")
        progress_callback("Please install it using: pip install ffcuesplitter")
        return False, f"Required library '{e.name}' missing."

    ffmpeg_path = find_ffmpeg()
    ffprobe_path = find_ffprobe()
    if not ffmpeg_path or not ffprobe_path:
        msg = f"[!] Error: {'FFmpeg' if not ffmpeg_path else ''}{' and ' if not ffmpeg_path and not ffprobe_path else ''}{'FFprobe' if not ffprobe_path else ''} not found in PATH."
        progress_callback(msg)
        return False, msg

    for cue_file in cue_files:
        processed_count += 1
        progress_callback(f"\nProcessing CUE: {os.path.basename(cue_file)}")
        cue_dir = os.path.dirname(cue_file)
        # Determine where ffcuesplitter *will* output based on settings
        effective_output_dir = output_dir if output_dir else cue_dir

        # --- Start of Step 1: Get Info ---
        original_audio_files = set()
        split_successful = False
        try:
            # ... (info getting logic remains the same) ...
            info_getter = FFCueSplitter(
                filename=cue_file, dry=True, prg_loglevel="error"
            )
            tracks = info_getter.audiotracks
            if not tracks:
                progress_callback(
                    f"  [!] Error: Could not read tracks or audio file info from CUE."
                )
                fail_count += 1
                continue

            for track in tracks:
                if "FILE" in track:
                    audio_path_in_cue = track["FILE"]
                    full_audio_path = os.path.normpath(
                        os.path.join(cue_dir, audio_path_in_cue)
                    )
                    if os.path.exists(full_audio_path):
                        original_audio_files.add(full_audio_path)
                    else:
                        cue_base = os.path.splitext(os.path.basename(cue_file))[0]
                        audio_ext = os.path.splitext(audio_path_in_cue)[1]
                        alt_audio_path = os.path.normpath(
                            os.path.join(cue_dir, f"{cue_base}{audio_ext}")
                        )
                        if os.path.exists(alt_audio_path):
                            original_audio_files.add(alt_audio_path)
                        else:
                            progress_callback(
                                f"  [!] Warning: Referenced audio file '{audio_path_in_cue}' not found near CUE."
                            )
            if not original_audio_files:
                progress_callback(
                    f"  [!] Error: Could not find any existing audio file referenced in the CUE sheet."
                )
                fail_count += 1
                continue

        except Exception as e_info:
            progress_callback(f"  [!] Error getting info from CUE: {e_info}")
            fail_count += 1
            continue
        # --- End of Step 1 ---

        # --- Start of Step 2: Perform Split ---
        progress_callback(
            f"  Splitting to format '{output_format}' into: {effective_output_dir}"
            + (f" (Collection: {collection})" if collection else "")
        )
        try:
            kwargs = {
                "filename": cue_file,
                "outputdir": effective_output_dir,  # Use the determined output dir
                "outputformat": output_format,
                "collection": collection,
                "overwrite": overwrite_mode,
                "ffmpeg_cmd": ffmpeg_path,
                "ffprobe_cmd": ffprobe_path,
                "prg_loglevel": FFCS_PROG_LOG_LEVEL,
                "ffmpeg_loglevel": FFMPEG_LOG_LEVEL,
                "progress_meter": "standard",
                "dry": False,
            }
            split_op = FileSystemOperations(**kwargs)
            overwr = (
                split_op.check_for_overwriting()
            )  # Check destination based on kwargs
            if not overwr:
                split_op.work_on_temporary_directory()  # Library handles moving to final outputdir
                split_successful = True
                progress_callback("  Split command sequence executed.")
            else:
                progress_callback(
                    f"  Skipped: Overwrite needed but not permitted (Overwrite mode: '{overwrite_mode}')."
                )
                skipped_count += 1

        except Exception as e_split:
            progress_callback(
                f"  [!] Error during split execution: {type(e_split).__name__} - {e_split}"
            )
            fail_count += 1
            split_successful = False
        # --- End of Step 2 ---

        # --- Start of Step 3: Cleanup (MODIFIED) ---
        if split_successful:
            success_count += 1
            progress_callback(f"  Split successful. Cleaning up original files...")
            files_to_delete = {os.path.normpath(cue_file)}
            files_to_delete.update(original_audio_files)

            # Add log file with same base name as CUE, in the CUE's original directory
            cue_base_name = os.path.splitext(os.path.basename(cue_file))[
                0
            ]  # Get base name without ext
            log_file_path = os.path.normpath(
                os.path.join(cue_dir, f"{cue_base_name}.log")
            )  # Log in CUE dir
            if os.path.exists(log_file_path):
                files_to_delete.add(log_file_path)

            # Delete ffcuesplitter.log from the effective output directory (where it's generated)
            # Handle potential collection subdirectory structure as well
            # Note: ffcuesplitter *should* handle its own log rotation/management ideally,
            # but we explicitly delete it here as requested.

            # Base path for the log file (without collection structure)
            ffcs_log_base_path = os.path.join(effective_output_dir, "ffcuesplitter.log")

            # Check if collection is used to adjust the path
            ffcs_log_path = ffcs_log_base_path
            if collection:
                # We need to figure out the *actual* subdirs created.
                # FileSystemOperations doesn't directly expose this easily after run.
                # Simplest approach: Check the base output dir AND potential artist/album dirs.
                # This isn't perfect but covers common cases.
                potential_paths_to_check = {ffcs_log_base_path}
                try:
                    # Try to get artist/album from CUE info again for path guess
                    # Re-use info_getter if still valid, or recreate minimally
                    if "info_getter" not in locals():
                        info_getter = FFCueSplitter(
                            filename=cue_file, dry=True, prg_loglevel="error"
                        )

                    cd_info = info_getter.cue.meta.data
                    artist = cd_info.get("PERFORMER", "")
                    album = cd_info.get("ALBUM", "")

                    # Sanitize artist/album names for path use (basic example)
                    def sanitize(name):
                        return "".join(
                            c for c in name if c.isalnum() or c in (" ", "_", "-")
                        ).rstrip()

                    sanitized_artist = sanitize(artist)
                    sanitized_album = sanitize(album)

                    if collection == "artist" and sanitized_artist:
                        potential_paths_to_check.add(
                            os.path.join(
                                effective_output_dir,
                                sanitized_artist,
                                "ffcuesplitter.log",
                            )
                        )
                    elif collection == "album" and sanitized_album:
                        potential_paths_to_check.add(
                            os.path.join(
                                effective_output_dir,
                                sanitized_album,
                                "ffcuesplitter.log",
                            )
                        )
                    elif (
                        collection == "artist+album"
                        and sanitized_artist
                        and sanitized_album
                    ):
                        potential_paths_to_check.add(
                            os.path.join(
                                effective_output_dir,
                                sanitized_artist,
                                sanitized_album,
                                "ffcuesplitter.log",
                            )
                        )

                except Exception as e_path:
                    progress_callback(
                        f"  [!] Warning: Could not determine exact collection path for ffcuesplitter.log cleanup: {e_path}"
                    )

                # Check all potential paths
                found_ffcs_log = False
                for potential_path in potential_paths_to_check:
                    norm_potential_path = os.path.normpath(potential_path)
                    if os.path.exists(norm_potential_path):
                        files_to_delete.add(norm_potential_path)
                        found_ffcs_log = True
                        # Assume only one log location exists per run
                        break
                if not found_ffcs_log:
                    progress_callback(
                        f"  Note: ffcuesplitter.log not found in expected output location(s) for cleanup."
                    )

            else:  # No collection used
                norm_ffcs_log_path = os.path.normpath(ffcs_log_path)
                if os.path.exists(norm_ffcs_log_path):
                    files_to_delete.add(norm_ffcs_log_path)
                else:
                    progress_callback(
                        f"  Note: ffcuesplitter.log not found in output directory ({effective_output_dir}) for cleanup."
                    )

            for file_to_del in files_to_delete:
                safe_delete(file_to_del, progress_callback)
        # --- End of Step 3 ---

    # --- Final Summary ---
    summary = (
        f"CUE Splitting finished.\n"
        f"Files Processed: {processed_count}\n"
        f"Succeeded: {success_count}\n"
        f"Failed: {fail_count}\n"
        f"Skipped (Overwrite): {skipped_count}"
    )
    overall_success = fail_count == 0
    return overall_success, summary
