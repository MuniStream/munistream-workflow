"""
Static map images for geo fields in entity documents.

Draws a pin (points) or the perimeter (polygons) over raster tiles and returns
a JPEG data URL, so the same image works in the HTML preview (iframe srcDoc)
and in the WeasyPrint PDF without either one fetching anything. If the tile
server cannot be reached the shape is drawn over a plain background: the
document still shows the geometry, only without the basemap.
"""
import asyncio
import base64
import io
import logging
import math
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from PIL import Image, ImageDraw, ImageFont

from ...core.config import settings
from .structured_fields import GEO_POINT_TYPES, is_geometry, parse_structured

logger = logging.getLogger(__name__)

TILE_SIZE = 256
# Output size in CSS pixels; rendered at SCALE for a sharp print.
WIDTH, HEIGHT = 640, 300
SCALE = 2
MAX_ZOOM = 18
POINT_ZOOM = 15
PADDING = 0.15
SHAPE_COLOR = (159, 34, 65)  # gob.mx guinda, readable over any basemap
MAX_MAPS_PER_DOCUMENT = 6

_TILE_CACHE: "OrderedDict[Tuple[int, int, int], Optional[Image.Image]]" = OrderedDict()
_TILE_CACHE_MAX = 512


def _project(lng: float, lat: float, zoom: int) -> Tuple[float, float]:
    """Web Mercator: (lng, lat) to global pixel coordinates at `zoom`."""
    lat = max(min(lat, 85.0511), -85.0511)
    world = TILE_SIZE * (2 ** zoom)
    x = (lng + 180.0) / 360.0 * world
    sin = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * world
    return x, y


def _rings(geometry: Dict[str, Any]) -> List[List[Tuple[float, float]]]:
    """Outer rings of a (Multi)Polygon, or single-point 'rings' for points."""
    coords = geometry["coordinates"]
    kind = geometry["type"]
    if kind == "Point":
        return [[(float(coords[0]), float(coords[1]))]]
    if kind == "MultiPoint":
        return [[(float(c[0]), float(c[1]))] for c in coords]
    polygons = [coords] if kind == "Polygon" else coords
    return [[(float(c[0]), float(c[1])) for c in polygon[0]] for polygon in polygons if polygon and polygon[0]]


