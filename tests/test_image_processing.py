import pytest
from PIL import Image

from automation_control.image_processing import auto_orient, crop_card, rotate_in_place


def _make_image(path, size=(200, 100), color=(255, 0, 0)):
    Image.new("RGB", size, color).save(path)


def test_auto_orient_applies_exif_rotation(tmp_path):
    path = tmp_path / "photo.jpg"
    img = Image.new("RGB", (200, 100), (255, 0, 0))
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90 CW
    img.save(path, exif=exif)

    auto_orient(path)

    with Image.open(path) as result:
        # a 90-degree-rotated 200x100 image becomes 100x200
        assert result.size == (100, 200)
        assert 0x0112 not in result.getexif()


def test_auto_orient_leaves_image_without_exif_untouched(tmp_path):
    path = tmp_path / "photo.jpg"
    _make_image(path, size=(200, 100))

    auto_orient(path)

    with Image.open(path) as result:
        assert result.size == (200, 100)


def test_crop_card_extracts_bounding_box_region(tmp_path):
    source = tmp_path / "group.jpg"
    _make_image(source, size=(1000, 500))
    dest = tmp_path / "card0.jpg"

    crop_card(source, dest, bounding_box=[0.1, 0.1, 0.4, 0.9])

    assert dest.exists()
    with Image.open(dest) as cropped:
        # box is [0.1,0.1,0.4,0.9], padded by CROP_PADDING (0.02) on each
        # side: x in [0.08, 0.42] * 1000 = 340 wide, y in [0.08, 0.92] * 500 = 420 tall
        assert cropped.size == (340, 420)


def test_crop_card_rotates_when_requested(tmp_path):
    source = tmp_path / "group.jpg"
    _make_image(source, size=(1000, 500))
    dest = tmp_path / "card0.jpg"

    crop_card(source, dest, bounding_box=[0.0, 0.0, 0.2, 0.4], rotation_degrees=90)

    with Image.open(dest) as cropped:
        # unrotated crop (padding clamped at 0 on the low end) is 220x210;
        # a 90-degree rotation swaps that to 210x220
        assert cropped.size == (210, 220)


def test_rotate_in_place_rotates_and_overwrites(tmp_path):
    path = tmp_path / "photo.jpg"
    _make_image(path, size=(200, 100))

    rotate_in_place(path, 90)

    with Image.open(path) as result:
        assert result.size == (100, 200)


def test_rotate_in_place_is_noop_for_zero_degrees(tmp_path):
    path = tmp_path / "photo.jpg"
    _make_image(path, size=(200, 100))
    original_bytes = path.read_bytes()

    rotate_in_place(path, 0)

    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("bbox", [
    [0.1, 0.1, 0.4],  # wrong length
    [0.5, 0.1, 0.1, 0.9],  # x_max < x_min
    [-0.1, 0.1, 0.4, 0.9],  # out of range
    [0.1, 0.1, 1.4, 0.9],  # out of range
])
def test_crop_card_rejects_malformed_bounding_box(tmp_path, bbox):
    source = tmp_path / "group.jpg"
    _make_image(source, size=(1000, 500))
    dest = tmp_path / "card0.jpg"

    with pytest.raises(ValueError):
        crop_card(source, dest, bounding_box=bbox)
    assert not dest.exists()
