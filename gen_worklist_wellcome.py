#!/usr/bin/env python3
# 惠康医学史图书馆(Wellcome Collection)worklist 生成器
# 侦察报告: F:\0book\0采集\r2_guji_123\_wellcome侦察报告.md
# 链路: Catalogue API 关键词枚举(workType=h 手稿类) -> 逐 work ?include=items 解析
#       items[].locations 找 locationType=iiif-presentation 的 url(真实 manifest URL)
#       -> license 白名单过滤 -> probe manifest canvas>=1 防空壳 -> 去重 -> worklist_wellcome.csv
#
# 铁律(SOP): 认书靠 manifest,不靠文件名/work标题瞎猜; license 白名单只收 pdm/cc0/cc-by/cc-by-nc;
#             worklist 零中文(public仓 opsec); Crawl-delay:30 官方限速,枚举阶段也要克制请求节奏。
import os, sys, csv, json, time, argparse, urllib.parse, urllib.request

CATALOGUE = "https://api.wellcomecollection.org/catalogue/v2/works"
UA = {"User-Agent": "Mozilla/5.0 (compatible; research-harvest/1.0; contact:hosonzuo@gmail.com)"}
LICENSE_WHITELIST = {"pdm", "cc0", "cc-by", "cc-by-nc"}

KEYWORDS = [
    "materia medica",
    "herbal manuscript",
    "Avicenna",
    "Unani medicine",
    "Ayurveda manuscript",
    "acupuncture",
    "Chinese medicine manuscript",
]

PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or ""


def _opener():
    if PROXY:
        h = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        return urllib.request.build_opener(h)
    return urllib.request.build_opener()


OPENER = _opener()


def http_get_json(url, timeout=30, tries=3, sleep_s=1.0):
    req = urllib.request.Request(url, headers=UA)
    for a in range(tries):
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if a == tries - 1:
                print(f"  [FAIL] {url} -> {e}", flush=True)
                return None
            time.sleep(sleep_s * (a + 1))
    return None


def search_works(query, page_size=100, max_pages=None, sleep_s=1.0):
    """分页遍历 Catalogue API 搜索结果, 只拿 h(手稿) 类, yield work id"""
    page = 1
    seen = 0
    while True:
        qs = urllib.parse.urlencode({
            "query": query, "workType": "h", "pageSize": page_size, "page": page,
        })
        url = f"{CATALOGUE}?{qs}"
        d = http_get_json(url, sleep_s=sleep_s)
        if not d:
            break
        results = d.get("results", [])
        total = d.get("totalResults", 0)
        for r in results:
            seen += 1
            yield r.get("id"), r.get("title", "")
        print(f"  [{query}] page {page}: +{len(results)} (seen {seen}/{total})", flush=True)
        if not results or seen >= total:
            break
        if max_pages and page >= max_pages:
            break
        page += 1
        time.sleep(sleep_s)


def resolve_manifest(work_id, sleep_s=1.0):
    """work id -> items[].locations 找 iiif-presentation url + license.id"""
    url = f"{CATALOGUE}/{work_id}?include=items"
    d = http_get_json(url, sleep_s=sleep_s)
    if not d:
        return None
    for item in d.get("items", []):
        for loc in item.get("locations", []):
            lt = (loc.get("locationType") or {}).get("id", "")
            if lt == "iiif-presentation":
                lic = (loc.get("license") or {}).get("id")
                return {"manifest": loc.get("url"), "license": lic}
    return None


def probe_manifest(manifest_url, sleep_s=1.0):
    """确认 manifest 能取到、canvas>=1(防空壳),返回页数"""
    d = http_get_json(manifest_url, sleep_s=sleep_s)
    if not d:
        return 0
    seqs = d.get("sequences") or []
    if seqs and seqs[0].get("canvases"):
        return len(seqs[0]["canvases"])
    # IIIF v3 fallback
    n = 0
    for c in d.get("items", []):
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="总共最多枚举多少个 work(验证模式用小数)")
    ap.add_argument("--out", default="worklist_wellcome.csv")
    ap.add_argument("--sleep", type=float, default=1.0, help="每次 API 请求 sleep 秒数(枚举阶段, 不是取图阶段)")
    ap.add_argument("--per-keyword-pages", type=int, default=1, help="每个关键词最多翻几页(page_size=100)")
    a = ap.parse_args()

    seen_work_ids = set()
    seen_manifest_ids = set()
    rows = []
    stats = {"work_seen": 0, "dup_work": 0, "no_presentation_loc": 0,
              "license_rejected": 0, "probe_empty": 0, "dup_manifest": 0, "accepted": 0}

    for kw in KEYWORDS:
        if len(rows) >= a.limit:
            break
        print(f"=== 关键词: {kw} ===", flush=True)
        for work_id, title in search_works(kw, page_size=100, max_pages=a.per_keyword_pages, sleep_s=a.sleep):
            if len(rows) >= a.limit:
                break
            stats["work_seen"] += 1
            if work_id in seen_work_ids:
                stats["dup_work"] += 1
                continue
            seen_work_ids.add(work_id)

            info = resolve_manifest(work_id, sleep_s=a.sleep)
            if not info or not info.get("manifest"):
                stats["no_presentation_loc"] += 1
                continue

            lic = info.get("license")
            if lic not in LICENSE_WHITELIST:
                stats["license_rejected"] += 1
                print(f"  [剔除-license={lic}] {work_id} {title[:40]}", flush=True)
                continue

            manifest_url = info["manifest"]
            manifest_id = manifest_url.rstrip("/").split("/")[-1]
            if manifest_id in seen_manifest_ids:
                stats["dup_manifest"] += 1
                continue

            pages = probe_manifest(manifest_url, sleep_s=a.sleep)
            if pages < 1:
                stats["probe_empty"] += 1
                print(f"  [剔除-空壳] {work_id} manifest={manifest_id}", flush=True)
                continue

            seen_manifest_ids.add(manifest_id)
            rows.append({
                "id": manifest_id,
                "manifest": manifest_url,
                "license": lic,
                "pages": pages,
            })
            stats["accepted"] += 1
            print(f"  [OK] work={work_id} manifest={manifest_id} license={lic} pages={pages}", flush=True)

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "manifest", "license", "pages"])
        w.writeheader()
        w.writerows(rows)

    print("\n=== 汇总 ===", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)
    print(f"  输出: {a.out} ({len(rows)} 行)", flush=True)


if __name__ == "__main__":
    main()