def fit_view(points: Iterable[Tuple[float, float]], width: int, height: int,
             single_point: bool) -> Tuple[int, float, float]:
    """Zoom and center (global pixels at that zoom) that frame every point."""
    pts = list(points)
    if single_point or len(pts) == 1:
        zoom = POINT_ZOOM
    else:
        zoom = MAX_ZOOM
        while zoom > 1:
            xs, ys = zip(*(_project(lng, lat, zoom) for lng, lat in pts))
            if (max(xs) - min(xs)) <= width * (1 - 2 * PADDING) and (max(ys) - min(ys)) <= height * (1 - 2 * PADDING):
                break
            zoom -= 1
    xs, ys = zip(*(_project(lng, lat, zoom) for lng, lat in pts))
    return zoom, (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2


async def _fetch_tile(client: httpx.AsyncClient, z: int, x: int, y: int) -> Optional[Image.Image]:
    key = (z, x, y)
    if key in _TILE_CACHE:
        _TILE_CACHE.move_to_end(key)
        return _TILE_CACHE[key]
    url = settings.MAP_TILE_URL.format(z=z, x=x, y=y)
    try:
        response = await client.get(url)
        response.raise_for_status()
        tile = Image.open(io.BytesIO(response.content)).convert("RGB")
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Map tile %s unavailable: %s", key, exc)
        return None
    _TILE_CACHE[key] = tile
    while len(_TILE_CACHE) > _TILE_CACHE_MAX:
        _TILE_CACHE.popitem(last=False)
    return tile


async def _basemap(zoom: int, cx: float, cy: float, width: int, height: int) -> Tuple[Image.Image, bool]:
    """Stitch the tiles covering the view. Returns (image, has_tiles)."""
    canvas = Image.new("RGB", (width, height), (236, 239, 236))
    if not settings.MAP_TILES_ENABLED:
        return canvas, False
    left, top = cx - width / 2, cy - height / 2
    n = 2 ** zoom
    tiles = [
        (tx, ty)
        for tx in range(int(left // TILE_SIZE), int((left + width) // TILE_SIZE) + 1)
        for ty in range(int(top // TILE_SIZE), int((top + height) // TILE_SIZE) + 1)
        if 0 <= ty < n
    ]
    async with httpx.AsyncClient(
        timeout=settings.MAP_TILE_TIMEOUT_SECONDS,
        headers={"User-Agent": settings.MAP_TILE_USER_AGENT},
    ) as client:
        images = await asyncio.gather(*(_fetch_tile(client, zoom, tx % n, ty) for tx, ty in tiles))
    placed = 0
    for (tx, ty), tile in zip(tiles, images):
        if tile is not None:
            canvas.paste(tile, (round(tx * TILE_SIZE - left), round(ty * TILE_SIZE - top)))
            placed += 1
    return canvas, placed > 0


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow without FreeType sizing
        return ImageFont.load_default()


def _draw_pin(draw: ImageDraw.ImageDraw, x: float, y: float, s: int) -> None:
    """Teardrop pin whose tip sits on (x, y)."""
    r = 9 * s
    head_y = y - 2.4 * r
    draw.polygon([(x, y), (x - r * 0.82, head_y + r * 0.55), (x + r * 0.82, head_y + r * 0.55)], fill=SHAPE_COLOR)
    draw.ellipse([x - r, head_y - r, x + r, head_y + r], fill=SHAPE_COLOR, outline="white", width=max(1, s))
    draw.ellipse([x - r * 0.38, head_y - r * 0.38, x + r * 0.38, head_y + r * 0.38], fill="white")


async def render_geometry_map(geometry: Any) -> Optional[str]:
    """JPEG data URL with the geometry drawn over a basemap, or None."""
    geometry = parse_structured(geometry)
    if not is_geometry(geometry):
        return None
    try:
        rings = _rings(geometry)
    except (TypeError, ValueError, IndexError):
        return None
    points = [p for ring in rings for p in ring]
    if not points:
        return None

    s = SCALE
    width, height = WIDTH * s, HEIGHT * s
    is_point = geometry["type"] in GEO_POINT_TYPES
    # Fit at 1x, then render one zoom level deeper so tiles stay sharp at 2x.
    zoom, cx, cy = fit_view(points, WIDTH, HEIGHT, single_point=is_point and len(points) == 1)
    zoom = min(zoom + 1, MAX_ZOOM + 1)
    cx, cy = cx * 2, cy * 2
    image, has_tiles = await _basemap(zoom, cx, cy, width, height)

    def to_px(lng: float, lat: float) -> Tuple[float, float]:
        gx, gy = _project(lng, lat, zoom)
        return gx - cx + width / 2, gy - cy + height / 2

    if is_point:
        draw = ImageDraw.Draw(image)
        for lng, lat in points:
            _draw_pin(draw, *to_px(lng, lat), s)
    else:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        for ring in rings:
            px = [to_px(lng, lat) for lng, lat in ring]
            if len(px) >= 3:
                odraw.polygon(px, fill=SHAPE_COLOR + (60,))
                odraw.line(px + [px[0]], fill=SHAPE_COLOR + (255,), width=3 * s, joint="curve")
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)
        for ring in rings:
            for lng, lat in ring:
                x, y = to_px(lng, lat)
                r = 3 * s
                draw.ellipse([x - r, y - r, x + r, y + r], fill="white", outline=SHAPE_COLOR, width=s)

    if has_tiles and settings.MAP_TILE_ATTRIBUTION:
        font = _font(10 * s)
        text = settings.MAP_TILE_ATTRIBUTION
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        pad = 3 * s
        x0, y0 = width - tw - 2 * pad, height - th - 2 * pad
        draw.rectangle([x0, y0, width, height], fill=(255, 255, 255))
        draw.text((x0 + pad, y0 + pad - box[1]), text, fill=(60, 60, 60), font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


async def build_field_maps(data: Dict[str, Any], keys: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """Map images for the geo fields of an entity, keyed by field name."""
    if not data:
        return {}
    candidates = [k for k in (keys if keys is not None else data.keys()) if is_geometry(parse_structured(data.get(k)))]
    candidates = candidates[:MAX_MAPS_PER_DOCUMENT]
    results = await asyncio.gather(*(render_geometry_map(data[k]) for k in candidates), return_exceptions=True)
    maps = {}
    for key, result in zip(candidates, results):
        if isinstance(result, Exception):
            logger.warning("Map for field %s failed: %s", key, result)
        elif result:
            maps[key] = result
    return maps
