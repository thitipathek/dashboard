#!/usr/bin/env python3
"""MR ALGO Dashboard - rebuild summary counts.

Run:  python3 build.py           # refresh copies + summary.json
      python3 build.py --push    # same, then commit & push this repo (only this repo)

What it does (standard library only, plus git; node is optional):
  * Reads the published data of each project (read-only blobless clones kept in .cache/):
      instagram-gallery/posts.json, tiktok-gallery/data.json,
      influencer-tracker/data/site.json + influencers.json, coach-map/coaches.js
  * Copies the local, unpublished pages into this repo:
      /workspace/youtube-gallery/channel-scores/channel_scores.html -> youtube/index.html
      /workspace/youtube-gallery/videos.json                       -> youtube/videos.html (generated)
      /workspace/trade-tools/index.html                             -> trade-tools/index.html
  * Writes summary.json (counts, last-updated, 3 latest additions, formats, insight).
  * Classifies IG/TikTok/YouTube posts into content formats -> formats/index.html + formats/items.json.
  * Builds weekly Insight block for the home page.
Never invents numbers: anything that cannot be read is written as null and shown as "—".
It never writes to the other repos or to the other bots' folders.
"""
import csv, datetime as dt, html, json, os, re, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, ".cache")
OWNER = "thitipathek"
PAGES = f"https://{OWNER}.github.io"
YT_SRC = "/workspace/youtube-gallery"
TT_SRC = "/workspace/trade-tools"
TZ = dt.timezone(dt.timedelta(hours=7))


def log(*a):
    print("[build]", *a, file=sys.stderr)


def git(repo_dir, *args, binary=False):
    out = subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8")


def sync_clone(name):
    """Read-only blobless clone of a published repo; returns path or None."""
    path = os.path.join(CACHE, name)
    try:
        if not os.path.isdir(os.path.join(path, ".git")):
            os.makedirs(CACHE, exist_ok=True)
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout",
                            f"https://github.com/{OWNER}/{name}.git", path], check=True)
        else:
            git(path, "fetch", "-q", "--prune", "origin")
            git(path, "remote", "set-head", "origin", "-a")
        return path
    except Exception as e:  # network / auth problem -> source shows "—"
        log("cannot sync", name, e)
        return None


def history(repo, relpath):
    """[(commit, iso_time)] oldest -> newest for commits touching relpath on origin/HEAD."""
    out = git(repo, "log", "--reverse", "--format=%H %cI", "origin/HEAD", "--", relpath)
    return [tuple(l.split(" ", 1)) for l in out.splitlines() if l.strip()]


def show(repo, commit, relpath):
    return git(repo, "show", f"{commit}:{relpath}")


def first_seen(repo, relpath, extract):
    """Map item-id -> iso time of the first commit in which it appeared."""
    seen = {}
    for commit, when in history(repo, relpath):
        try:
            items = extract(show(repo, commit, relpath))
        except Exception:
            continue
        for iid in items:
            seen.setdefault(iid, when)
    return seen


def norm_iso(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)
    return d.astimezone(TZ).isoformat(timespec="seconds")


def short(text, n=110):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def empty(**extra):
    d = {"count": None, "updated": None, "extras": {}}
    d.update(extra)
    return d


# ---------------------------------------------------------------- Instagram
def build_instagram(adds):
    res = empty(url=f"{PAGES}/instagram-gallery/")
    repo = sync_clone("instagram-gallery")
    if not repo:
        return res
    rel = "posts.json"
    posts = json.loads(show(repo, "origin/HEAD", rel))
    h = history(repo, rel)
    res.update(count=len(posts), updated=norm_iso(h[-1][1]) if h else None)
    res["extras"] = {
        "accounts": len({p.get("handle") for p in posts if p.get("handle")}),
        "videos": sum(1 for p in posts if str(p.get("image", "")).lower().endswith(".mp4")),
    }
    seen = first_seen(repo, rel, lambda s: [p["url"] for p in json.loads(s) if p.get("url")])
    tree = set(git(repo, "ls-tree", "-r", "--name-only", "origin/HEAD").splitlines())
    for p in posts:
        img = p.get("image") or ""
        if img.lower().endswith(".mp4"):  # videos: use the poster frame the gallery publishes, if any
            img = next((c for c in (img[:-4] + "-poster.jpg", img[:-4] + ".jpg") if c in tree), "")
        thumb = f"{PAGES}/instagram-gallery/{img}" if img in tree and re.search(r"\.(jpe?g|png|webp)$", img, re.I) else None
        if p.get("url") in seen:
            adds.append({"source": "instagram", "title": short(p.get("summary") or p.get("caption")),
                         "who": "@" + p.get("handle", ""), "url": p["url"], "thumb": thumb,
                         "added_at": norm_iso(seen[p["url"]])})
    return res


# ---------------------------------------------------------------- TikTok
def build_tiktok(adds):
    res = empty(url=f"{PAGES}/tiktok-gallery/")
    repo = sync_clone("tiktok-gallery")
    if not repo:
        return res
    rel = "data.json"
    items = json.loads(show(repo, "origin/HEAD", rel))
    h = history(repo, rel)
    res.update(count=len(items), updated=norm_iso(h[-1][1]) if h else None)
    res["extras"] = {"accounts": len({i.get("account") for i in items if i.get("account")})}
    seen = first_seen(repo, rel, lambda s: [str(i["id"]) for i in json.loads(s) if i.get("id")])
    for i in items:
        iid = str(i.get("id"))
        if iid in seen:
            poster = i.get("poster")
            adds.append({"source": "tiktok", "title": short(i.get("summary") or i.get("caption")),
                         "who": i.get("account", ""), "url": i.get("post_url"),
                         "thumb": f"{PAGES}/tiktok-gallery/{poster}" if poster else None,
                         "added_at": norm_iso(seen[iid])})
    return res


