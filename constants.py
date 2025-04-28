# audio_toolkit/constants.py

# --- General Constants ---
IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp"]
VALID_MIME_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
AUDIO_META_EXTENSIONS = [".mp3", ".flac"]
AUDIO_WAV_EXTENSIONS = [".wav"]
CUE_EXTENSIONS = [".cue"]

# --- Metadata Editor Constants ---
EDITABLE_TAGS = {
    "artist": "Artist",
    "albumartist": "Album Artist",
    "album": "Album",
    "genre": "Genre",
    "date": "Year/Date (YYYY or YYYY-MM-DD)",
    "tracknumber": "Track Number (e.g., 5 or 5/12)",
    "discnumber": "Disc Number (e.g., 1 or 1/2)",
    "composer": "Composer",
    "comment": "Comment",
}
EDITABLE_TAGS_LOWER = {k.lower(): v for k, v in EDITABLE_TAGS.items()}
CLEAR_TAG_PLACEHOLDER = "__CLEAR_THIS_TAG__"
CLEAR_TAG_DISPLAY_TEXT = "<Marked for Clearing>"
MULTIPLE_VALUES_PLACEHOLDER = "<Multiple Values>"
MULTIPLE_VALUES_DISPLAY = "<i><Multiple Values></i>"

# --- CUE Splitter Constants ---
CUE_OUTPUT_FORMATS = ["flac", "wav", "mp3", "ogg", "opus", "copy"]
CUE_COLLECTION_OPTS = {
    "None": "",
    "Artist / Album": "artist+album",
    "Artist": "artist",
    "Album": "album",
}
CUE_OVERWRITE_MODES = {  # Maps GUI Checkbox state to ffcuesplitter arg
    False: "never",  # Default: Don't overwrite
    True: "always",  # If checkbox checked: Overwrite
}
FFMPEG_LOG_LEVEL = "info"  # Reduce ffmpeg verbosity in GUI log
FFPROBE_LOG_LEVEL = "info"
FFCS_PROG_LOG_LEVEL = "info"  # Reduce ffcuesplitter library verbosity
