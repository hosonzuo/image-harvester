# -*- coding: utf-8 -*-
"""航母 B 版·内閣子部
- 走 /img/{aid} → najContentList → IIIF Image API
- 4-tile 拼接原图 · resize 4000w · JPEG q80 · ~1.5MB/页
- 每 runner 一 shard · 输出 PDF 到 out/{夹名}/
"""
import os, sys, io, csv, re, json, time, argparse, urllib.request, urllib.parse
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
try:
    sys.stdout.reconfigure(encoding='utf-8')
except: pass

CAP = 3000
TARGET_W = 4000
JPEG_Q = 80
SLEEP_TILE = 0.3
SLEEP_PAGE = 0.8
UA = 'Mozilla/5.0 (compatible; naikaku-harvest-b/1.0)'

def http(url, timeout=90):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return urllib.request.urlopen(req, timeout=timeout).read()

def tile_plan(W, H, cap=CAP):
    cols = (W + cap - 1) // cap
    rows = (H + cap - 1) // cap
    tw = (W + cols - 1) // cols
    th = (H + rows - 1) // rows
    out = []
    for r in range(rows):
        y = r * th; h = min(th, H - y)
        for c in range(cols):
            x = c * tw; w = min(tw, W - x)
            out.append((x, y, w, h))
    return out

def fetch_page(iiif_path):
    info = json.loads(http(f'https://www.digital.archives.go.jp{iiif_path}/info.json'))
    W, H = info['width'], info['height']
    tiles = tile_plan(W, H)
    if len(tiles) == 1 and W <= TARGET_W:
        return http(f'https://www.digital.archives.go.jp{iiif_path}/full/max/0/native.jpg')
    canvas = Image.new('RGB', (W, H), 'white')
    for x, y, w, h in tiles:
        u = f'https://www.digital.archives.go.jp{iiif_path}/{x},{y},{w},{h}/{w},/0/native.jpg'
        canvas.paste(Image.open(io.BytesIO(http(u, timeout=60))), (x, y))
        time.sleep(SLEEP_TILE)
    if W > TARGET_W:
        canvas = canvas.resize((TARGET_W, int(H*TARGET_W/W)), Image.LANCZOS)
    buf = io.BytesIO()
    canvas.save(buf, 'JPEG', quality=JPEG_Q, optimize=True)
    return buf.getvalue()

def download_book(aid, out_pdf):
    html = http(f'https://www.digital.archives.go.jp/img/{aid}').decode('utf-8', errors='replace')
    m = re.search(r'var najContentList = (\[[\s\S]*?\]);', html)
    if not m: raise RuntimeError('no najContentList')
    lst = json.loads(m.group(1))
    total = len(lst)
    pages = []
    for i, item in enumerate(lst):
        pages.append(fetch_page(item['path']))
        if (i+1) % 10 == 0:
            print(f'    aid={aid} p{i+1}/{total}', flush=True)
        time.sleep(SLEEP_PAGE)
    import img2pdf
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    tmp = out_pdf + '.tmp'
    with open(tmp, 'wb') as f: f.write(img2pdf.convert(pages))
    os.replace(tmp, out_pdf)
    return total, os.path.getsize(out_pdf)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--worklist', required=True)
    p.add_argument('--shard', type=int, required=True)
    p.add_argument('--total', type=int, required=True)
    p.add_argument('--shard-start', type=int, default=0, help='加到 shard 上·三号铺开用')
    p.add_argument('--out', default='out')
    a = p.parse_args()

    rows = list(csv.DictReader(open(a.worklist, encoding='utf-8-sig')))
    real_shard = a.shard + a.shard_start
    my = [r for i, r in enumerate(rows) if i % a.total == real_shard]
    print(f'shard {real_shard}/{a.total} (arg={a.shard}+offset={a.shard_start}) · {len(my)}/{len(rows)} 本', flush=True)

    ok, fail, skip = 0, 0, 0
    t0 = time.time()
    for j, r in enumerate(my):
        num = r['num']; folder = r['folder']
        aids = r['aids'].split(',')  # aid:ce,aid:ce,...
        book_dir = os.path.join(a.out, folder)
        for ac in aids:
            aid_s, ce_s = ac.split(':')
            aid = int(aid_s); ce = int(ce_s)
            fname = f'{folder} 冊{ce:02d} 國立公文書館.pdf'
            out_pdf = os.path.join(book_dir, fname)
            if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 8*1024*1024:
                skip += 1; continue
            try:
                pages, sz = download_book(aid, out_pdf)
                ok += 1
                print(f'  [{j+1}/{len(my)}] {num} 冊{ce:02d} aid={aid} · {pages}p · {sz//1024//1024}MB · ok={ok} fail={fail} skip={skip}', flush=True)
            except Exception as e:
                fail += 1
                print(f'  [{j+1}/{len(my)}] {num} 冊{ce:02d} aid={aid} FAIL: {str(e)[:80]}', flush=True)
                time.sleep(3)
        time.sleep(2)  # 本本冷却
    print(f'\nshard {a.shard} 完 · ok={ok} fail={fail} skip={skip} · 用时 {int(time.time()-t0)}s', flush=True)