# ---------------------------------------------------------------- Influencer Tracker
def build_influencers(adds):
    res = empty(url=f"{PAGES}/influencer-tracker/")
    repo = sync_clone("influencer-tracker")
    if not repo:
        return res
    site = json.loads(show(repo, "origin/HEAD", "data/site.json"))
    accounts = site.get("accounts") or []
    counts = site.get("counts") or {}
    res["count"] = len(accounts) if accounts else (sum(counts.values()) if counts else None)
    h = history(repo, "data/site.json")
    res["updated"] = norm_iso(site.get("generated_at")) or (norm_iso(h[-1][1]) if h else None)
    with_stats = sum(1 for a in accounts if a.get("followers") is not None)
    res["extras"] = {
        "instagram": counts.get("instagram"),
        "tiktok": counts.get("tiktok"),
        "with_stats": with_stats if accounts else None,
        "snapshots": len(site.get("snapshot_dates") or []) or None,
    }
    rel = "data/influencers.json"
    seen = first_seen(repo, rel, lambda s: [a["id"] for a in json.loads(s) if a.get("id")])
    for a in accounts:
        if a.get("id") in seen:
            adds.append({"source": "influencers", "title": short(a.get("display_name") or a.get("handle")),
                         "who": ("@" + a["handle"]) if a.get("handle") else "", "url": a.get("profile_url"),
                         "thumb": None, "added_at": norm_iso(seen[a["id"]])})
    return res


# ---------------------------------------------------------------- Coach Map
def parse_coaches(js):
    try:
        code = "const window={};" + js + ";process.stdout.write(JSON.stringify(window.COACHES||[]))"
        out = subprocess.run(["node", "-e", code], capture_output=True, check=True, timeout=30)
        return json.loads(out.stdout)
    except Exception:
        block = js.split("window.COACHES", 1)[-1]
        return [{"id": m} for m in re.findall(r'^\s{4}id:\s*"([^"]+)"', block, re.M)]


def build_coachmap(adds):
    res = empty(url=f"{PAGES}/coach-map/")
    repo = sync_clone("coach-map")
    if not repo:
        return res
    rel = "coaches.js"
    coaches = parse_coaches(show(repo, "origin/HEAD", rel))
    h = history(repo, rel)
    res.update(count=len(coaches), updated=norm_iso(h[-1][1]) if h else None)
    seen = first_seen(repo, rel, lambda s: [c.get("id") for c in parse_coaches(s)])
    for c in coaches:
        if c.get("id") in seen:
            adds.append({"source": "coachmap", "title": short(c.get("name") or c.get("id")),
                         "who": c.get("handle", ""), "url": f"{PAGES}/coach-map/", "thumb": None,
                         "added_at": norm_iso(seen[c["id"]])})
    return res


# ---------------------------------------------------------------- YouTube (local -> this repo)
def file_time(path):
    return dt.datetime.fromtimestamp(os.path.getmtime(path), TZ).isoformat(timespec="seconds")


def build_youtube(adds, ledger):
    res = empty(url="youtube/")
    out_dir = os.path.join(ROOT, "youtube")
    os.makedirs(out_dir, exist_ok=True)
    scores_html = os.path.join(YT_SRC, "channel-scores", "channel_scores.html")
    scores_csv = os.path.join(YT_SRC, "channel-scores", "channel_scores.csv")
    videos_json = os.path.join(YT_SRC, "videos.json")
    times = []
    if os.path.isfile(scores_html):
        shutil.copyfile(scores_html, os.path.join(out_dir, "index.html"))
        times.append(file_time(scores_html))
    rows = []
    if os.path.isfile(scores_csv):
        with open(scores_csv, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if (r.get("channel") or "").strip()]
        times.append(file_time(scores_csv))
        res["count"] = len(rows)
        grades = {}
        for r in rows:
            g = (r.get("grade") or "").strip()
            if g:
                grades[g] = grades.get(g, 0) + 1
        res["extras"]["grades"] = grades
        av_dir = os.path.join(out_dir, "avatars")
        os.makedirs(av_dir, exist_ok=True)
        for r in rows:
            key = (r.get("handle") or r.get("url") or r["channel"]).strip()
            if key not in ledger:
                # first time this build sees the channel: use its own "collected" date if present
                ledger[key] = norm_iso((r.get("collected") or "").strip() + "T00:00:00") if r.get("collected") \
                    else dt.datetime.now(TZ).isoformat(timespec="seconds")
            av = (r.get("avatar_file") or "").strip()
            thumb = None
            src = os.path.join(YT_SRC, "channel-scores", av) if av else ""
            if av and os.path.isfile(src):
                shutil.copyfile(src, os.path.join(av_dir, os.path.basename(av)))
                thumb = "youtube/avatars/" + os.path.basename(av)
            score = (r.get("total_score") or "").strip()
            grade = (r.get("grade") or "").strip()
            adds.append({"source": "youtube", "title": r["channel"].strip(),
                         "who": (f"คะแนน {score} · เกรด {grade}" if score and grade else r.get("handle", "")),
                         "url": r.get("url"), "thumb": thumb, "added_at": ledger[key]})
    if os.path.isfile(videos_json):
        with open(videos_json, encoding="utf-8") as f:
            vd = json.load(f)
        vids = vd.get("videos") or []
        res["extras"]["videos"] = len(vids)
        times.append(file_time(videos_json))
        write_videos_page(vids, vd.get("updated"), os.path.join(out_dir, "videos.html"))
    res["updated"] = max(times) if times else None
    return res


TOPIC_TH = {"trading": "เทรด", "gold": "ทองคำ", "forex": "Forex", "investing": "ลงทุน", "mindset": "Mindset"}


def write_videos_page(vids, updated, path):
    e = html.escape
    cards = []
    for v in vids:
        t = v.get("topic", "")
        cards.append(
            f'<a class="card" href="{e(v.get("url",""))}" target="_blank" rel="noopener">'
            f'<span class="tag">{e(TOPIC_TH.get(t, t))}</span>'
            f'<h3>{e(v.get("title",""))}</h3><div class="ch">{e(v.get("channel",""))}</div>'
            f'<div class="meta">{e(v.get("views") or "—")} · {e(v.get("date") or "—")}</div>'
            f'<p>{e(v.get("note",""))}</p></a>')
    page = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>คลิป YouTube ที่เก็บไว้</title>
