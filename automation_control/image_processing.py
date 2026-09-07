from pathlib import Path

from PIL import Image, ImageOps

# Small margin added around an AI-detected bounding box so a slightly tight
# crop doesn't clip the card's edge.
# Padding is a FRACTION OF THE BOX, not a fixed slice of the image.
# As an absolute 0.02 it was tuned for cards, which fill a good part of
# the frame -- on a card 0.3 wide it adds a sane 13%. On a Sonny Angel
# 0.054 wide in a shelf photo the same constant adds 74%, which pulled
# four neighbouring figures into every crop and made them useless for
# identifying anything.
CROP_PADDING_FRACTION = 0.08
CROP_PADDING_MIN = 0.004


def auto_orient(path: Path) -> None:
    """Rewrite an image file upright according to its EXIF orientation tag,
    then strip the tag so it isn't re-applied by something else downstream.
    Phones commonly save landscape/upside-down photos with the pixel data
    untouched and just an orientation flag set -- most image viewers honor
    it, but naive `<img>` tags and PIL crops don't, so we bake it in once.
    """
    with Image.open(path) as img:
        oriented = ImageOps.exif_transpose(img)
        if oriented is None:
            return
        oriented.save(path)


def rotate_in_place(path: Path, rotation_degrees: int) -> None:
    """Rotate an already-saved image clockwise and overwrite it. Used by the
    retroactive orientation cleanup pass, separate from the normal capture
    flow where rotation happens as part of cropping (see crop_card).
    """
    if rotation_degrees % 360 == 0:
        return
    with Image.open(path) as img:
        rotated = img.rotate(-rotation_degrees, expand=True)
        rotated.save(path)


def crop_card(source_path: Path, dest_path: Path, bounding_box: list[float], rotation_degrees: int = 0) -> None:
    """Crop a single card out of a (possibly multi-card) photo using an
    AI-detected normalized bounding box [x_min, y_min, x_max, y_max], then
    rotate it upright. Raises on a malformed box rather than silently
    producing a garbage crop -- callers should catch and fall back to the
    uncropped source image.
    """
    if len(bounding_box) != 4:
        raise ValueError(f"expected 4 bounding box values, got {len(bounding_box)}")
    x_min, y_min, x_max, y_max = bounding_box
    if not (0 <= x_min < x_max <= 1 and 0 <= y_min < y_max <= 1):
        raise ValueError(f"bounding box out of range or inverted: {bounding_box}")

    pad_x = max(CROP_PADDING_MIN, (x_max - x_min) * CROP_PADDING_FRACTION)
    pad_y = max(CROP_PADDING_MIN, (y_max - y_min) * CROP_PADDING_FRACTION)
    x_min = max(0.0, x_min - pad_x)
    y_min = max(0.0, y_min - pad_y)
    x_max = min(1.0, x_max + pad_x)
    y_max = min(1.0, y_max + pad_y)

    with Image.open(source_path) as img:
        width, height = img.size
        box = (round(x_min * width), round(y_min * height), round(x_max * width), round(y_max * height))
        cropped = img.crop(box)
        if rotation_degrees % 360:
            cropped = cropped.rotate(-rotation_degrees, expand=True)
        cropped.save(dest_path)
