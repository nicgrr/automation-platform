from pathlib import Path

from PIL import Image, ImageOps

# Small margin added around an AI-detected bounding box so a slightly tight
# crop doesn't clip the card's edge.
CROP_PADDING = 0.02


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

    x_min = max(0.0, x_min - CROP_PADDING)
    y_min = max(0.0, y_min - CROP_PADDING)
    x_max = min(1.0, x_max + CROP_PADDING)
    y_max = min(1.0, y_max + CROP_PADDING)

    with Image.open(source_path) as img:
        width, height = img.size
        box = (round(x_min * width), round(y_min * height), round(x_max * width), round(y_max * height))
        cropped = img.crop(box)
        if rotation_degrees % 360:
            cropped = cropped.rotate(-rotation_degrees, expand=True)
        cropped.save(dest_path)
