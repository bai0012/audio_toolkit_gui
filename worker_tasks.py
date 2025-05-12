# audio_toolkit/worker_tasks.py

import os
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from venv import logger

import mutagen
import requests
from PyQt5.QtCore import QObject, pyqtSignal
from vgmdb_scraper import scrape_vgmdb_album, _parse_date, _get_preferred_lang

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

    finished = pyqtSignal(bool, object)  # Signal(success_bool, result_object)
    progress = pyqtSignal(str)

    def __init__(self, task_func, *args, **kwargs):
        super().__init__()
        self.task_func = task_func
        self.args = args
        self.kwargs = kwargs
        self.logger = kwargs.pop("logger_func", print)

    def run(self):
        try:
            # Task function now returns (bool, Any)
            result, message_or_data = self.task_func(
                self.progress.emit, *self.args, **self.kwargs
            )
            self.finished.emit(result, message_or_data)  # Emit the actual result object
        except ImportError as e:
            self.logger(f"[!] Worker thread error: Required library missing - {e}")
            self.finished.emit(
                False,
                f"Worker thread error: Required library missing.\nPlease ensure '{e.name}' is installed (`pip install {e.name}`).",
            )
        except Exception as e:
            self.logger(f"[!] Worker thread error: {type(e).__name__} - {str(e)}")
            import traceback

            self.logger(traceback.format_exc())  # Log traceback on unexpected errors
            self.finished.emit(
                False, f"Worker thread error: {type(e).__name__} - {str(e)}"
            )  # Emit error string


# --- Task Functions ---

# (Existing task_convert_wav, task_edit_metadata need slight mods
#  to use the logger correctly if they call helper functions that need it,
#  and import helpers/constants from new locations)


# --- Metadata Helpers (Pulled from old main file) ---
def get_metadata(file_path: str, logger) -> Dict[str, Any]:
    # (Code from previous version, ensure imports are correct)
    metadata = {}
    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is None:
            # Try again without easy=True for formats it might not support directly
            audio_raw = mutagen.File(file_path)
            if audio_raw is None:
                return {"error": "Unsupported format or read error"}
            # If raw loading worked but easy didn't, populate common tags manually if possible
            # This is a basic example, might need format-specific handling
            metadata["title"] = (
                str(audio_raw.get("TIT2", [""])[0]) if hasattr(audio_raw, "get") else ""
            )
            metadata["artist"] = (
                str(audio_raw.get("TPE1", [""])[0]) if hasattr(audio_raw, "get") else ""
            )
            metadata["album"] = (
                str(audio_raw.get("TALB", [""])[0]) if hasattr(audio_raw, "get") else ""
            )
            # Add more tags as needed...
            for key in EDITABLE_TAGS_LOWER:
                if key not in metadata:
                    metadata[key] = ""  # Ensure all keys exist
        else:
            # easy=True worked, proceed as before
            for key in EDITABLE_TAGS_LOWER:
                metadata[key] = "; ".join(audio[key]) if key in audio else ""
            metadata["title"] = "; ".join(audio["title"]) if "title" in audio else ""

        # Cover art check (remains the same logic)
        cover_info = "No"
        try:
            audio_raw = mutagen.File(
                file_path
            )  # Need raw file access for pictures/APIC
            if isinstance(audio_raw, mutagen.flac.FLAC) and audio_raw.pictures:
                cover_info = f"Yes ({len(audio_raw.pictures)} image(s))"
            elif isinstance(audio_raw, mutagen.mp3.MP3) and any(
                tag.startswith("APIC") for tag in audio_raw.tags.keys()
            ):
                cover_info = "Yes"
            # Add checks for other formats if needed (e.g., M4A, Ogg Vorbis)
            elif isinstance(audio_raw, mutagen.mp4.MP4) and "covr" in audio_raw.tags:
                cover_info = "Yes"
            elif (
                isinstance(audio_raw, mutagen.oggvorbis.OggVorbis)
                and "metadata_block_picture" in audio_raw.tags
            ):
                cover_info = "Yes"

            metadata["cover_art"] = cover_info
        except Exception as e_cover:
            # Don't overwrite existing metadata if only cover check fails
            if "cover_art" not in metadata:
                metadata["cover_art"] = "N/A"
            logger(
                f"  Warning: Could not check cover art for {os.path.basename(file_path)}: {e_cover}"
            )

    except mutagen.id3.ID3NoHeaderError:
        logger(f"  Warning: No ID3 header found in MP3: {os.path.basename(file_path)}")
        metadata = {k: "" for k in EDITABLE_TAGS_LOWER}
        metadata["title"] = ""
        metadata["cover_art"] = "N/A"
        metadata["error"] = "No ID3 Header"
    except Exception as e:
        logger(f"  Error reading metadata for {os.path.basename(file_path)}: {e}")
        metadata = {k: "" for k in EDITABLE_TAGS_LOWER}  # Default empty on error
        metadata["title"] = ""
        metadata["cover_art"] = "N/A"
        metadata["error"] = str(e)
    return metadata


