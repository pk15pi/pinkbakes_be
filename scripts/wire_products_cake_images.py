#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinkbakes_backend.settings")

import django
django.setup()

from catalog.models import Product

BE = ROOT / "media" / "products" / "cakes"
FE = Path(r"E:\repos\pinksBakes\public\products\cakes")
manifest = json.loads((BE / "manifest.json").read_text(encoding="utf-8"))

CAT_MAP = {
    "Birthday Cakes": "BirthdayCakes",
    "Anniversary Cakes": "AnniversaryCakes",
    "Wedding Cakes": "WeddingCakes",
    "Chocolate Cakes": "ChocolateCakes",
    "Designer Cakes": "DesignerCakes",
    "Photo Cakes": "PhotoCakes",
    "Custom Cakes": "CustomCakes",
    "Eggless Cakes": "EgglessCakes",
}

by_cat = defaultdict(list)
for img in manifest.get("images") or []:
    cat = img.get("category")
    fn = img.get("filename")
    if not cat or not fn:
        continue
    if not (BE / fn).is_file() or not (FE / fn).is_file():
        continue
    by_cat[cat].append(fn)

for k in list(by_cat.keys()):
    by_cat[k] = sorted(set(by_cat[k]))

print("files per cat:", {k: len(v) for k, v in by_cat.items()})

updated = 0
rr = defaultdict(int)
for p in Product.objects.all().order_by("id"):
    prefix = CAT_MAP.get(p.category)
    if not prefix:
        print(f"SKIP id={p.id} unknown category {p.category!r}")
        continue
    files = by_cat.get(prefix) or []
    if not files:
        print(f"SKIP id={p.id} no files for {prefix}")
        continue
    idx = rr[prefix] % len(files)
    rr[prefix] += 1
    gallery = [f"/products/cakes/{files[(idx + j) % len(files)]}" for j in range(3)]
    # dedupe preserve order
    seen = set()
    gallery2 = []
    for g in gallery:
        if g not in seen:
            seen.add(g)
            gallery2.append(g)
    main = f"/products/cakes/{files[idx]}"
    p.main_image = main
    p.images = gallery2
    p.save(update_fields=["main_image", "images"])
    updated += 1
    print(f"OK id={p.id} {p.category} -> {main}")

print(f"UPDATED {updated}/{Product.objects.count()}")

remote = 0
missing = 0
for p in Product.objects.all():
    if (p.main_image or "").startswith("http"):
        remote += 1
    fe_check = Path(r"E:\repos\pinksBakes\public") / Path(*p.main_image.strip("/").split("/"))
    be_check = BE / Path(p.main_image).name
    if not fe_check.is_file() or not be_check.is_file():
        missing += 1
        print("MISSING", p.id, p.main_image)
    for g in (p.images or []):
        if str(g).startswith("http"):
            remote += 1
print("remote_urls", remote, "missing_files", missing)
