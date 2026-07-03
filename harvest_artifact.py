#!/usr/bin/env python3
# GitHub Actions runner(海外IP)直连官网抓 → webp+PDF → out/(供upload-artifact) → 本地拉→123
# 不存R2。源: NDL IIIF(runner海外直连,不封)。验证版
import os, sys, io, json, argparse, time, requests
from PIL import Image
import img2pdf

UA = {"User-Agent": "Mozilla/5.0 (compatible; GujiArchive/1.0)"}
def fetch(s, url, timeout=120, tries=3):
    for a in range(tries):
        try:
            r = s.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200: return r.content
        except Exception: pass
        time.sleep(2 * (a + 1))
    return None

def one(s, iid, out, manifest_url=None):
    mu = manifest_url or f"https://dl.ndl.go.jp/api/iiif/{iid}/manifest.json"
    mf = fetch(s, mu, 60)
    if not mf: print(f"{iid}: manifest失败", flush=True); return
    m = json.loads(mf)
    cvs = m.get("sequences", [{}])[0].get("canvases", [])
    title = m.get("label", iid)
    imgs = []
    for c in cvs:
        iu = c.get("images", [{}])[0].get("resource", {}).get("@id", "")
        if not iu: continue
        b = fetch(s, iu)
        if b and len(b) > 2048: imgs.append(b)
    if not imgs: print(f"{iid}: 零页", flush=True); return
    d = os.path.join(out, iid); os.makedirs(d, exist_ok=True)
    # webp散页
    for i, b in enumerate(imgs, 1):
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"): im = im.convert("RGB")
            im.save(os.path.join(d, f"page_{i:04d}.webp"), "WEBP", quality=82)
        except Exception: pass
    # PDF
    jpgs = []
    for b in imgs:
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"): im = im.convert("RGB")
            bio = io.BytesIO(); im.save(bio, "JPEG", quality=88); jpgs.append(bio.getvalue())
        except Exception: pass
    with open(os.path.join(d, f"{iid}.pdf"), "wb") as f:
        f.write(img2pdf.convert(jpgs))
    # 元数据(供本地归位命名)
    json.dump({"id": iid, "title": title, "pages": len(imgs)},
              open(os.path.join(d, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{iid}: {len(imgs)}页 webp+PDF ✅", flush=True)

if __name__ == "__main__":
    import csv
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--worklist", default="worklist_ndl.csv")
    ap.add_argument("--shard", type=int, default=-1)
    ap.add_argument("--total", type=int, default=1)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    s = requests.Session()
    jobs = []
    if a.shard >= 0 and os.path.exists(a.worklist):
        rows = list(csv.DictReader(open(a.worklist, encoding="utf-8")))
        # 内阁按番号分片(同书所有冊同shard,整本原子不跳跃);无num则按行
        def shard_of(i, r):
            num = r.get("num", "")
            return (sum(ord(c) for c in num) % a.total) if num else (i % a.total)
        jobs = [(r["id"], r.get("manifest") or None) for i, r in enumerate(rows) if shard_of(i, r) == a.shard]
        # 保持worklist番号顺序(流水式一本本连续,不打乱番号序)
        print(f"shard {a.shard}/{a.total}: 分到 {len(jobs)} 本", flush=True)
    elif a.ids:
        jobs = [(x.strip(), None) for x in a.ids.split(",") if x.strip()]
    for iid, mu in jobs:
        one(s, iid, a.out, mu)