def update_metadata(file_path: str, metadata_updates: Dict[str, str], logger) -> bool:
    # (Code from previous version, ensure imports/constants are correct)
    logger(f"Updating metadata for: {os.path.basename(file_path)}")
    try:
        # Try easy mode first, common for MP3/FLAC/M4A etc.
        audio = mutagen.File(file_path, easy=True)
        if audio is None or audio.tags is None:
            # If easy mode fails or no tags, try loading raw and adding tags
            logger(
                f"  [*] File type may not support easy tags or tags missing. Trying raw mode."
            )
            audio_raw = mutagen.File(file_path)
            if audio_raw is None:
                logger(f"  [!] Cannot load file: {os.path.basename(file_path)}")
                return False
            if audio_raw.tags is None:
                try:
                    logger(f"  [*] Adding tags structure...")
                    audio_raw.add_tags()
                    audio_raw.save()
                    # Reload in easy mode if possible after adding tags
                    audio = mutagen.File(file_path, easy=True)
                    if audio is None or audio.tags is None:
                        logger(
                            "  [*] Tags added, but easy mode still unavailable. Update might fail."
                        )
                        # Fallback: attempt to use raw object if easy still fails
                        # This part gets complex as tag names differ (e.g., TPE1 vs artist)
                        # For now, we'll primarily rely on easy=True working after add_tags
                        # or for formats that support it initially.
                        return False  # Simplification: If easy mode doesn't work, report failure for now.
                except Exception as e_add:
                    logger(f"  [!] Error adding tags structure: {e_add}")
                    return False
            else:
                # Tags exist, but easy mode failed. This is less common.
                logger(
                    "  [*] Tags structure exists, but easy mode failed. Update might fail."
                )
                return False  # Simplification for now.

        # Proceed with easy tags object
        audio_easy = audio  # Use the successfully loaded object

        needs_save = False
        updated_count = 0
        deleted_count = 0
        for key, value in metadata_updates.items():
            l_key = key.lower()
            # Use the mapping from constants to get the display name
            display_key = EDITABLE_TAGS_LOWER.get(l_key, l_key)

            if l_key not in EDITABLE_TAGS_LOWER:
                continue

            current_value_str = (
                "; ".join(audio_easy[l_key]) if l_key in audio_easy else ""
            )

            if value == "":  # Delete request
                if l_key in audio_easy:
                    try:
                        del audio_easy[l_key]
                        logger(f"      - Deleted tag {display_key}")
                        needs_save = True
                        deleted_count += 1
                    except Exception as e_del:
                        logger(f"      - Failed to delete tag {display_key}: {e_del}")
            elif value != current_value_str:  # Update request
                try:
                    # Split value by semicolon, strip whitespace, filter empty strings
                    new_value_list = [v.strip() for v in value.split(";") if v.strip()]
                    if new_value_list:
                        audio_easy[l_key] = new_value_list
                        logger(f"      - Set {display_key} = '{value}'")
                        needs_save = True
                        updated_count += 1
                    elif (
                        l_key in audio_easy
                    ):  # New value is empty string after stripping/splitting
                        del audio_easy[l_key]
                        logger(f"      - Deleted tag {display_key} (new value empty)")
                        needs_save = True
                        deleted_count += 1
                except Exception as e_set:
                    logger(f"  [!] Error setting tag '{display_key}': {e_set}")

        if needs_save:
            try:
                # Use the 'audio_easy' object which holds the changes
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
        import traceback

        logger(traceback.format_exc())
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
                # Allow download even if type unknown, maybe ffmpeg can handle it
                ext = path_ext if path_ext else "jpg"  # Default guess if no ext
                logger(
                    f"  [!] Warning: Unsupported or ambiguous image type '{content_type}' / '{path_ext}'. Attempting download as '.{ext}'."
                )
                # raise ValueError(f"Unsupported image type: {content_type} / {path_ext}") # Don't raise, just warn
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
    except ValueError as e:  # Keep this for actual ValueErrors if we add checks back
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
        # Check for 'folder.ext' as well, common convention
        folder_cover_path = os.path.join(directory, f"folder.{ext}")
        if os.path.isfile(folder_cover_path):
            logger(f"  Found local cover: {folder_cover_path}")
            return folder_cover_path
    # logger("  No local cover file found.")
    return None


