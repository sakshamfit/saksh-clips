"""Shared settings for the channel banner rendered over clips."""
import unicodedata

WATERMARK_POSITION_LABELS = {
    "top-left": "Top left",
    "top-right": "Top right",
    "bottom-left": "Bottom left",
    "bottom-right": "Bottom right",
}
WATERMARK_POSITIONS = tuple(WATERMARK_POSITION_LABELS)
DEFAULT_WATERMARK_POSITION = "top-left"
MAX_WATERMARK_TEXT = 48


def clean_watermark_text(value) -> str:
    """Keep the banner to one short, safe line of text."""
    if not isinstance(value, str):
        raise ValueError("Banner text must be text.")
    one_line = " ".join(value.split())
    one_line = "".join(ch for ch in one_line if unicodedata.category(ch) not in ("Cc", "Cs"))
    return one_line[:MAX_WATERMARK_TEXT]


def clean_watermark_position(value) -> str:
    """Only allow the four supported corner positions."""
    if not isinstance(value, str) or value not in WATERMARK_POSITION_LABELS:
        raise ValueError("Choose a corner for the channel banner.")
    return value
