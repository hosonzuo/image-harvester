#!/usr/bin/env python3
# 支线:内閣通过 37 自定义域轮换加速 fetch(runner IP + 37域轮换 = 抗封)
# 落 out/{iid}/{page_XXXX.webp + {iid}.pdf + _meta.json}·供 upload-artifact
import os, sys, io, json, argparse, time, csv, requests
from urllib.parse import quote
from PIL import Image
import img2pdf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36", "Accept-Language": "ja,en;q=0.8"}
DOMAINS = os.environ.get("DOMAIN_POOL", "").split(",")
DOMAINS = [d.strip() for d in DOMAINS if d.strip()]

def fetch(s, url, tries=2, timeout=90):
    # 优先直连(runner海外IP大概率通);挂了轮域
    for a in range(tries):
        try:
            r = s.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 500: return r.content
        except Exception: pass
    for i, d in enumerate(DOMAINS):
        try:
            r = s.get(f"https://{d}/fetch?url=" + quote(url, safe=""), headers=UA, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 500: return r.content
        except Exception: pass
    return None

def one(s, iid, out, manifest_url=None):
    mu = manifest_url or f"https://www.digital.archives.go.jp/api/iiif/{iid}/manifest.json"
    mf = fetch(s, mu, tries=3)
    if not mf: print(f"{iid}: manifest失败", flush=True); return
    try: m = json.loads(mf)
    except Exception: print(f"{iid}: manifest非json", flush=True); return
    cvs = m.get("sequences", [{}])[0].get("canvases", [])
    title = m.get("label", iid)
    if isinstance(title, list): title = title[0] if title else iid
    imgs = []
    for c in cvs:
        iu = c.get("images", [{}])[0].get("resource", {}).get("@id", "")
        if not iu: continue
        # IIIF Image API: /full/max/0/default.jpg 完整图
        if not iu.endswith("/default.jpg"):
            iu = iu.rstrip("/") + "/full/max/0/default.jpg"
        b = fetch(s, iu)
        if b and len(b) > 2048: imgs.append(b)
    if not imgs: print(f"{iid}: 零页", flush=True); return
    d = os.path.join(out, iid); os.makedirs(d, exist_ok=True)
    for i, b in enumerate(imgs, 1):
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"): im = im.convert("RGB")
            im.save(os.path.join(d, f"page_{i:04d}.webp"), "WEBP", quality=82)
        except Exception: pass
    jpgs = []
    for b in imgs:
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"): im = im.convert("RGB")
            bio = io.BytesIO(); im.save(bio, "JPEG", quality=88); jpgs.append(bio.getvalue())
        except Exception: pass
    with open(os.path.join(d, f"{iid}.pdf"), "wb") as f:
        f.write(img2pdf.convert(jpgs))
    json.dump({"id": iid, "title": title, "pages": len(imgs)},
              open(os.path.join(d, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{iid}: {len(imgs)}页 webp+PDF OK", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--worklist", default="worklist_naikaku_domain.csv")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--total", type=int, default=1)
    ap.add_argument("--out", default="out")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    s = requests.Session()
    rows = list(csv.DictReader(open(a.worklist, encoding="utf-8")))
    jobs = [r for i, r in enumerate(rows) if i % a.total == a.shard]
    if a.limit: jobs = jobs[:a.limit]
    print(f"shard {a.shard}/{a.total}: {len(jobs)} 本 · 域池{len(DOMAINS)}", flush=True)
    for r in jobs:
        one(s, r["id"], a.out, r.get("manifest") or None)