# --- MODIFIED: Renamed and generalized function ---
def process_audio_file_add_cover(
    audio_path: str, cover_image_path: str, logger
) -> bool:
    """
    Embeds a cover image into an audio file (FLAC or MP3) using FFmpeg.
    Overwrites the original file on success.
    """
    logger(f"Processing (Add Cover): {os.path.basename(audio_path)}")
    base, ext = os.path.splitext(audio_path)
    # Ensure the temporary file has the correct extension for FFmpeg to work correctly
    temp_output = f"{base}.tmp_cover{ext}"

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        logger("  [!] FFmpeg not found. Cannot process file.")
        return False

    # This FFmpeg command works for embedding covers in both FLAC and MP3.
    # - For FLAC, it creates a FLAC picture block.
    # - For MP3, it creates an ID3v2 APIC frame.
    # - '-c copy' copies the audio stream without re-encoding.
    # - '-map 0:a' ensures only the audio stream from the original is mapped.
    # - '-map 1' maps the entire image file (input 1) as a new stream.
    # - '-map_metadata 0' copies metadata from the original audio file.
    # - '-disposition:v attached_pic' marks the image stream appropriately.
    cmd = [
        ffmpeg_path,
        "-i",
        audio_path,  # Input 0: Audio file
        "-i",
        cover_image_path,  # Input 1: Cover image
        "-map",
        "0:a",  # Map audio stream from input 0
        "-map",
        "1",  # Map image stream from input 1
        "-c",
        "copy",  # Copy audio stream, copy image stream (as appropriate tag/block)
        "-map_metadata",
        "0",  # Copy metadata from input 0 (the audio file)
        # Metadata for the *image stream itself*. More relevant for FLAC but harmless for MP3.
        "-metadata:s:v",
        "title=Album cover",
        "-metadata:s:v",
        "comment=Cover (front)",
        "-disposition:v",
        "attached_pic",  # Mark the video/image stream as an attached picture
        "-y",  # Overwrite temporary output file if it exists
        "-loglevel",
        FFMPEG_LOG_LEVEL,  # Use configured log level
        temp_output,
    ]

    success = run_ffmpeg_command(cmd, logger)  # Pass logger

    if success:
        try:
            # Verify the temp file exists before attempting replace
            if not os.path.exists(temp_output):
                logger(
                    f"  [!] Error: FFmpeg reported success, but output file '{os.path.basename(temp_output)}' not found."
                )
                return False
            # Replace the original file with the new one
            os.replace(temp_output, audio_path)
            logger(f"  Successfully embedded cover in: {os.path.basename(audio_path)}")
            return True
        except Exception as e:
            logger(
                f"  [!] Error replacing original file '{os.path.basename(audio_path)}' with temp file: {str(e)}"
            )
            # Attempt to clean up the temp file if replacement fails
            safe_delete(temp_output, logger)
            return False
    else:
        logger(f"  [!] FFmpeg failed to process: {os.path.basename(audio_path)}")
        # Cleanup failed temp file
        if os.path.exists(temp_output):
            safe_delete(temp_output, logger)
        return False


# --- Task Function Implementations ---


