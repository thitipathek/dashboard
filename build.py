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
  * Writes summary.json (counts, last-updated, 3 latest additions).
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
    adds = [a for a in adds if a.get("added_at")]
    adds.sort(key=lambda a: a["added_at"], reverse=True)
    summary = {"generated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
               "sources": sources, "latest": adds[:3]}
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
