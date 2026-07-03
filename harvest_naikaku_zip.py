#!/usr/bin/env python3
# runner 直连内阁 contentDownload 整件ZIP → webp+PDF
# 大部头超快(1请求≤100页 vs IIIF逐页几百请求) · 80个runner IP分摊 · 绕worker egress不顶封
# 流程: GET /img/{id} → najContentList → 分块POST /contentDownload → 解压JPG → webp+PDF
import os, sys, io, re, json, time, argparse, zipfile, requests
from PIL import Image
import img2pdf

H = "www.digital.archives.go.jp"
BH = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ja,en;q=0.8", "Accept": "*/*"}

def fetch_zip_pages(s, iid):
    """整件ZIP抓全本 → [jpg字节,...]。分块≤100页(server limit),失败重试。"""
    try:
        vp = s.get(f"https://{H}/img/{iid}", headers=BH, timeout=60)
        if vp.status_code != 200: return None
        m = re.search(r'var najContentList = (\[[\s\S]*?\]);', vp.text)
        if not m: return None
        lst = json.loads(m.group(1))
    except Exception:
        return None
    total = len(lst)
    if not total: return None
    imgs = []
    for start in range(0, total, 100):
        sel = lst[start:start + 100]
        body = "&".join("cid=" + requests.utils.quote("da12/" + str(x["id"])) for x in sel)
        ok = False
        for attempt in range(4):
            try:
                dl = s.post(f"https://{H}/contentDownload/{iid}?type=imageJpeg", data=body,
                            headers={**BH, "Content-Type": "application/x-www-form-urlencoded",
                                     "Referer": f"https://{H}/img/{iid}", "Origin": f"https://{H}"}, timeout=240)
                if dl.status_code == 403:
                    time.sleep(30); continue   # 万一顶封,退避
                if dl.status_code == 200 and "zip" in dl.headers.get("Content-Type", "").lower():
                    z = zipfile.ZipFile(io.BytesIO(dl.content))
                    for n in sorted(n for n in z.namelist() if not n.endswith("/")):
                        imgs.append(z.read(n))
                    ok = True; break
            except Exception:
                pass
            time.sleep(3 * (attempt + 1))
        if not ok: return None   # 某块失败=整本残,弃(下轮重抓)
        time.sleep(0.4)          # 块间温和间隔
    return imgs

def one(s, iid, out):
    imgs = fetch_zip_pages(s, iid)
    if not imgs:
        print(f"{iid}: 整件失败", flush=True); return
    d = os.path.join(out, iid); os.makedirs(d, exist_ok=True)
    for i, b in enumerate(imgs, 1):
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"): im = im.convert("RGB")
            im.save(os.path.join(d, f"page_{i:04d}.webp"), "WEBP", quality=82)
        except Exception:
            pass
    jpgs = []
    for b in imgs:
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"): im = im.convert("RGB")
            bio = io.BytesIO(); im.save(bio, "JPEG", quality=88); jpgs.append(bio.getvalue())
        except Exception:
            pass
    with open(os.path.join(d, f"{iid}.pdf"), "wb") as f:
        f.write(img2pdf.convert(jpgs))
    json.dump({"id": iid, "pages": len(imgs)},
              open(os.path.join(d, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{iid}: {len(imgs)}页 整件ZIP→webp+PDF ✅", flush=True)

if __name__ == "__main__":
    import csv
    ap = argparse.ArgumentParser()
    ap.add_argument("--worklist", default="worklist_naikaku.csv")
    ap.add_argument("--shard", type=int, default=-1)
    ap.add_argument("--total", type=int, default=1)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    s = requests.Session()
    rows = list(csv.DictReader(open(a.worklist, encoding="utf-8")))
    def shard_of(i, r):
        num = r.get("num", "")
        return (sum(ord(c) for c in num) % a.total) if num else (i % a.total)
    jobs = [r["id"] for i, r in enumerate(rows) if shard_of(i, r) == a.shard]
    print(f"shard {a.shard}/{a.total}: 分到 {len(jobs)} 本(整件ZIP)", flush=True)
    for iid in jobs:
        one(s, iid, a.out)