# --- MODIFIED: task_add_covers now handles FLAC and MP3 ---
def task_add_covers(progress_callback, folder_path, cover_url):
    """
    Worker task to find FLAC and MP3 files in a folder and embed covers.
    Uses a remote URL or local 'cover.*' / 'folder.*' image.
    """
    progress_callback(f"Starting cover embedding process for folder: {folder_path}")
    if cover_url:
        progress_callback(f"Will attempt to use remote cover: {cover_url}")

    files_processed, files_succeeded, files_failed, folders_skipped = 0, 0, 0, 0
    all_audio_files = []  # List to store all found audio files

    for root, dirs, files in os.walk(folder_path):
        # Find both FLAC and MP3 files in the current directory
        current_audio_files = [
            os.path.join(root, f)
            for f in files
            if f.lower().endswith((".flac", ".mp3"))  # Check for both extensions
        ]

        if not current_audio_files:
            continue  # Skip directory if no relevant audio files found

        all_audio_files.extend(current_audio_files)
        # progress_callback(f"  Found {len(current_audio_files)} FLAC/MP3 file(s) in {os.path.basename(root)}.") # Verbose

        cover_to_use, downloaded_temp_cover = None, None

        # 1. Try downloading cover if URL provided
        if cover_url:
            downloaded_temp_cover = download_cover(
                cover_url, root, progress_callback
            )  # Use helper
            if downloaded_temp_cover:
                cover_to_use = downloaded_temp_cover
                progress_callback(
                    f"  Using downloaded cover for directory: {os.path.basename(root)}"
                )
            # else: progress_callback("  Download failed. Checking local...") # Verbose

        # 2. If no downloaded cover, look for local cover
        if not cover_to_use:
            local_cover = find_local_cover(root, progress_callback)  # Use helper
            if local_cover:
                cover_to_use = local_cover
                progress_callback(
                    f"  Using local cover for directory: {os.path.basename(root)}"
                )
            else:
                progress_callback(
                    f"  Skipping directory (no cover found/downloaded): {os.path.basename(root)}"
                )
                folders_skipped += 1
                # Must clean up temp download if it exists but wasn't used (e.g., local search failed after download)
                if downloaded_temp_cover:
                    safe_delete(downloaded_temp_cover, progress_callback)
                continue  # Move to the next directory

        # 3. Process all audio files in the current directory using the selected cover
        for audio_path in current_audio_files:
            files_processed += 1
            # Use the generalized processing function
            if process_audio_file_add_cover(
                audio_path, cover_to_use, progress_callback
            ):
                files_succeeded += 1
            else:
                files_failed += 1

        # 4. Clean up the temporary downloaded cover *after* processing all files in the directory
        if downloaded_temp_cover:
            safe_delete(downloaded_temp_cover, progress_callback)  # Use safe delete

    if not all_audio_files:
        return (
            False,
            "No FLAC or MP3 files found in the selected folder.",
        )  # Updated message

    # Updated summary message
    summary = (
        f"Cover embedding finished.\n"
        f"Total FLAC/MP3 files found: {len(all_audio_files)}\n"
        f"Files processed: {files_processed}\n"
        f"Succeeded: {files_succeeded}, Failed: {files_failed}\n"
        f"Folders skipped (no cover): {folders_skipped}"
    )

    # Consider success if at least one file succeeded OR if no files failed (e.g., all skipped okay)
    overall_success = (
        files_succeeded > 0
        or (files_processed > 0 and files_failed == 0)
        or (files_processed == 0 and folders_skipped > 0 and files_failed == 0)
    )

    return overall_success, summary


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
            return True  # Count as success if already exists
        cmd = [
            ffmpeg_path,
            "-i",
            wav_path,
            "-c:a",
            "flac",
            "-compression_level",  # Optional: Specify compression (e.g., 5 is default, 8 is higher)
            "8",
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
            # Clean up potentially incomplete output file on failure
            if os.path.exists(flac_path):
                safe_delete(flac_path, logger)
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
            # This is not a failure of the *task*, just a skip condition. Return True? Or False?
            # Let's return False as the conversion *didn't happen*. The summary counts failures.
            return False
        if os.path.exists(flac_path):
            logger(
                f"  Skipping: FLAC file already exists: {os.path.basename(flac_path)}"
            )
            return True  # Count as success if already exists

        # Command to convert WAV, copy metadata and *any* video streams (like cover art) from MP3
        cmd = [
            ffmpeg_path,
            "-i",
            wav_path,  # Input 0: WAV
            "-i",
            mp3_path,  # Input 1: MP3
            "-map",
            "0:a",  # Map audio from WAV (input 0)
            "-map",
            "1:v?",  # Map video stream(s) from MP3 if they exist (input 1, '?')
            "-map_metadata",
            "1",  # Copy metadata from MP3 (input 1)
            "-c:a",
            "flac",  # Encode audio to FLAC
            "-compression_level",
            "8",  # Optional: specify compression
            "-c:v",
            "copy",  # Copy video stream(s) (cover art) without re-encoding
            # Ensure disposition is copied or set if needed (FFmpeg often handles this with map_metadata)
            # '-disposition:v', 'attached_pic', # Usually not needed if copied correctly
            "-y",
            "-loglevel",
            FFMPEG_LOG_LEVEL,
            flac_path,
        ]
        success = run_ffmpeg_command(cmd, logger)
        if success:
            logger(
                f"  Successfully copied metadata/cover and converted to: {os.path.basename(flac_path)}"
            )
            return True
        else:
            logger(
                f"  [!] Failed to convert/copy metadata for: {os.path.basename(wav_path)}"
            )
            # Clean up potentially incomplete output file on failure
            if os.path.exists(flac_path):
                safe_delete(flac_path, logger)
            return False

    # --- Main task logic ---
    progress_callback(f"Starting WAV conversion process ({mode})...")
    files_processed, files_succeeded, files_failed, files_skipped = 0, 0, 0, 0
    if not wav_files:
        return False, "No WAV files selected."

    total_files = len(wav_files)
    for i, wav_path in enumerate(wav_files):
        progress_callback(
            f"Processing file {i+1}/{total_files}: {os.path.basename(wav_path)}"
        )
        files_processed += 1
        success = False
        skip = False  # Flag to differentiate skips from failures

        if mode == "simple":
            # Check for existing FLAC before calling the convert function
            flac_path_simple = f"{os.path.splitext(wav_path)[0]}.flac"
            if os.path.exists(flac_path_simple):
                logger(
                    f"  Skipping: FLAC file already exists: {os.path.basename(flac_path_simple)}"
                )
                success = True  # Treat existing as success for summary
                skip = True
            else:
                success = convert_wav_to_flac_simple(wav_path, progress_callback)
        elif mode == "mp3_meta":
            # Check for existing FLAC and missing MP3 before calling convert function
            base_name_meta = os.path.splitext(os.path.basename(wav_path))[0]
            directory_meta = os.path.dirname(wav_path)
            mp3_path_meta = os.path.join(directory_meta, f"{base_name_meta}.mp3")
            flac_path_meta = os.path.join(directory_meta, f"{base_name_meta}.flac")

            if os.path.exists(flac_path_meta):
                logger(
                    f"  Skipping: FLAC file already exists: {os.path.basename(flac_path_meta)}"
                )
                success = True  # Treat existing as success
                skip = True
            elif not os.path.exists(mp3_path_meta):
                logger(
                    f"  Skipping: Corresponding MP3 not found: {os.path.basename(mp3_path_meta)}"
                )
                success = False  # Conversion didn't happen, count as failure/skip? Let's count as skipped.
                skip = True  # Explicitly mark as skipped
            else:
                success = convert_wav_to_flac_with_mp3_meta(wav_path, progress_callback)
        else:
            progress_callback(
                f"  Skipping {os.path.basename(wav_path)}: Invalid mode '{mode}'"
            )
            success = False
            skip = True  # Skipped due to invalid mode

        if skip:
            files_skipped += 1
            # If we counted existing/skipped-no-mp3 as success=True for the overall task result,
            # ensure it's also counted in succeeded stats if not skipped for other reasons.
            if success:
                files_succeeded += 1
            else:
                # If skip=True but success=False (e.g. skipped-no-mp3, invalid mode)
                # We don't count it in failed, just skipped.
                pass  # Only increment files_skipped
        elif success:
            files_succeeded += 1
        else:
            files_failed += 1

    # Refined summary
    summary = (
        f"WAV conversion ({mode}) finished.\n"
        f"Files processed: {files_processed}\n"
        f"Succeeded: {files_succeeded}\n"
        f"Skipped (Exists/No MP3/Invalid Mode): {files_skipped}\n"
        f"Failed: {files_failed}"
    )

    # Success if no failures occurred, even if some were skipped
    overall_success = files_failed == 0 and files_processed > 0
    return overall_success, summary


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

    # Log the changes being applied
    progress_callback(f"Applying changes to {len(file_paths)} file(s):")
    change_list = []
    for key, value in metadata_updates.items():
        # Use the display name from constants map
        display_key = EDITABLE_TAGS_LOWER.get(key.lower(), key)
        action = f"'{value}'" if value else "[Clear Tag]"
        change_list.append(f"  - {display_key}: {action}")
    progress_callback("\n".join(change_list))

    total_files = len(file_paths)
    for i, file_path in enumerate(file_paths):
        progress_callback(
            f"\nProcessing file {i+1}/{total_files}: {os.path.basename(file_path)}"
        )
        files_processed += 1
        # Call the updated helper function
        if update_metadata(file_path, metadata_updates, progress_callback):
            files_succeeded += 1
        else:
            files_failed += 1

    summary = (
        f"Metadata editing finished.\n"
        f"Files processed: {files_processed}\n"
        f"Succeeded (or no changes needed): {files_succeeded}\n"
        f"Failed: {files_failed}"
    )

    # Success if no failures occurred
    overall_success = files_failed == 0 and files_processed > 0
    return overall_success, summary


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
        missing = []
        if not ffmpeg_path:
            missing.append("FFmpeg")
        if not ffprobe_path:
            missing.append("FFprobe")
        msg = f"[!] Error: { ' and '.join(missing) } not found in PATH or system environment."
        progress_callback(msg)
        return False, msg

    total_files = len(cue_files)
    for i, cue_file in enumerate(cue_files):
        processed_count += 1
        progress_callback(
            f"\nProcessing CUE {i+1}/{total_files}: {os.path.basename(cue_file)}"
        )
        cue_dir = os.path.dirname(cue_file)
        # Determine where ffcuesplitter *will* output based on settings
        effective_output_dir = output_dir if output_dir else cue_dir

        # --- Start of Step 1: Get Info ---
        original_audio_files = set()
        split_successful = False
        info_getter = None  # Define scope outside try block
        try:
            # Instantiate once to get info
            info_getter = FFCueSplitter(
                filename=cue_file,
                dry=True,  # Don't split yet, just parse
                prg_loglevel="error",  # Keep info gathering quiet
            )
            tracks = info_getter.audiotracks
            if not tracks:
                progress_callback(
                    f"  [!] Error: Could not read tracks or audio file info from CUE."
                )
                fail_count += 1
                continue

            # Find associated audio file(s) - robust check
            found_audio_for_cue = False
            for track in tracks:
                if "FILE" in track:
                    audio_path_in_cue = track["FILE"]
                    # Try path relative to CUE file first
                    full_audio_path_rel = os.path.normpath(
                        os.path.join(cue_dir, audio_path_in_cue)
                    )
                    # Also check if the path in CUE was absolute
                    full_audio_path_abs = os.path.normpath(audio_path_in_cue)

                    if os.path.exists(full_audio_path_rel):
                        original_audio_files.add(full_audio_path_rel)
                        found_audio_for_cue = True
                    elif os.path.exists(full_audio_path_abs):
                        original_audio_files.add(full_audio_path_abs)
                        found_audio_for_cue = True
                    else:
                        # Try common case: audio file has same base name as CUE file
                        cue_base = os.path.splitext(os.path.basename(cue_file))[0]
                        audio_ext = os.path.splitext(audio_path_in_cue)[1]
                        alt_audio_path = os.path.normpath(
                            os.path.join(cue_dir, f"{cue_base}{audio_ext}")
                        )
                        if os.path.exists(alt_audio_path):
                            original_audio_files.add(alt_audio_path)
                            found_audio_for_cue = True
                        # else: # Don't warn for every track, just once if none found
                        #     pass

            if not found_audio_for_cue:
                progress_callback(
                    f"  [!] Error: Could not find any existing audio file referenced in the CUE sheet."
                )
                fail_count += 1
                continue  # Skip this CUE file

        except Exception as e_info:
            progress_callback(f"  [!] Error getting info from CUE: {e_info}")
            fail_count += 1
            continue  # Skip this CUE file
        # --- End of Step 1 ---

        # --- Start of Step 2: Perform Split ---
        progress_callback(
            f"  Splitting to format '{output_format}' into: {effective_output_dir}"
            + (f" (Collection: {collection})" if collection else "")
        )
        split_op = None  # Define scope
        try:
            # Prepare arguments for FileSystemOperations
            kwargs = {
                "filename": cue_file,
                "outputdir": effective_output_dir,
                "outputformat": output_format,
                "collection": (
                    collection if collection else None
                ),  # Pass None if empty string
                "overwrite": overwrite_mode,
                "ffmpeg_cmd": ffmpeg_path,
                "ffprobe_cmd": ffprobe_path,
                "prg_loglevel": FFCS_PROG_LOG_LEVEL,
                "ffmpeg_loglevel": FFMPEG_LOG_LEVEL,
                "progress_meter": "standard",  # Or None? Check library docs/behavior
                "dry": False,  # Actually perform the split
            }
            split_op = FileSystemOperations(**kwargs)

            # Check for potential overwrites *before* executing
            overwr = split_op.check_for_overwriting()
            if overwr:
                # Library's check returns a list of files that would be overwritten.
                # If the list is not empty and overwrite mode is 'no', skip.
                if overwrite_mode == "no":
                    progress_callback(
                        f"  Skipped: Output file(s) already exist and overwrite mode is 'no'."
                    )
                    progress_callback(f"  Files that would be overwritten: {overwr}")
                    skipped_count += 1
                    continue  # Skip this CUE file
                elif overwrite_mode == "force":
                    progress_callback(
                        f"  Warning: Overwriting existing file(s) as per 'force' mode: {overwr}"
                    )
                # If mode is 'ask', this library isn't interactive, so treat like 'no'? Or proceed?
                # Assuming GUI handles 'ask' logic before calling task, treat 'ask' like 'no' here.
                elif overwrite_mode == "ask":
                    progress_callback(
                        f"  Skipped: Output file(s) already exist and overwrite mode is 'ask' (treated as 'no' in non-interactive task)."
                    )
                    progress_callback(f"  Files that would be overwritten: {overwr}")
                    skipped_count += 1
                    continue

            # Execute the split operation
            split_op.work_on_temporary_directory()  # This performs the split & moves files
            split_successful = True
            progress_callback("  Split command sequence executed successfully.")

        except Exception as e_split:
            progress_callback(
                f"  [!] Error during split execution: {type(e_split).__name__} - {e_split}"
            )
            import traceback

            progress_callback(traceback.format_exc())  # Log traceback for split errors
            fail_count += 1
            split_successful = False
            # No cleanup of originals needed if split failed
            continue  # Move to next CUE file
        # --- End of Step 2 ---

        # --- Start of Step 3: Cleanup (MODIFIED) ---
        if split_successful:
            success_count += 1
            progress_callback(f"  Split successful. Cleaning up original files...")
            files_to_delete = {os.path.normpath(cue_file)}
            files_to_delete.update(
                original_audio_files
            )  # Add all found original audio files

            # Add log file with same base name as CUE, in the CUE's original directory (if it exists)
            cue_base_name = os.path.splitext(os.path.basename(cue_file))[0]
            log_file_path_cue_dir = os.path.normpath(
                os.path.join(cue_dir, f"{cue_base_name}.log")
            )
            if os.path.exists(log_file_path_cue_dir):
                files_to_delete.add(log_file_path_cue_dir)

            # Delete ffcuesplitter.log from the effective output directory (where it's generated)
            # The library *should* place this log in the final output dir, potentially within collection subdirs.
            ffcs_log_name = "ffcuesplitter.log"
            # Determine the *actual* final directory structure created by ffcuesplitter
            # This is tricky without direct feedback from the library.
            # We can *guess* based on the collection mode and metadata.
            final_log_dir = effective_output_dir
            if (
                collection and info_getter and info_getter.cue.meta
            ):  # Check if we have info_getter and metadata
                cd_info = info_getter.cue.meta.data
                artist = cd_info.get("PERFORMER", "")
                album = cd_info.get("ALBUM", "")

                # Use library's own path sanitization if possible, otherwise basic one
                sanitize = getattr(
                    split_op,
                    "_FileSystemOperations__sanitize_path_element",
                    lambda x: "".join(
                        c for c in x if c.isalnum() or c in (" ", "_", "-")
                    ).rstrip(),
                )

                sanitized_artist = sanitize(artist) if artist else None
                sanitized_album = sanitize(album) if album else None

                path_parts = []
                if collection == "artist" and sanitized_artist:
                    path_parts.append(sanitized_artist)
                elif collection == "album" and sanitized_album:
                    path_parts.append(sanitized_album)
                elif (
                    collection == "artist+album"
                    and sanitized_artist
                    and sanitized_album
                ):
                    path_parts.append(sanitized_artist)
                    path_parts.append(sanitized_album)

                if path_parts:
                    final_log_dir = os.path.join(effective_output_dir, *path_parts)

            # Now construct the potential path to the log file
            ffcs_log_path = os.path.normpath(os.path.join(final_log_dir, ffcs_log_name))

            if os.path.exists(ffcs_log_path):
                files_to_delete.add(ffcs_log_path)
            else:
                # Log if not found, might indicate an issue or different library behavior
                progress_callback(
                    f"  Note: {ffcs_log_name} not found in expected output location ({final_log_dir}) for cleanup."
                )
                # Also check the base output dir just in case
                ffcs_log_path_base = os.path.normpath(
                    os.path.join(effective_output_dir, ffcs_log_name)
                )
                if final_log_dir != effective_output_dir and os.path.exists(
                    ffcs_log_path_base
                ):
                    files_to_delete.add(ffcs_log_path_base)
                    progress_callback(
                        f"  Found and added {ffcs_log_name} from base output directory."
                    )

            # Perform deletions
            progress_callback(
                f"  Files identified for deletion: { {os.path.basename(f) for f in files_to_delete} }"
            )
            deleted_count_this_cue = 0
            failed_delete_count_this_cue = 0
            for file_to_del in files_to_delete:
                if safe_delete(file_to_del, progress_callback):
                    deleted_count_this_cue += 1
                else:
                    failed_delete_count_this_cue += 1
            if failed_delete_count_this_cue > 0:
                progress_callback(
                    f"  [!] Warning: Failed to delete {failed_delete_count_this_cue} original/log file(s)."
                )
            else:
                progress_callback(
                    f"  Successfully deleted {deleted_count_this_cue} original/log file(s)."
                )
        # --- End of Step 3 ---

    # --- Final Summary ---
    summary = (
        f"CUE Splitting finished.\n"
        f"CUE Files Processed: {processed_count}\n"
        f"Succeeded: {success_count}\n"
        f"Failed: {fail_count}\n"
        f"Skipped (Overwrite/Error): {skipped_count}"  # Skipped includes overwrite and pre-split errors
    )
    # Overall success if no failures occurred during processing/splitting itself
    overall_success = fail_count == 0 and processed_count > 0
    return overall_success, summary


def task_fetch_vgmdb(progress_callback, album_id: str):
    """
    Worker task to fetch album data from VGMdb.

    Args:
        progress_callback (callable): Function to report progress/log messages.
        album_id (str): The VGMdb album ID (e.g., "12345") or full URL.

    Returns:
        tuple: (bool success, dict data or str error_message)
    """
    progress_callback(f"Starting VGMdb fetch for input: {album_id}")

    # Extract album ID from URL or use directly
    cleaned_album_id = None
    if isinstance(album_id, str):
        match_url = re.search(r"vgmdb\.net/album/(\d+)", album_id)
        if match_url:
            cleaned_album_id = match_url.group(1)
            progress_callback(f"  Extracted Album ID {cleaned_album_id} from URL.")
        else:
            match_id = re.match(r"\d+", album_id.strip())
            if match_id:
                cleaned_album_id = match_id.group(0)
            else:
                msg = f"Invalid VGMdb Album ID or URL format: '{album_id}'. Provide ID (e.g., 12345) or URL."
                progress_callback(f"[!] {msg}")
                return False, msg
    else:  # Handle non-string input if necessary, e.g. integer
        match_id = re.match(r"\d+", str(album_id))
        if match_id:
            cleaned_album_id = match_id.group(0)
        else:
            msg = (
                f"Invalid VGMdb Album ID format: '{album_id}'. Should be numbers only."
            )
            progress_callback(f"[!] {msg}")
            return False, msg

    progress_callback(f"Attempting to fetch data for VGMdb ID: {cleaned_album_id}")
    try:
        # Call the scraper function, passing our progress_callback as the logger
        # The scraper library itself might have internal logging; this adds our task logging.
        scraped_data = scrape_vgmdb_album(cleaned_album_id, logger=progress_callback)

        if not scraped_data:
            # Scraper might return None or empty dict on failure without explicit error
            msg = f"Scraper returned no data for ID {cleaned_album_id}. Check network connection, ID validity, and VGMdb status."
            progress_callback(f"[!] {msg}")
            return False, msg
        elif scraped_data.get("_error"):
            # Check for specific error key the scraper might add
            msg = f"Scraping failed for ID {cleaned_album_id}: {scraped_data['_error']}"
            progress_callback(f"[!] {msg}")
            # Return the partial data anyway so user can see what was found, but flag as failure
            return False, scraped_data  # Return data dict even on error for inspection
        else:
            progress_callback(
                f"Successfully fetched and parsed data for VGMdb ID {cleaned_album_id}."
            )
            return True, scraped_data  # Return the full dictionary on success

    except ImportError:
        # This shouldn't happen if Worker handles it, but as a fallback
        progress_callback(
            "[!] Error: 'vgmdb_scraper' library not found. Please install it (`pip install vgmdb-scraper`)."
        )
        return False, "Required library 'vgmdb_scraper' missing."
    except requests.exceptions.RequestException as e:
        progress_callback(
            f"[!] Network error during VGMdb fetch for ID {cleaned_album_id}: {e}"
        )
        return False, f"Network error: {e}"
    except Exception as e:
        progress_callback(
            f"[!] Unexpected error during VGMdb fetch for ID {cleaned_album_id}: {type(e).__name__} - {e}"
        )
        import traceback

        progress_callback(traceback.format_exc())  # Log full traceback for debugging
        return False, f"Unexpected error during fetch: {e}"
