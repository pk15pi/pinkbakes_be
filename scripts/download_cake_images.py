#!/usr/bin/env python3
"""Download high-res cake product images for PinkBakes (Unsplash / Pexels only).

Usage:
  set UNSPLASH_ACCESS_KEY=...   # https://unsplash.com/developers
  set PEXELS_API_KEY=...        # https://www.pexels.com/api/
  python scripts/download_cake_images.py

Without keys: uses curated_cake_ids.json (+ inline fallbacks), verifies HTTP 200,
skips 404s, downloads as many as available (target ~500).

Outputs:
  media/products/cakes/*.jpg
  media/products/cakes/manifest.json
  mirrored to FE public/products/cakes/ when present
"""
from __future__ import annotations

import concurrent.futures
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Pillow required: pip install pillow", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "media" / "products" / "cakes"
FE_MIRROR = Path(r"E:\repos\pinksBakes\public\products\cakes")
MANIFEST_PATH = OUT_DIR / "manifest.json"
IDS_JSON = Path(__file__).with_name("curated_cake_ids.json")

TARGET_TOTAL = 1000
PER_CATEGORY = 125
MAX_EDGE = 1200
JPEG_QUALITY = 84
CONCURRENCY = 6
MIN_SOURCE_EDGE = 400
UA = "PinkBakesCakeDownloader/1.0 (product catalog; +https://pinkbakes.com)"

CATEGORIES = [
    "BirthdayCakes",
    "AnniversaryCakes",
    "WeddingCakes",
    "ChocolateCakes",
    "DesignerCakes",
    "PhotoCakes",
    "CustomCakes",
    "EgglessCakes",
]

TYPE_SLUGS = [
    "VanillaButtercream", "ChocolateGanache", "StrawberryCream", "RedVelvet",
    "BlackForest", "LemonZest", "CaramelDrizzle", "BlueberryCompote",
    "OreoCrunch", "MangoMousse", "PineappleDelight", "CoffeeMocha",
    "WhiteChocolate", "RaspberryRose", "HazelnutPraline", "CoconutCream",
    "PistachioDream", "BerryMedley", "FudgeLayer", "TiramisuStyle",
    "FunfettiSprinkle", "SaltedCaramel", "MatchaGreen", "PassionFruit",
    "CherryAmaretto", "PeanutButterCup", "CookiesCream", "BananaWalnut",
    "OrangeBlossom", "LavenderHoney", "AlmondMarzipan", "TruffleTower",
    "MarbleSwirl", "ChocoChip", "VelvetBerry", "GoldenButter",
    "IvoryFondant", "FloralCascade", "GoldLeaf", "SilverPearl",
    "HeartTopper", "CandleGlow", "PhotoPrint", "CustomMessage",
    "EgglessVanilla", "EgglessChocolate", "EgglessFruit", "LayerTower",
    "MiniCelebration", "SheetCelebration", "CupcakeTower", "NakedCake",
    "DripChocolate", "MirrorGlaze", "GeodeCrystal", "OmbrePink",
    "GalaxyNight", "UnicornFantasy", "SafariTheme", "PrincessCrown",
    "SportsBall", "CartoonCharacter", "FloralWedding", "ClassicTier",
]

LICENSE_UNSPLASH = "Unsplash License (https://unsplash.com/license) — free to use"
LICENSE_PEXELS = "Pexels License (https://www.pexels.com/license/) — free to use"

