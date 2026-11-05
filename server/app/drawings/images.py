"""Everything done to a sheet image without a model: checking an upload, shrinking, cropping, cutting tiles.

Positions are pixels of the stored sheet image. The model answers on a 0..1000 scale of the picture it was shown, so its
answer maps back to the sheet the same way whether the picture was shrunk or not, and whether it was the sheet or a crop.
"""

import base64
import io
import math
from dataclasses import dataclass

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

Box = list[float]  # x0, y0, x1, y1

# Pillow's bomb check would refuse large scans; read_upload checks the size itself before decoding any pixels.
Image.MAX_IMAGE_PIXELS = None
MAX_SHEET_PIXELS = 400_000_000
MAX_FRAMES = 500

PATCH_PX = 32  # OpenAI measures an image in patches of this size
MODEL_SCALE = 1000
EXIF_ORIENTATION = 274

# Stored and sent as they are. Other formats (a TIFF scan) are stored as PNG, one sheet per page.
PASS_THROUGH = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
ACCEPTED = set(PASS_THROUGH) | {"TIFF"}


class BadImage(ValueError):
    """The upload is not a usable drawing image. The message is for the user."""


@dataclass(frozen=True)
class SheetFile:
    data: bytes
    content_type: str
    width: int
    height: int


def to_rgb(im: Image.Image) -> Image.Image:
    """A copy with 8-bit colours, on white where the image was transparent."""
    if im.mode.startswith("I;16"):  # 16-bit greyscale scan: convert() would clip it to white
        return im.point(lambda v: v * (1 / 256)).convert("L").convert("RGB")
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        back = Image.new("RGBA", rgba.size, "white")
        back.alpha_composite(rgba)
        return back.convert("RGB")
    return im.convert("RGB")


def to_png(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def read_upload(data: bytes) -> list[SheetFile]:
    """The sheets in an uploaded image: one, or one per page of a TIFF. Raises BadImage."""
    try:
        im = Image.open(io.BytesIO(data))
        fmt = im.format
        if fmt not in ACCEPTED:
            raise BadImage("A drawing must be a PNG, JPEG, WEBP or TIFF image.")
        frames = getattr(im, "n_frames", 1)
        if fmt != "TIFF" and frames > 1:
            raise BadImage("Animated images are not drawings.")
        if frames > MAX_FRAMES:
            raise BadImage(f"This file has {frames} pages; the limit is {MAX_FRAMES}.")
        sheets: list[SheetFile] = []
        for frame in ImageSequence.Iterator(im):
            if frame.width * frame.height > MAX_SHEET_PIXELS:
                raise BadImage(f"A {frame.width} x {frame.height} image is too large; the limit is {MAX_SHEET_PIXELS:,} pixels.")
            frame.load()  # a truncated or corrupt file fails here, not in the middle of a run
            turned = frame.getexif().get(EXIF_ORIENTATION, 1) not in (0, 1)
            if fmt in PASS_THROUGH and not turned and frame.mode != "CMYK":  # browsers and the model handle CMYK badly
                sheets.append(SheetFile(data, PASS_THROUGH[fmt], frame.width, frame.height))
            else:  # stored upright, so the positions on it mean the same to the model and to the browser
                upright = to_rgb(ImageOps.exif_transpose(frame))
                sheets.append(SheetFile(to_png(upright), "image/png", upright.width, upright.height))
        return sheets
    except BadImage:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:  # what Pillow raises on a bad file
        raise BadImage("This file is not a readable image.") from exc


def patches(width: int, height: int) -> int:
    return math.ceil(width / PATCH_PX) * math.ceil(height / PATCH_PX)


def fit_size(width: int, height: int, max_patches: int) -> tuple[int, int]:
    """The largest size with the same shape that fits the model's limit. An image that fits keeps its size."""
    if patches(width, height) <= max_patches:
        return width, height
    scale = math.sqrt(max_patches / patches(width, height))
    while True:
        w, h = max(1, math.floor(width * scale)), max(1, math.floor(height * scale))
        if patches(w, h) <= max_patches:
            return w, h
        scale *= 0.99


def shrink(im: Image.Image, width: int, height: int) -> Image.Image:
    """Lanczos, which keeps thin dark lines visible. A very large step goes through reduce() first, which avoids aliasing."""
    factor = min(im.width // (width * 2), im.height // (height * 2))
    if factor >= 2:
        im = im.reduce(factor)
    return im.resize((width, height), Image.Resampling.LANCZOS)


def data_url(data: bytes, content_type: str) -> str:
    return f"data:{content_type};base64,{base64.b64encode(data).decode()}"


def sheet_for_model(sheet: SheetFile, im: Image.Image, max_patches: int) -> str:
    """The sheet as the model gets it: the original when it fits the limit, otherwise a shrunk copy."""
    width, height = fit_size(sheet.width, sheet.height, max_patches)
    if (width, height) == (sheet.width, sheet.height):
        return data_url(sheet.data, sheet.content_type)
    return data_url(to_png(shrink(im, width, height)), "image/png")


def crop_png(im: Image.Image, box: Box) -> bytes:
    return to_png(im.crop((math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3]))))


def picture_for_model(png: bytes, max_patches: int) -> str:
    """A crop or tile as the model gets it, shrunk only if it is over the limit."""
    with Image.open(io.BytesIO(png)) as im:
        width, height = fit_size(im.width, im.height, max_patches)
        if (width, height) == im.size:
            return data_url(png, "image/png")
        return data_url(to_png(shrink(im, width, height)), "image/png")


def tile_boxes(width: int, height: int, size: int, overlap: float) -> list[Box]:
    """Overlapping squares covering the sheet, so text cut by one tile's edge is whole in the next. The last tile of a
    row or column is pushed back to end at the sheet's edge."""
    step = max(1, round(size * (1 - overlap)))

    def starts(length: int) -> list[int]:
        if length <= size:
            return [0]
        out = list(range(0, length - size, step))
        return out + [length - size]

    return [
        [float(x), float(y), float(min(x + size, width)), float(min(y + size, height))]
        for y in starts(height)
        for x in starts(width)
    ]


def from_model_box(box: list[int] | list[float], region: Box) -> Box | None:
    """A box on the model's 0..1000 scale of `region`, in sheet pixels. None if it is not a real box."""
    if len(box) != 4:
        return None
    x0, y0, x1, y1 = (min(max(float(v), 0.0), MODEL_SCALE) / MODEL_SCALE for v in box)
    if x1 <= x0 or y1 <= y0:
        return None
    width, height = region[2] - region[0], region[3] - region[1]
    return [
        round(region[0] + x0 * width, 1),
        round(region[1] + y0 * height, 1),
        round(region[0] + x1 * width, 1),
        round(region[1] + y1 * height, 1),
    ]


def union(boxes: list[Box]) -> Box:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def pad(box: Box, width: int, height: int, fraction: float, minimum: float) -> Box:
    """`box` grown on all sides, inside the sheet. The model's boxes are approximate, so a crop needs room around them."""
    dx = max((box[2] - box[0]) * fraction, minimum)
    dy = max((box[3] - box[1]) * fraction, minimum)
    return [max(0.0, box[0] - dx), max(0.0, box[1] - dy), min(float(width), box[2] + dx), min(float(height), box[3] + dy)]


def long_side(box: Box) -> float:
    return max(box[2] - box[0], box[3] - box[1])


def overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def center_inside(a: Box, b: Box) -> bool:
    """Whether the centre of `a` is in `b`."""
    x, y = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]