<style>
:root{{color-scheme:light dark;--bg:#f5f6f8;--card:#fff;--fg:#1d2330;--mut:#5b6475;--line:#e3e6ec;--acc:#c62828}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1218;--card:#181c25;--fg:#e8eaf0;--mut:#9aa3b5;--line:#262c38;--acc:#ff6b6b}}}}
body{{margin:0;font-family:system-ui,-apple-system,"Noto Sans Thai",sans-serif;background:var(--bg);color:var(--fg)}}
.wrap{{max-width:1100px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:4px 0}}.sub{{color:var(--mut);font-size:13px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
.card{{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;color:inherit;text-decoration:none}}
.card h3{{font-size:15px;margin:8px 0 4px}}.ch{{font-weight:600;font-size:13px}}.meta{{color:var(--mut);font-size:12px;margin-top:2px}}
.card p{{font-size:13px;color:var(--mut);margin:8px 0 0}}.tag{{font-size:11px;padding:2px 8px;border-radius:9px;background:var(--acc);color:#fff}}
</style></head><body><div class="wrap"><h1>คลิป YouTube ที่เก็บไว้ ({len(vids)} คลิป)</h1>
<div class="sub">อัปเดต {e(updated or "—")} · ยอดวิวและวันที่ตามที่ YouTube แสดง ไม่ได้ประมาณเอง</div>
<div class="grid">{''.join(cards)}</div></div></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


# ---------------------------------------------------------------- Trade Tools (local -> this repo)
def build_tradetools():
    res = empty(url="trade-tools/")
    src = os.path.join(TT_SRC, "index.html")
    if not os.path.isfile(src):
        return res
    out_dir = os.path.join(ROOT, "trade-tools")
    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(src, os.path.join(out_dir, "index.html"))
    s = open(src, encoding="utf-8").read()
    m = re.search(r"const items\s*=\s*\[(.*?)\n\];", s, re.S)
    if m:
        cats = re.findall(r'\{c:"(\w+)"', m.group(1))
        res["count"] = len(cats)
        res["extras"] = {c: cats.count(c) for c in sorted(set(cats))}
    # the page states when prices were checked, e.g. "เมื่อ 4 ต.ค. 2026"
    TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    d = re.search(r"(\d{1,2})\s+(" + "|".join(re.escape(x) for x in TH) + r")\s+(\d{4})", s)
    if d:
        y = int(d.group(3)); y = y - 543 if y > 2400 else y
        res["updated"] = dt.datetime(y, TH.index(d.group(2)) + 1, int(d.group(1)), tzinfo=TZ).isoformat()
        res["updated_is_date"] = True
    else:
        res["updated"] = file_time(src)
    return res


# ---------------------------------------------------------------- Content formats
FORMAT_DEFS = [
    ("chart", "วิเคราะห์ชาร์ต / เซ็ตอัป"),
    ("recap", "รีแคปเทรด / พิสูจน์ผล"),
    ("edu", "สอน / ทิป / ทิวโทเรียล"),
    ("psych", "จิตวิทยา / มายด์เซ็ต"),
    ("meme", "มีม / ตลก"),
    ("lifestyle", "ไลฟ์สไตล์ / โชว์ไลฟ์"),
    ("quiz", "ควิซ / คำถาม / โพล"),
    ("cta", "CTA คอมเมนต์รับของ / โค้ดส่วนลด"),
    ("news", "ข่าว / อัปเดตตลาด"),
    ("testimonial", "รีวิว / ผลนักเรียน"),
    ("other", "อื่นๆ"),
]
FORMAT_NAME = {i: n for i, n in FORMAT_DEFS}

# Deterministic keyword rules over caption + summary + hashtags (+ title/note for YouTube).
# First match wins, so the order below is the priority.
_FORMAT_RULES = [
    ("cta", [
        r"comment\s*[\"“”'‘’]?[A-Za-z0-9]+[\"“”'‘’]?\s*(and|to|for)\b", r"comment .{0,20} to (get|receive)",
        r"\bdm me\b", r"\bcode\s*[\"“”']?[A-Z0-9]{3,}[\"“”']?.{0,12}% ?off", r"\d+% off",
        r"comment settings to know", r"คอมเมนต์ .{0,15}รับ",
    ]),
    ("quiz", [
        r"\bquiz", r"\bpoll\b", r"buy or sell\?", r"would you buy or sell", r"comment your score",
        r"which (one|setup|side) (would|do)", r"guess the", r"ควิซ", r"โพล",
    ]),
    ("testimonial", [
        r"\bmy students?\b", r"\bstudents?'? results?\b", r"testimonial", r"client results?",
        r"passed (the |my )?challenge", r"ผลนักเรียน", r"รีวิวจากนักเรียน",
    ]),
    ("news", [
        r"\bnews\b", r"\bnfp\b", r"\bfomc\b", r"\bcpi\b", r"interest rates?", r"market update",
        r"weekly outlook", r"ข่าว", r"อัปเดตตลาด", r"all[- ]time high", r"record highs?",
        r"stock split", r"dow tops", r"\binflation\b", r"\bsec\b", r"blackrock", r"\bcftc\b",
        r"\bhack(ed)?\b", r"announce(s|d|ment)",
    ]),
    ("recap", [
        r"\brecap\b", r"\bp&l\b", r"\bpnl\b", r"(made|makes|earned|banked|generated?|over)\s+\$\d",
        r"\+\$?\d[\d,.]*\s*(usd|pips)\b", r"\+\d+(\.\d+)?r\b", r"trade result", r"closed (the )?trade",
        r"keeps printing", r"\bbalance\b", r"\bequity\b", r"target (done|hit)", r"\btp hit\b",
        r"รีแคป", r"กำไร", r"six[- ]figure", r"positions? open", r"\bpayout\b",
        r"winning (short|long|trade)", r"profit",
    ]),
    ("chart", [
        r"\bchart\b", r"\bsetup\b", r"order block", r"\bi?fvg\b", r"\bote\b", r"liquidity",
        r"\bretest\b", r"support.{0,20}resistance", r"fibonacc?h?i", r"\bfib\b", r"\bsmc\b",
        r"\bict\b", r"price action", r"market structure", r"(demand|supply) zone", r"\bbos\b",
        r"\bchoch\b", r"order ?flow", r"volume profile", r"point of control", r"\btpo\b",
        r"วิเคราะห์", r"ชาร์ต", r"กราฟ", r"quasimodo", r"confluence", r"\bentry\b", r"xau/?usd",
        r"\bnq\b", r"es futures", r"timeframe", r"candlestick", r"pattern", r"breakout",
        r"absorption", r"trendline", r"\bgap\b", r"momentum", r"\bvwap\b", r"\bema\b", r"\brsi\b",
    ]),
    ("psych", [
        r"psycholog", r"mindset", r"disciplin", r"displine", r"patien(ce|t)", r"emotion",
        r"\bfear\b", r"\bgreed\b", r"self.?doubt", r"lose hope", r"give up", r"motivation",
        r"journey of a trader", r"boring", r"จิตวิทยา", r"มายด์เซ็ต", r"วินัย", r"สมาธิ",
        r"mindfulness", r"habits", r"overtrad", r"revenge trad", r"\bfomo\b", r"your prime",
        r"harder (road|path)", r"\bsuccess\b", r"\bjoy\b", r"in your 20s", r"compare",
        r"blow(ing|n)? up", r"พอร์?ตพัง", r"trap\b",
    ]),
    ("meme", [
        r"\bmeme\b", r"\bfunny\b", r"\bjoke\b", r"\bhumou?r\b", r"\blol\b", r"\blmao\b",
        r"\bpeak\b", r"cartoon", r"มีม", r"ตลก", r"😂", r"home ?alone", r"acts out", r"\bpov\b",
        r"\bviral clip\b", r"reacts? to",
    ]),
    ("lifestyle", [
        r"lifestyle", r"day in the life", r"\bluxury\b", r"ferrari", r"lamborghini", r"my desk",
        r"\btravel\b", r"\bflex\b", r"ไลฟ์สไตล์", r"tradinglife", r"femaletrader", r"costco",
        r"\bvlog\b", r"\bbought\b",
    ]),
    ("edu", [
        r"how to", r"explain", r"\btutorial\b", r"\blessons?\b", r"\btips?\b", r"\blearn",
        r"beginner", r"\bguide(line)?\b", r"strateg(y|ies)", r"what is", r"education", r"สอน",
        r"ทิป", r"full course", r"index funds?", r"compound", r"buy and hold", r"portfolio",
        r"net worth", r"\binvest", r"walks? through", r"breakdown", r"need to know", r"ลงทุน",
        r"เก็บเงิน", r"\bvs\.?\b",
    ]),
]
_FORMAT_COMPILED = [(fid, [re.compile(p, re.I) for p in pats]) for fid, pats in _FORMAT_RULES]


def classify_format(text):
    t = text or ""
    for fid, regs in _FORMAT_COMPILED:
        for r in regs:
            if r.search(t):
                return fid
    return "other"


def _num(v):
    """Parse a numeric field; return int/float or None. Treat 0 views as unknown when flagged."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KMB])?\s*(views?)?$", s, re.I)
    if m:
        n = float(m.group(1))
        u = (m.group(2) or "").upper()
        if u == "K":
            n *= 1_000
        elif u == "M":
            n *= 1_000_000
        elif u == "B":
            n *= 1_000_000_000
        return int(n) if n == int(n) else n
    try:
        return float(s)
    except ValueError:
        return None


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _avg(vals):
    return (sum(vals) / len(vals)) if vals else None


def _round_num(v):
    if v is None:
        return None
    if isinstance(v, float):
        return round(v, 1) if abs(v) < 100 else int(round(v))
    return int(v)


def _item_text(parts):
    return " ".join(str(p) for p in parts if p)


def _stat_block(vals):
    vals = [v for v in vals if v is not None]
    return {
        "n": len(vals),
        "avg": _round_num(_avg(vals)),
        "median": _round_num(_median(vals)),
    }


def collect_content_items(ig_posts, tt_items, yt_videos):
    """Normalize gallery items and attach a format id. Never invents numbers."""
    out = []
    for p in ig_posts or []:
        likes = _num(p.get("likes"))
        views = _num(p.get("views"))
        # Instagram views often missing/0 without login — treat 0 as unknown
        if views is not None and views == 0:
            views = None
        comments = _num(p.get("comments"))
        text = _item_text([
            p.get("caption"), p.get("summary"),
            " ".join(p.get("hashtags") or []),
        ])
        out.append({
            "platform": "instagram",
            "who": ("@" + p["handle"]) if p.get("handle") else "",
            "title": short(p.get("summary") or p.get("caption"), 90),
            "url": p.get("url"),
            "date": (p.get("posted_at") or "")[:10] or None,
            "likes": likes, "comments": comments, "views": views,
            "duration_sec": p.get("duration_sec"), "lang": p.get("lang"),
            "format": classify_format(text),
            "views_known": views is not None,
        })
    for p in tt_items or []:
        likes = _num(p.get("likes"))
        views = _num(p.get("views"))
        if views is not None and views == 0:
            views = None
        comments = _num(p.get("comments"))  # usually absent
        text = _item_text([
            p.get("caption"), p.get("summary"),
            " ".join(p.get("hashtags") or []),
        ])
        out.append({
            "platform": "tiktok",
            "who": p.get("account") or "",
            "title": short(p.get("summary") or p.get("caption"), 90),
            "url": p.get("post_url"),
            "date": (p.get("date") or "")[:10] or None,
            "likes": likes, "comments": comments, "views": views,
            "duration_sec": p.get("duration_sec"), "lang": p.get("lang"),
            "format": classify_format(text),
            "views_known": views is not None,
        })
    for p in yt_videos or []:
        views = _num(p.get("views"))
        text = _item_text([p.get("title"), p.get("note"), p.get("topic")])
        # YouTube dates are relative ("1 year ago") — leave date blank
        out.append({
            "platform": "youtube",
            "who": p.get("channel") or "",
            "title": short(p.get("title"), 90),
            "url": p.get("url"),
            "date": None,
            "likes": None, "comments": None, "views": views,
            "duration_sec": None, "lang": None,
            "format": classify_format(text),
            "views_known": views is not None,
        })
    return out


PLAT_TH = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
PLATS = ("instagram", "tiktok", "youtube")
MIN_RANK_N = 3  # a format needs >= 3 items with a number on a platform to be ranked


def _platform_stats(group):
    out = {}
    for pl in PLATS:
        g = [it for it in group if it["platform"] == pl]
        out[pl] = {
            "count": len(g),
            "likes": _stat_block([it["likes"] for it in g]),
            "comments": _stat_block([it["comments"] for it in g]),
            "views": _stat_block([it["views"] for it in g]),
        }
    return out


def _rank(blocks, fid, pl, metric):
    """Rank of format fid by median metric on platform pl among formats with enough data."""
    pool = [(b["id"], b["platforms"][pl][metric]["median"]) for b in blocks
            if b["id"] != "other" and b["platforms"][pl][metric]["n"] >= MIN_RANK_N]
    pool.sort(key=lambda x: x[1], reverse=True)
    ids = [x[0] for x in pool]
    return (ids.index(fid) + 1, len(ids)) if fid in ids else (None, len(ids))


def _takeaway_th(b, blocks):
    """Templated Thai takeaway built only from real numbers in this build."""
    if b["count"] == 0:
        return "ยังไม่มีโพสต์ที่เข้ารูปแบบนี้ในข้อมูลชุดนี้"
    parts = []
    for pl, metric, word in (("tiktok", "likes", "ไลก์"), ("instagram", "likes", "ไลก์"),
                             ("tiktok", "views", "วิว"), ("youtube", "views", "วิว")):
        st = b["platforms"][pl][metric]
        if st["n"] < MIN_RANK_N:
            continue
        r, k = _rank(blocks, b["id"], pl, metric)
        med = fmt_num(st["median"])
        if r == 1 and k > 1:
            parts.append(f"{PLAT_TH[pl]}: {word}มัธยฐาน {med} สูงสุดจาก {k} รูปแบบ")
        elif r:
            parts.append(f"{PLAT_TH[pl]}: {word}มัธยฐาน {med} อันดับ {r} จาก {k} รูปแบบ")
        else:
            parts.append(f"{PLAT_TH[pl]}: {word}มัธยฐาน {med} (n={st['n']})")
        if len(parts) >= 2:
            break
    if not parts:
        parts.append(f"ตัวอย่างยังน้อย ({b['count']} โพสต์) ต้องมีอย่างน้อย {MIN_RANK_N} โพสต์ที่มีตัวเลขต่อแพลตฟอร์มจึงจะจัดอันดับ")
    share = round(b["count"] / b["_total"] * 100) if b.get("_total") else None
    if share is not None:
        parts.append(f"คิดเป็น {share}% ของทั้งหมด")
    return " · ".join(parts)


def aggregate_formats(items):
    by = {fid: [] for fid, _ in FORMAT_DEFS}
    for it in items:
        by.setdefault(it["format"], []).append(it)
    blocks = []
    for fid, name in FORMAT_DEFS:
        group = by.get(fid) or []
        # examples: best item per platform first (likes; YouTube by views), then the rest
        def key(it):
            return (it["likes"] if it["likes"] is not None else -1,
                    it["views"] if it["views"] is not None else -1)
        per = {pl: sorted([it for it in group if it["platform"] == pl], key=key, reverse=True) for pl in PLATS}
        picks = [per[pl][0] for pl in ("tiktok", "instagram", "youtube") if per[pl]]
        rest = sorted([it for it in group if it not in picks], key=key, reverse=True)
        examples = [{
            "platform": it["platform"], "who": it["who"], "title": it["title"], "url": it["url"],
            "likes": it["likes"], "comments": it["comments"], "views": it["views"],
        } for it in (picks + rest)[:3]]
        blocks.append({
            "id": fid, "name_th": name, "count": len(group),
            "by_platform": {pl: sum(1 for it in group if it["platform"] == pl) for pl in PLATS},
            "platforms": _platform_stats(group),
            "examples": examples, "_total": len(items),
        })
    for b in blocks:
        b["takeaway_th"] = _takeaway_th(b, blocks)
    for b in blocks:
        b.pop("_total", None)
    # page-level headline (real numbers only)
    head = []
    for pl, metric, word in (("tiktok", "likes", "ไลก์"), ("instagram", "likes", "ไลก์"), ("youtube", "views", "วิว")):
        pool = [b for b in blocks if b["id"] != "other" and b["platforms"][pl][metric]["n"] >= MIN_RANK_N]
        if pool:
            top = max(pool, key=lambda b: b["platforms"][pl][metric]["median"])
            head.append({"platform": pl, "metric": metric, "format": top["id"], "name_th": top["name_th"],
                         "median": top["platforms"][pl][metric]["median"], "n": top["platforms"][pl][metric]["n"],
                         "text_th": f"{PLAT_TH[pl]}: {top['name_th']} ได้{word}มัธยฐานสูงสุด {fmt_num(top['platforms'][pl][metric]['median'])} (n={top['platforms'][pl][metric]['n']})"})
    return {
        "total_items": len(items),
        "by_platform": {pl: sum(1 for it in items if it["platform"] == pl) for pl in PLATS},
        "headline": head,
        "note_th": ("ยอดวิว Instagram ที่เป็น 0 หรือว่าง (ต้องล็อกอินถึงจะเห็น) ถือว่าไม่ทราบ และไม่นำไปคิดค่าเฉลี่ย/มัธยฐานวิว · "
                    "ข้อมูลต้นทางยังไม่มีจำนวนคอมเมนต์ จึงแสดงเป็น — · YouTube ไม่มีไลก์ ใช้วิวตามที่ YouTube แสดง (เช่น 399K) · "
                    f"จัดอันดับเฉพาะรูปแบบที่มีอย่างน้อย {MIN_RANK_N} โพสต์ที่มีตัวเลขในแพลตฟอร์มนั้น · "
                    "สถิติแยกตามแพลตฟอร์มเพราะสเกลไลก์ของ TikTok กับ Instagram ต่างกันมาก · ไม่มีการประมาณตัวเลข"),
        "formats": blocks,
    }


def write_formats_page(fp, path, updated):
    e = html.escape
    pc = {"instagram": "#dd2a7b", "tiktok": "#111", "youtube": "#e62117"}
    cards = []
    for b in fp["formats"]:
        rows = []
        for pl in PLATS:
            st = b["platforms"][pl]
            if not st["count"]:
                continue
            def c(x):
                return (f"{fmt_num(x['median'])}<small>เฉลี่ย {fmt_num(x['avg'])}</small>" if x["n"] else "—")
            rows.append(f"<tr><th><span class='dot' style='background:{pc[pl]}'></span>{PLAT_TH[pl]} <small>{st['count']}</small></th>"
                        f"<td>{c(st['likes'])}</td><td>{c(st['comments'])}</td><td>{c(st['views'])}"
                        f"{'' if (not st['views']['n'] or st['views']['n']==st['count']) else f'<small>มีวิว {st["views"]["n"]}/{st["count"]}</small>'}</td></tr>")
        table = (f"<table><thead><tr><th></th><th>ไลก์ (มัธยฐาน)</th><th>คอมเมนต์</th><th>วิว (มัธยฐาน)</th></tr></thead>"
                 f"<tbody>{''.join(rows)}</tbody></table>") if rows else ""
        exs = []
        for x in b["examples"]:
            meta = [m for m in (x["likes"] is not None and f"♥ {fmt_num(x['likes'])}",
                                x["comments"] is not None and f"💬 {fmt_num(x['comments'])}",
                                x["views"] is not None and f"▶ {fmt_num(x['views'])}") if m] or ["ไม่มีตัวเลข"]
            exs.append(f"<a class='ex' href='{e(x['url'] or '#')}' target='_blank' rel='noopener'>"
                       f"<span class='pill' style='background:{pc.get(x['platform'],'#666')}'>{PLAT_TH.get(x['platform'],x['platform'])}</span>"
                       f"<span class='t'>{e(x['title'] or '—')}</span><span class='m'>{e(x['who'])} · {' · '.join(meta)}</span></a>")
        cards.append(f"""<article class="card" id="{b['id']}">
<div class="hd"><h2>{e(b['name_th'])}</h2><b class="cnt">{b['count']}</b></div>
<div class="plats"><em>Instagram {b['by_platform']['instagram']}</em><em>TikTok {b['by_platform']['tiktok']}</em><em>YouTube {b['by_platform']['youtube']}</em></div>
{table}<p class="take">💡 {e(b['takeaway_th'])}</p>
{('<h3>ตัวอย่าง</h3><div class="examples">' + ''.join(exs) + '</div>') if exs else ''}</article>""")
    head = "".join(f"<li>{e(h['text_th'])}</li>" for h in fp.get("headline") or [])
    bp = fp.get("by_platform") or {}
    page = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>รูปแบบคอนเทนต์ · MR ALGO</title>
<style>
:root{{color-scheme:light dark;--bg:#f4f5f8;--card:#fff;--fg:#151922;--mut:#5d6677;--line:#e2e5ec;--soft:#eef0f5;--gold:#c9940a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d1016;--card:#161a22;--fg:#e9ecf2;--mut:#98a1b3;--line:#262c38;--soft:#1d222c;--gold:#f2c14e}}}}
body{{margin:0;font-family:system-ui,-apple-system,"Noto Sans Thai",sans-serif;background:var(--bg);color:var(--fg)}}
.wrap{{max-width:1150px;margin:0 auto;padding:16px 14px 40px}}h1{{font-size:20px;margin:4px 0}}
.sub{{color:var(--mut);font-size:12.5px;margin:0 0 12px;line-height:1.6}}
.head{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--gold);border-radius:12px;padding:10px 14px;margin-bottom:14px;font-size:14px}}
.head ul{{margin:4px 0 0;padding-left:18px}}.head li{{margin:2px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:8px}}
.hd{{display:flex;align-items:baseline;justify-content:space-between;gap:8px}}.hd h2{{font-size:16px;margin:0}}
.cnt{{font-size:22px;font-variant-numeric:tabular-nums}}
.plats{{display:flex;gap:6px;flex-wrap:wrap}}.plats em{{font-style:normal;background:var(--soft);border-radius:7px;padding:2px 8px;font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}}
th,td{{text-align:right;padding:5px 4px;border-bottom:1px solid var(--line);vertical-align:top}}
th:first-child{{text-align:left;white-space:nowrap}}thead th{{font-size:11px;color:var(--mut);font-weight:600}}
td small,th small{{display:block;font-size:10.5px;color:var(--mut);font-weight:400}}th small{{display:inline}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}}
.take{{font-size:13px;margin:2px 0;line-height:1.5}}h3{{font-size:12.5px;margin:4px 0 0;color:var(--mut)}}
.examples{{display:flex;flex-direction:column;gap:6px}}
.ex{{display:block;text-decoration:none;color:inherit;border:1px solid var(--line);border-radius:10px;padding:7px 10px}}
.ex:hover{{background:var(--soft)}}.ex .t{{display:block;font-size:13px;margin-top:3px;line-height:1.35}}
.ex .m{{display:block;font-size:11.5px;color:var(--mut);margin-top:2px}}
.pill{{display:inline-block;font-size:10px;font-weight:700;color:#fff;border-radius:5px;padding:1px 6px}}
</style></head><body><div class="wrap">
<h1>รูปแบบคอนเทนต์</h1>
<div class="sub">{fp['total_items']} โพสต์ (Instagram {bp.get('instagram',0)} · TikTok {bp.get('tiktok',0)} · YouTube {bp.get('youtube',0)}) จัดกลุ่มอัตโนมัติด้วยกฎคำสำคัญจากแคปชัน สรุป แฮชแท็ก และชื่อคลิป · อัปเดต {e(updated or '—')}</div>
{f'<div class="head"><b>สรุปเร็ว</b><ul>{head}</ul></div>' if head else ''}
<div class="grid">{''.join(cards)}</div>
<p class="sub" style="margin-top:16px">หมายเหตุ: {e(fp.get('note_th',''))}</p>
</div></body></html>"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def fmt_num(n):
    if n is None:
        return "—"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.1f}M".replace(".0M", "M")
    if abs(n) >= 10_000:
        return f"{n/1_000:.1f}K".replace(".0K", "K")
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.1f}"


# ---------------------------------------------------------------- Weekly insight
SHARED_IT = "/home/box/shared/influencer-tracker"


def _parse_iso_date(s):
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def build_insight(items, formats_payload, generated_at):
    now = dt.datetime.now(TZ)
    today = now.date()
    week_start = today - dt.timedelta(days=6)  # last 7 calendar days incl. today
    week_items = []
    for it in items:
        d = _parse_iso_date(it.get("date"))
        if d and week_start <= d <= today:
            week_items.append(it)

    def eng(it):
        # likes + comments; views only as tiebreaker when real
        base = (it["likes"] or 0) + (it["comments"] or 0)
        views = it["views"] if it.get("views") is not None else -1
        return (base, views)

    top_posts = []
    for it in sorted(week_items, key=eng, reverse=True)[:3]:
        top_posts.append({
            "platform": it["platform"], "who": it["who"], "title": it["title"],
            "url": it["url"], "date": it["date"], "format": it["format"],
            "format_th": FORMAT_NAME.get(it["format"], it["format"]),
            "likes": it["likes"], "comments": it["comments"], "views": it["views"],
        })

    # best-performing format this week. TikTok likes are ~10x Instagram's, so each post is compared
    # with its own platform: lift = likes / median likes of ALL gallery posts on that platform.
    # Pick the format with the highest median lift among formats with >= 2 dated posts this week
    # (falls back to 1 post, flagged small_sample).
    plat_med = {}
    for pl in PLATS:
        vals = [it["likes"] for it in items if it["platform"] == pl and it["likes"] is not None]
        plat_med[pl] = _median(vals) if vals else None
    week_by_fmt = {}
    for it in week_items:
        week_by_fmt.setdefault(it["format"], []).append(it)
    candidates = []
    for fid, group in week_by_fmt.items():
        lifts = [it["likes"] / plat_med[it["platform"]] for it in group
                 if it["likes"] is not None and plat_med.get(it["platform"])]
        if not lifts:
            continue
        candidates.append({
            "id": fid, "name_th": FORMAT_NAME.get(fid, fid), "count": len(group),
            "lift_median": round(_median(lifts), 2), "likes_n": len(lifts),
            "platforms": sorted({g["platform"] for g in group}),
        })
    best_format = None
    pool = [c for c in candidates if c["likes_n"] >= 2] or candidates
    if pool:
        best_format = max(pool, key=lambda c: (c["lift_median"], c["likes_n"]))
        best_format["small_sample"] = best_format["likes_n"] < 3

    # scores
    scored_count = 0
    scored_total = None
    top_influencers = []
    scores_note = None
    scores_path = os.path.join(SHARED_IT, "scores.json")
    try:
        if os.path.isfile(scores_path):
            sc = json.load(open(scores_path, encoding="utf-8"))
            accounts = sc.get("accounts") or {}
            summary = sc.get("summary") or {}
            status_counts = summary.get("status") or {}
            scored_total = summary.get("accounts") or len(accounts)
            scored_count = status_counts.get("scored") or sum(
                1 for a in accounts.values() if a.get("status") == "scored")
            ranked = [a for a in accounts.values() if a.get("status") == "scored"
                      and a.get("scaled_100") is not None]
            ranked.sort(key=lambda a: a.get("scaled_100") or 0, reverse=True)
            for a in ranked[:5]:
                top_influencers.append({
                    "handle": a.get("handle"), "platform": a.get("platform"),
                    "scaled_100": a.get("scaled_100"), "grade": a.get("grade"),
                    "total": a.get("total"), "max": a.get("max"),
                    "pre_growth": a.get("pre_growth"),
                    "why_th": a.get("why_th"),
                    "tags": a.get("tags") or [],
                })
            if scored_count == 0:
                scores_note = "ยังไม่มีบัญชีที่ให้คะแนนครบ (รอ judgement เชิงคุณภาพ)"
            elif scored_count < (scored_total or 0):
                scores_note = f"ให้คะแนนครบแล้ว {scored_count} จาก {scored_total} บัญชี (ส่วนที่เหลือรอ judgement)"
        else:
            scores_note = "ยังไม่มีไฟล์ scores.json"
    except Exception as ex:
        log("scores read failed:", ex)
        scores_note = "อ่าน scores.json ไม่ได้"

    # rising / growth from snapshots
    snap_dir = os.path.join(SHARED_IT, "snapshots")
    snap_files = []
    if os.path.isdir(snap_dir):
        snap_files = sorted(f for f in os.listdir(snap_dir) if f.endswith(".json"))
    rising = {
        "available": False,
        "snapshots": len(snap_files),
        "note_th": "ต้องมีอย่างน้อย 2 สแนปช็อตรายสัปดาห์ถึงจะคำนวณการเติบโตของฟอลโลว์ได้ ตอนนี้มี 1 สแนปช็อต — ยังไม่มีตัวเลขการเติบโต",
        "accounts": [],
    }
    if len(snap_files) >= 2:
        rising["available"] = True
        rising["note_th"] = None
        # compute growth between last two snapshots (read-only)
        try:
            older = json.load(open(os.path.join(snap_dir, snap_files[-2]), encoding="utf-8"))
            newer = json.load(open(os.path.join(snap_dir, snap_files[-1]), encoding="utf-8"))
            oa, na = older.get("accounts") or {}, newer.get("accounts") or {}
            growths = []
            for key, nv in na.items():
                ov = oa.get(key)
                if not ov or not nv:
                    continue
                of_, nf_ = ov.get("followers"), nv.get("followers")
                if of_ is None or nf_ is None or of_ <= 0:
                    continue
                growths.append({
                    "id": key,
                    "handle": (nv.get("handle") or key.split(":", 1)[-1]),
                    "platform": nv.get("platform") or key.split(":", 1)[0],
                    "followers_before": of_, "followers_after": nf_,
                    "delta": nf_ - of_,
                    "pct": round((nf_ - of_) / of_ * 100, 2),
                })
            growths.sort(key=lambda g: g["pct"], reverse=True)
            rising["accounts"] = growths[:5]
            rising["from"] = older.get("date")
            rising["to"] = newer.get("date")
        except Exception as ex:
            log("rising compute failed:", ex)
            rising["available"] = False
            rising["note_th"] = "อ่านสแนปช็อตไม่สำเร็จ จึงยังไม่แสดงตัวเลขการเติบโต"
    elif len(snap_files) == 1:
        rising["note_th"] = f"ตอนนี้มีสแนปช็อต {snap_files[0].replace('.json','')} แค่ 1 ไฟล์ ต้องรอสแนปช็อตสัปดาห์ถัดไปถึงจะบอกได้ว่าใครกำลังพุ่ง"
    else:
        rising["note_th"] = "ยังไม่มีสแนปช็อตในโฟลเดอร์ snapshots"

    return {
        "week_start": week_start.isoformat(),
        "week_end": today.isoformat(),
        "week_posts_count": len(week_items),
        "top_posts": top_posts,
        "best_format": best_format,
        "top_influencers": top_influencers,
        "scored_count": scored_count,
        "scored_total": scored_total,
        "scores_note_th": scores_note,
        "rising": rising,
    }


def load_gallery_posts():
    """Pull IG + TikTok from cached clones (same as summary builders)."""
    ig_posts, tt_items = [], []
    ig_repo = sync_clone("instagram-gallery")
    if ig_repo:
        try:
            ig_posts = json.loads(show(ig_repo, "origin/HEAD", "posts.json"))
        except Exception as ex:
            log("ig posts load failed:", ex)
    tt_repo = sync_clone("tiktok-gallery")
    if tt_repo:
        try:
            tt_items = json.loads(show(tt_repo, "origin/HEAD", "data.json"))
        except Exception as ex:
            log("tt data load failed:", ex)
    yt_videos = []
    yj = os.path.join(YT_SRC, "videos.json")
    if os.path.isfile(yj):
        try:
            yt_videos = json.load(open(yj, encoding="utf-8")).get("videos") or []
        except Exception as ex:
            log("yt videos load failed:", ex)
    return ig_posts, tt_items, yt_videos


# ---------------------------------------------------------------- main
def main():
    ledger_path = os.path.join(ROOT, "data", "seen.json")
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    ledger = json.load(open(ledger_path, encoding="utf-8")) if os.path.isfile(ledger_path) else {}
    adds, sources = [], {}
    for key, fn in [("instagram", build_instagram), ("tiktok", build_tiktok),
                    ("influencers", build_influencers), ("coachmap", build_coachmap)]:
        try:
            sources[key] = fn(adds)
        except Exception as e:
            log(key, "failed:", e)
            sources[key] = empty()
    try:
        sources["youtube"] = build_youtube(adds, ledger)
    except Exception as e:
        log("youtube failed:", e); sources["youtube"] = empty(url="youtube/")
    try:
        sources["tradetools"] = build_tradetools()
    except Exception as e:
        log("tradetools failed:", e); sources["tradetools"] = empty(url="trade-tools/")

    # Content formats + weekly insight (generated every build)
    formats_payload, insight, classified = None, None, []
    try:
        ig_posts, tt_items, yt_videos = load_gallery_posts()
        classified = collect_content_items(ig_posts, tt_items, yt_videos)
        formats_payload = aggregate_formats(classified)
        sources["formats"] = {
            "count": formats_payload["total_items"],
            # newest source update (not "now") so unchanged data doesn't create a commit
            "updated": max([v for v in (sources.get(k, {}).get("updated") for k in ("instagram", "tiktok", "youtube")) if v] or [None]),
            "extras": {b["id"]: b["count"] for b in formats_payload["formats"]},
            "url": "formats/",
        }
        write_formats_page(
            formats_payload,
            os.path.join(ROOT, "formats", "index.html"),
            sources["formats"]["updated"],
        )
        # also dump classified items for transparency (format stored on each item)
        with open(os.path.join(ROOT, "formats", "items.json"), "w", encoding="utf-8") as f:
            json.dump({"generated_at": sources["formats"]["updated"],
                       "items": classified}, f, ensure_ascii=False, indent=1)
        insight = build_insight(classified, formats_payload, None)
        log("formats", {b["id"]: b["count"] for b in formats_payload["formats"]})
        log("insight week", insight["week_start"], "->", insight["week_end"],
            "posts", insight["week_posts_count"],
            "scored", f"{insight['scored_count']}/{insight['scored_total']}")
    except Exception as e:
        log("formats/insight failed:", e)
        sources.setdefault("formats", empty(url="formats/"))

    adds = [a for a in adds if a.get("added_at")]
    adds.sort(key=lambda a: a["added_at"], reverse=True)
    summary = {"generated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
               "sources": sources, "latest": adds[:3],
               "formats": formats_payload, "insight": insight}
    prev_path = os.path.join(ROOT, "summary.json")
    if os.path.isfile(prev_path):  # keep the old timestamp when nothing changed (avoids empty commits)
        prev = json.load(open(prev_path, encoding="utf-8"))
        if {k: v for k, v in prev.items() if k != "generated_at"} == {k: v for k, v in summary.items() if k != "generated_at"}:
            summary["generated_at"] = prev.get("generated_at", summary["generated_at"])
    with open(os.path.join(ROOT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1, sort_keys=True)
    for k, v in sources.items():
        log(f"{k:12s} count={v.get('count')} updated={v.get('updated')} extras={v.get('extras')}")
    for a in adds[:3]:
        log("latest:", a["source"], a["added_at"], a["title"][:60])
    if "--push" in sys.argv:
        git(ROOT, "add", "-A")
        if subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"]).returncode:
            git(ROOT, "commit", "-q", "-m", "Update dashboard summary " + dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"))
            git(ROOT, "push", "-q", "origin", "HEAD")
            log("pushed")
        else:
            log("no changes to push")



if __name__ == "__main__":
    main()
