from pathlib import Path

# Fix backend constants fallbacks to real local files
p = Path(r"E:\repos\pinkbakes_backend\catalog\constants.py")
text = p.read_text(encoding="utf-8")
new_block = """# Local FE public static cake images (category-matched). Collab 3D uses product.image.
CATEGORY_FALLBACK_IMAGES = {
    'Birthday Cakes': '/products/cakes/BirthdayCakes_BerryMedley.jpg',
    'Anniversary Cakes': '/products/cakes/AnniversaryCakes_BlackForest.jpg',
    'Wedding Cakes': '/products/cakes/WeddingCakes_AlmondMarzipan.jpg',
    'Chocolate Cakes': '/products/cakes/ChocolateCakes_BlackForest2.jpg',
    'Designer Cakes': '/products/cakes/DesignerCakes_BerryMedley.jpg',
    'Photo Cakes': '/products/cakes/PhotoCakes_AlmondMarzipan.jpg',
    'Custom Cakes': '/products/cakes/CustomCakes_BerryMedley.jpg',
    'Eggless Cakes': '/products/cakes/EgglessCakes_BerryMedley.jpg',
}
"""
start = text.find("CATEGORY_FALLBACK_IMAGES")
if start < 0:
    raise SystemExit("CATEGORY_FALLBACK_IMAGES not found")
# find comment start a few lines above
comment = text.rfind("\n#", 0, start)
if comment < 0:
    comment = start
else:
    comment += 1  # skip leading newline already handled
# find end of dict
end = text.find("}\n", start)
if end < 0:
    raise SystemExit("end not found")
end += 2
# include preceding comment line(s) belonging to this block
line_start = text.rfind("\n", 0, start) 
# walk back over comment lines
block_start = start
while True:
    prev_nl = text.rfind("\n", 0, block_start)
    if prev_nl < 0:
        break
    line = text[prev_nl + 1 : block_start]
    if line.strip().startswith("#") or not line.strip():
        block_start = prev_nl + 1
        # continue if blank or comment
        if not line.strip().startswith("#") and line.strip():
            break
        continue
    break
# simpler: replace from CATEGORY_FALLBACK through closing brace
import re
pat = re.compile(
    r"(?:# [^\n]*\n)?CATEGORY_FALLBACK_IMAGES\s*=\s*\{.*?\}\n",
    re.S,
)
m = pat.search(text)
if not m:
    raise SystemExit("regex miss")
text2 = text[: m.start()] + new_block + text[m.end() :]
p.write_text(text2, encoding="utf-8")
print("constants OK")
print(p.read_text(encoding="utf-8"))