_lock = threading.Lock()
_seen_ids: set[str] = set()
_failures: list[dict] = []
_success: list[dict] = []


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def load_dotenv_files() -> None:
    candidates = [
        ROOT / ".env",
        ROOT.parent / "pinksBakes" / ".env",
        ROOT.parent / "pinksBakes" / ".env.local",
        Path(r"E:\repos\pinksBakes\.env"),
        Path(r"E:\repos\pinksBakes\.env.local"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("UNSPLASH_ACCESS_KEY", "PEXELS_API_KEY") and v and k not in os.environ:
                os.environ[k] = v


def http_get(url: str, headers: dict | None = None, timeout: int = 45) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def unsplash_api_collect(access_key: str, need: int) -> list[dict]:
    queries = [
        "birthday cake", "anniversary cake", "wedding cake", "chocolate cake",
        "designer cake", "decorated cake", "custom cake", "eggless cake",
        "layer cake", "fondant cake", "buttercream cake", "red velvet cake",
        "strawberry cake", "vanilla cake", "celebration cake", "tiered cake",
    ]
    out: list[dict] = []
    seen: set[str] = set()
    page = 1
    q_idx = 0
    while len(out) < need and q_idx < len(queries):
        q = queries[q_idx]
        url = (
            "https://api.unsplash.com/search/photos?"
            + urllib.parse.urlencode(
                {"query": q, "per_page": 30, "page": page, "orientation": "squarish"}
            )
        )
        try:
            raw = http_get(url, headers={"Authorization": f"Client-ID {access_key}"})
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            print(f"[unsplash api] query={q!r} page={page} error: {e}")
            q_idx += 1
            page = 1
            time.sleep(1)
            continue
        results = data.get("results") or []
        if not results:
            q_idx += 1
            page = 1
            continue
        for item in results:
            pid = item.get("id") or ""
            if not pid or pid in seen:
                continue
            seen.add(pid)
            urls = item.get("urls") or {}
            src = urls.get("regular") or urls.get("full") or urls.get("raw")
            if not src:
                continue
            out.append(
                {
                    "provider": "unsplash",
                    "photo_id": f"unsplash:{pid}",
                    "source_url": src,
                    "license": LICENSE_UNSPLASH,
                }
            )
            if len(out) >= need:
                break
        page += 1
        if page > 15:
            q_idx += 1
            page = 1
        time.sleep(0.35)
    return out


def pexels_api_collect(api_key: str, need: int) -> list[dict]:
    queries = [
        "birthday cake", "anniversary cake", "wedding cake", "chocolate cake",
        "decorated cake", "fondant cake", "custom cake", "celebration cake",
        "layer cake", "strawberry cake", "buttercream cake", "tiered cake",
    ]
    out: list[dict] = []
    seen: set[str] = set()
    page = 1
    q_idx = 0
    while len(out) < need and q_idx < len(queries):
        q = queries[q_idx]
        url = (
            "https://api.pexels.com/v1/search?"
            + urllib.parse.urlencode({"query": q, "per_page": 80, "page": page})
        )
        try:
            raw = http_get(url, headers={"Authorization": api_key})
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            print(f"[pexels api] query={q!r} page={page} error: {e}")
            q_idx += 1
            page = 1
            time.sleep(1)
            continue
        photos = data.get("photos") or []
        if not photos:
            q_idx += 1
            page = 1
            continue
        for item in photos:
            pid = str(item.get("id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            src_set = item.get("src") or {}
            src = src_set.get("large2x") or src_set.get("large") or src_set.get("original")
            if not src:
                continue
            out.append(
                {
                    "provider": "pexels",
                    "photo_id": f"pexels:{pid}",
                    "source_url": src,
                    "license": LICENSE_PEXELS,
                }
            )
            if len(out) >= need:
                break
        page += 1
        if page > 20:
            q_idx += 1
            page = 1
        time.sleep(0.25)
    return out


def curated_candidates() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    unsplash_ids: list[str] = []
    pexels_ids: list[int] = []
    if IDS_JSON.is_file():
        try:
            data = json.loads(IDS_JSON.read_text(encoding="utf-8-sig"))
            unsplash_ids = list(data.get("unsplash") or [])
            pexels_ids = [int(x) for x in (data.get("pexels") or [])]
            print(f"Loaded {IDS_JSON.name}: unsplash={len(unsplash_ids)} pexels={len(pexels_ids)}")
        except Exception as e:
            print(f"Warning: could not load {IDS_JSON}: {e}")
    for pid in unsplash_ids:
        pid = str(pid).strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        url = f"https://images.unsplash.com/{pid}?auto=format&fit=crop&w=1200&q=85"
        items.append(
            {
                "provider": "unsplash",
                "photo_id": f"unsplash:{pid}",
                "source_url": url,
                "license": LICENSE_UNSPLASH,
            }
        )
    for num in pexels_ids:
        key = f"pexels:{int(num)}"
        if key in seen:
            continue
        seen.add(key)
        pid = str(int(num))
        url = (
            f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg"
            f"?auto=compress&cs=tinysrgb&w=1200"
        )
        items.append(
            {
                "provider": "pexels",
                "photo_id": key,
                "source_url": url,
                "license": LICENSE_PEXELS,
            }
        )
    return items


def resize_jpeg(raw: bytes) -> tuple[bytes, int, int]:
    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGB")
    w, h = im.size
    if max(w, h) < MIN_SOURCE_EDGE:
        raise ValueError(f"source too small: {w}x{h}")
    if max(w, h) > MAX_EDGE:
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
        w, h = im.size
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buf.getvalue(), w, h


def assign_name(category: str, index: int) -> str:
    slug = TYPE_SLUGS[index % len(TYPE_SLUGS)]
    cycle = index // len(TYPE_SLUGS)
    if cycle:
        slug = f"{slug}{cycle + 1}"
    return f"{category}_{slug}.jpg"


def process_one(item: dict, category: str, index: int) -> dict | None:
    pid = item["photo_id"]
    with _lock:
        if pid in _seen_ids:
            return None
        _seen_ids.add(pid)

    filename = assign_name(category, index)
    dest = OUT_DIR / filename
    # Avoid clobbering resume files when index space has gaps
    n = 2
    while dest.exists():
        base = filename.rsplit(".", 1)[0]
        # strip prior numeric suffix like Foo2
        core = base
        filename = f"{core}_v{n}.jpg"
        dest = OUT_DIR / filename
        n += 1
        if n > 50:
            break
    try:
        raw = http_get(item["source_url"])
        if len(raw) < 800:
            raise ValueError("response too small")
        jpeg, w, h = resize_jpeg(raw)
        dest.write_bytes(jpeg)
        FE_MIRROR.mkdir(parents=True, exist_ok=True)
        (FE_MIRROR / filename).write_bytes(jpeg)
        entry = {
            "filename": filename,
            "category": category,
            "type": filename[len(category) + 1 :].rsplit(".", 1)[0],
            "width": w,
            "height": h,
            "bytes": len(jpeg),
            "source_url": item["source_url"],
            "photo_id": pid,
            "provider": item["provider"],
            "license": item["license"],
        }
        with _lock:
            _success.append(entry)
        return entry
    except Exception as e:
        with _lock:
            _failures.append({"photo_id": pid, "url": item["source_url"], "error": str(e)})
        return None


def main() -> int:
    load_dotenv_files()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FE_MIRROR.mkdir(parents=True, exist_ok=True)

    unsplash_key = _env("UNSPLASH_ACCESS_KEY")
    pexels_key = _env("PEXELS_API_KEY")


    # RESUME_FROM_MANIFEST: skip photo_ids already saved; preserve files
    resume_cat_counts = {c: 0 for c in CATEGORIES}
    if MANIFEST_PATH.is_file():
        try:
            prev = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            by_name = {}
            for img in prev.get("images") or []:
                fn = img.get("filename") or ""
                pid = img.get("photo_id")
                if pid:
                    _seen_ids.add(pid)
                if fn and (OUT_DIR / fn).is_file():
                    by_name[fn] = img
            for img in by_name.values():
                _success.append(img)
                cat = img.get("category")
                if cat in resume_cat_counts:
                    resume_cat_counts[cat] += 1
            print(f"Resuming: {len(_success)} files, {len(_seen_ids)} photo_ids")
            print("  existing per category:", resume_cat_counts)
        except Exception as e:
            print(f"Resume load warning: {e}")

    print(f"OUT_DIR={OUT_DIR}")
    print(f"FE_MIRROR={FE_MIRROR}")
    print(f"UNSPLASH_ACCESS_KEY set: {bool(unsplash_key)}")
    print(f"PEXELS_API_KEY set: {bool(pexels_key)}")

    pool: list[dict] = []
    if unsplash_key:
        print("Collecting via Unsplash API...")
        pool.extend(unsplash_api_collect(unsplash_key, TARGET_TOTAL + 80))
        print(f"  unsplash api items: {len(pool)}")
    if pexels_key:
        print("Collecting via Pexels API...")
        before = len(pool)
        pool.extend(pexels_api_collect(pexels_key, TARGET_TOTAL + 80))
        print(f"  pexels api items: {len(pool) - before}")

    curated = curated_candidates()
    print(f"Curated candidates: {len(curated)}")
    have = {x["photo_id"] for x in pool}
    for c in curated:
        if c["photo_id"] not in have:
            pool.append(c)
            have.add(c["photo_id"])
    print(f"Total candidate pool: {len(pool)}")
    if not pool:
        print("No candidates available.", file=sys.stderr)
        return 1

    cat_counts = {c: resume_cat_counts.get(c, 0) for c in CATEGORIES}
    cat_indices = {c: resume_cat_counts.get(c, 0) for c in CATEGORIES}
    # Skip pool items already downloaded
    pool = [x for x in pool if x["photo_id"] not in _seen_ids]
    print(f"Pool after resume filter: {len(pool)}")
    work: list[tuple[dict, str, int]] = []
    pool_i = 0
    while pool_i < len(pool) and sum(cat_counts.values()) < TARGET_TOTAL:
        remaining = [c for c in CATEGORIES if cat_counts[c] < PER_CATEGORY]
        if not remaining:
            break
        cat = min(remaining, key=lambda c: cat_counts[c])
        item = pool[pool_i]
        pool_i += 1
        idx = cat_indices[cat]
        cat_indices[cat] += 1
        cat_counts[cat] += 1
        work.append((item, cat, idx))

    print(f"Planned downloads: {len(work)} (concurrency={CONCURRENCY})")
    remaining_pool = pool[pool_i:]

    def run_batch(batch: list[tuple[dict, str, int]]) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futs = [ex.submit(process_one, it, cat, idx) for it, cat, idx in batch]
            for fut in concurrent.futures.as_completed(futs):
                fut.result()

    # Process in chunks for progress visibility
    chunk = 24
    for i in range(0, len(work), chunk):
        batch = work[i : i + chunk]
        run_batch(batch)
        print(f"  progress: {len(_success)} saved, {len(_failures)} failed/skipped")

    success_by_cat = {c: 0 for c in CATEGORIES}
    for e in _success:
        success_by_cat[e["category"]] += 1

    refill_i = 0
    guard = 0
    while (
        sum(success_by_cat.values()) < TARGET_TOTAL
        and refill_i < len(remaining_pool)
        and guard < 3000
    ):
        guard += 1
        burst: list[tuple[dict, str, int]] = []
        while (
            len(burst) < CONCURRENCY
            and refill_i < len(remaining_pool)
            and sum(success_by_cat.values()) + len(burst) < TARGET_TOTAL
        ):
            needy = [c for c in CATEGORIES if success_by_cat[c] < PER_CATEGORY] or list(
                CATEGORIES
            )
            cat = min(needy, key=lambda c: success_by_cat[c])
            it = remaining_pool[refill_i]
            refill_i += 1
            idx = cat_indices[cat]
            cat_indices[cat] += 1
            burst.append((it, cat, idx))
        if not burst:
            break
        run_batch(burst)
        success_by_cat = {c: 0 for c in CATEGORIES}
        for e in _success:
            success_by_cat[e["category"]] += 1
        if guard % 5 == 0:
            print(f"  refill progress: {len(_success)} saved")

    # Dedupe by filename (resume may accumulate)
    dedup = {}
    for e in _success:
        dedup[e["filename"]] = e
    _success.clear()
    _success.extend(dedup.values())

    success_by_cat = {c: 0 for c in CATEGORIES}
    for e in _success:
        success_by_cat[e["category"]] += 1

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": len(_success),
        "per_category": success_by_cat,
        "rules": {
            "max_edge_px": MAX_EDGE,
            "jpeg_quality": JPEG_QUALITY,
            "naming": "CategoryName_typeOfCake.jpg",
            "sources": "Unsplash License and/or Pexels License only",
            "cake_only": True,
            "query_policy": "cake-specific queries only; no bakery/dessert/pastry/cupcake/bread/cookie/donut searches",
            "curated_filter": "curated_cake_ids.json cake-only rebuild; Pexels slug contains cake; non-cake Unsplash discarded",
        },
        "images": sorted(_success, key=lambda x: (x["category"], x["filename"])),
        "failures_count": len(_failures),
        "failures_sample": _failures[:50],
        "api_keys_used": {"unsplash": bool(unsplash_key), "pexels": bool(pexels_key)},
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    FE_MIRROR.mkdir(parents=True, exist_ok=True)
    (FE_MIRROR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("==== SUMMARY ====")
    print(f"Total saved: {len(_success)}")
    for c in CATEGORIES:
        print(f"  {c}: {success_by_cat[c]}")
    print(f"Failures/skips: {len(_failures)}")
    print(f"Manifest: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

