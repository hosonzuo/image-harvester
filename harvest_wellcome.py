#!/usr/bin/env python3
# 惠康医学史图书馆(Wellcome Collection) GitHub Actions runner 直连抓取 → webp+PDF → out/(供 upload-artifact)
# 不存R2/D1(与内阁 hv.py/worker.py 完全独立,不碰主力)。复制自 harvest_artifact.py 改造。
# 侦察报告: F:\0book\0采集\r2_guji_123\_wellcome侦察报告.md
#
# 惠康专属改造点(勿混淆内阁/NDL 逻辑):
#   1. 图片 URL 不能直接用 manifest 内嵌 resource.@id(那是预生成缩略图 863x1024)——
#      必须取 canvas.images[].resource.service.@id 自拼 /full/max/0/default.jpg 才是原图(3094x3670实测)。
#   2. 官方 robots.txt 明示 Crawl-delay:30 / Request-rate:1/30s —— 每取一图后必 sleep,
#      按 shard 数分摊(如 4 shard 则每请求约 sleep 8s,合计≈站方节奏,不猛冲)。
import os, sys, io, json, argparse, time, requests
from PIL import Image
import img2pdf

UA = {"User-Agent": "Mozilla/5.0 (compatible; research-harvest/1.0; contact:hosonzuo@gmail.com)"}


def fetch(s, url, timeout=120, tries=3):
    for a in range(tries):
        try:
            r = s.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r.content
        except Exception:
            pass
        time.sleep(2 * (a + 1))
    return None


def image_url_of(canvas):
    """惠康坑: manifest 内嵌 resource.@id 是预生成缩略图, 必须取 service.@id 自拼 /full/max/0/default.jpg"""
    res = (canvas.get("images") or [{}])[0].get("resource", {})
    svc = res.get("service") or {}
    sid = svc.get("@id") if isinstance(svc, dict) else None
    if sid:
        return f"{sid}/full/max/0/default.jpg"
    return res.get("@id", "")  # 兜底(理论上不会走到,manifest 都带 service)


def one(s, iid, out, manifest_url=None, page_sleep=8.0):
    mu = manifest_url or f"https://iiif.wellcomecollection.org/presentation/v2/{iid}"
    mf = fetch(s, mu, 60)
    if not mf:
        print(f"{iid}: manifest失败", flush=True)
        return
    m = json.loads(mf)
    cvs = m.get("sequences", [{}])[0].get("canvases", [])
    title = m.get("label", iid)
    imgs = []
    for c in cvs:
        iu = image_url_of(c)
        if not iu:
            continue
        b = fetch(s, iu)
        if b and len(b) > 2048:
            imgs.append(b)
        time.sleep(page_sleep)  # 官方 Crawl-delay:30 限速, 按 shard 数分摊后每请求 sleep
    if not imgs:
        print(f"{iid}: 零页", flush=True)
        return
    d = os.path.join(out, iid)
    os.makedirs(d, exist_ok=True)
    # webp散页
    for i, b in enumerate(imgs, 1):
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(os.path.join(d, f"page_{i:04d}.webp"), "WEBP", quality=82)
        except Exception:
            pass
    # PDF
    jpgs = []
    for b in imgs:
        try:
            im = Image.open(io.BytesIO(b))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            bio = io.BytesIO()
            im.save(bio, "JPEG", quality=88)
            jpgs.append(bio.getvalue())
        except Exception:
            pass
    with open(os.path.join(d, f"{iid}.pdf"), "wb") as f:
        f.write(img2pdf.convert(jpgs))
    # 元数据(供本地归位命名; 惠康 license 由 worklist 传入写回, 供后续合规审计)
    json.dump({"id": iid, "title": title, "pages": len(imgs), "source": "wellcome"},
               open(os.path.join(d, "_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{iid}: {len(imgs)}页 webp+PDF ok", flush=True)


if __name__ == "__main__":
    import csv
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--worklist", default="worklist_wellcome.csv")
    ap.add_argument("--shard", type=int, default=-1)
    ap.add_argument("--total", type=int, default=1)
    ap.add_argument("--out", default="out")
    ap.add_argument("--page-sleep", type=float, default=0.0,
                     help="每取一图后 sleep 秒数; 0=按 total shard 数自动分摊 Crawl-delay:30(即 30/total)")
    a = ap.parse_args()
    s = requests.Session()
    page_sleep = a.page_sleep if a.page_sleep > 0 else max(30.0 / max(a.total, 1), 1.0)
    jobs = []
    if a.shard >= 0 and os.path.exists(a.worklist):
        rows = list(csv.DictReader(open(a.worklist, encoding="utf-8")))
        jobs = [(r["id"], r.get("manifest")) for i, r in enumerate(rows) if i % a.total == a.shard]
        print(f"shard {a.shard}/{a.total}: 分到 {len(jobs)} 本, page_sleep={page_sleep:.1f}s", flush=True)
    elif a.ids:
        jobs = [(x.strip(), None) for x in a.ids.split(",") if x.strip()]
    for iid, mu in jobs:
        one(s, iid, a.out, mu, page_sleep=page_sleep)
