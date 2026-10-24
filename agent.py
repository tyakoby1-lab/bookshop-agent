#!/usr/bin/env python3
"""
Bookshop.org -> Instagram agent.

Free version: no AI API. You write the captions (e.g. with Claude in chat).

Every CYCLE_DAYS days it publishes two Instagram posts for one book:
  1. a book spotlight (morning run)   -> captions/<isbn>-spotlight.txt
  2. your review (evening run)        -> captions/<isbn>-review.txt
For your own book (kind=self) the second post is an author post instead of a review.

Flow per book in books.csv:
  queued  -> agent prepares a 4:5 cover image + book info (drafts/<isbn>.json) -> drafted
  drafted -> you add the two caption files in captions/                        -> ready
  ready   -> agent posts spotlight, then review                                -> posted

Commands:
  python agent.py                run one scheduled tick (what GitHub Actions calls)
  python agent.py draft <isbn>   draft one book now
  python agent.py refresh-token  print a refreshed long-lived Instagram token
"""
import csv
import datetime as dt
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent
BOOKS = ROOT / "books.csv"
STATE = ROOT / "state.json"
DRAFTS = ROOT / "drafts"
IMAGES = ROOT / "images"

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Jerusalem"))
CYCLE_DAYS = int(os.getenv("CYCLE_DAYS", "2"))
REVIEW_AFTER_HOUR = int(os.getenv("REVIEW_AFTER_HOUR", "15"))  # local hour
SHOP_URL = os.getenv("SHOP_URL", "https://bookshop.org/shop/JewishChildrenBooks")
# Public base URL where images/ is served, e.g.
# https://raw.githubusercontent.com/<you>/<repo>/main/images
PUBLIC_IMAGE_BASE = os.getenv("PUBLIC_IMAGE_BASE", "").rstrip("/")
IG_USER_ID = os.getenv("IG_USER_ID", "")
IG_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_API_VERSION = os.getenv("IG_API_VERSION", "v23.0")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

FIELDS = ["isbn", "title", "author", "kind", "review_notes",
          "list_name", "priority", "image_url", "status"]


def log(msg):
    print(f"[{dt.datetime.now(TZ):%Y-%m-%d %H:%M}] {msg}", flush=True)


# ---------- storage ----------

def load_books():
    with BOOKS.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in FIELDS:
            r[k] = (r.get(k) or "").strip()
        r["status"] = r["status"] or "queued"
        r["kind"] = r["kind"] or "book"
    return rows


def save_books(rows):
    with BOOKS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"last_cycle": None, "step": "idle", "current_isbn": None}


def save_state(s):
    STATE.write_text(json.dumps(s, indent=2) + "\n")


def by_priority(rows):
    # higher priority first, then file order
    return sorted(rows, key=lambda r: -int(r["priority"] or 0))


# ---------- book data ----------

def google_books(isbn):
    try:
        r = requests.get("https://www.googleapis.com/books/v1/volumes",
                         params={"q": f"isbn:{isbn}"}, timeout=20)
        items = r.json().get("items") or []
        return items[0]["volumeInfo"] if items else {}
    except Exception as e:
        log(f"Google Books lookup failed for {isbn}: {e}")
        return {}


def download(url):
    r = requests.get(url, timeout=30)
    if r.status_code != 200 or len(r.content) < 2000:
        return None
    try:
        return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception:
        return None


def cover_image(book, info):
    candidates = [book["image_url"],
                  f"https://covers.openlibrary.org/b/isbn/{book['isbn']}-L.jpg?default=false"]
    thumb = (info.get("imageLinks") or {}).get("thumbnail")
    if thumb:
        candidates.append(thumb.replace("http://", "https://").replace("&edge=curl", ""))
    for url in filter(None, candidates):
        img = download(url)
        if img:
            return img
    return None


def pad_to_instagram(img, size=(1080, 1350), bg=(250, 247, 240)):
    """Book covers are ~2:3, narrower than Instagram's 4:5 minimum, so pad them."""
    canvas = Image.new("RGB", size, bg)
    margin = 90
    img = img.copy()
    img.thumbnail((size[0] - 2 * margin, size[1] - 2 * margin), Image.LANCZOS)
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    return canvas


# ---------- captions (written by you) ----------

CAPTIONS = ROOT / "captions"


def caption_path(isbn, which):
    return CAPTIONS / f"{isbn}-{which}.txt"


def has_captions(book):
    return all(caption_path(book["isbn"], w).exists() and
               caption_path(book["isbn"], w).read_text(encoding="utf-8").strip()
               for w in ("spotlight", "review"))


def draft(book):
    info = google_books(book["isbn"])
    book["title"] = book["title"] or info.get("title", "")
    book["author"] = book["author"] or ", ".join(info.get("authors", []))
    img = cover_image(book, info)
    if not img:
        book["status"] = "needs_image"
        log(f"{book['isbn']}: no cover found, add image_url in books.csv")
        return False
    IMAGES.mkdir(exist_ok=True)
    pad_to_instagram(img).save(IMAGES / f"{book['isbn']}.jpg", "JPEG", quality=90)
    DRAFTS.mkdir(exist_ok=True)
    (DRAFTS / f"{book['isbn']}.json").write_text(json.dumps({
        "title": book["title"],
        "author": book["author"],
        "kind": book["kind"],
        "description": info.get("description", ""),
        "image": f"images/{book['isbn']}.jpg",
        "next_step": f"add captions/{book['isbn']}-spotlight.txt and captions/{book['isbn']}-review.txt",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    book["status"] = "drafted"
    log(f"Prepared {book['title']} ({book['isbn']}). Now add its two caption files in captions/")
    return True


# ---------- Instagram ----------

def ig_publish(caption, image_url):
    if DRY_RUN:
        log(f"DRY RUN would post {image_url}\n{caption[:200]}...")
        return "dry-run"
    if not (IG_USER_ID and IG_TOKEN):
        raise SystemExit("IG_USER_ID and IG_ACCESS_TOKEN must be set")
    base = f"https://graph.instagram.com/{IG_API_VERSION}"
    r = requests.post(f"{base}/{IG_USER_ID}/media", timeout=60, data={
        "image_url": image_url, "caption": caption, "access_token": IG_TOKEN})
    if not r.ok:
        raise RuntimeError(f"Create container failed: {r.text}")
    cid = r.json()["id"]
    for _ in range(12):
        s = requests.get(f"{base}/{cid}", timeout=30, params={
            "fields": "status_code", "access_token": IG_TOKEN}).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError(f"Container error: {s}")
        time.sleep(5)
    r = requests.post(f"{base}/{IG_USER_ID}/media_publish", timeout=60, data={
        "creation_id": cid, "access_token": IG_TOKEN})
    if not r.ok:
        raise RuntimeError(f"Publish failed: {r.text}")
    return r.json()["id"]


def post(book, which):
    if not PUBLIC_IMAGE_BASE and not DRY_RUN:
        raise SystemExit("PUBLIC_IMAGE_BASE must be set so Instagram can fetch the image")
    url = f"{PUBLIC_IMAGE_BASE}/{book['isbn']}.jpg"
    caption = caption_path(book["isbn"], which).read_text(encoding="utf-8").strip()
    media_id = ig_publish(caption, url)
    log(f"Posted {which} for {book['title']} (media {media_id})")


# ---------- scheduler tick ----------

def tick():
    now = dt.datetime.now(TZ)
    today = now.date()
    books = load_books()
    state = load_state()
    find = {b["isbn"]: b for b in books}
    for b in books:
        if b["status"] in ("drafted", "approved") and has_captions(b):
            b["status"] = "ready"

    if state["step"] == "spotlight_posted":
        last = dt.date.fromisoformat(state["last_cycle"])
        book = find.get(state["current_isbn"])
        if book and (today > last or now.hour >= REVIEW_AFTER_HOUR):
            post(book, "review")
            book["status"] = "posted"
            state.update(step="idle", current_isbn=None)
        else:
            log("Waiting for the afternoon run to post the review")
    else:
        last = state["last_cycle"]
        due = last is None or today >= dt.date.fromisoformat(last) + dt.timedelta(days=CYCLE_DAYS)
        if due:
            approved = [b for b in by_priority(books) if b["status"] == "ready"]
            if approved:
                book = approved[0]
                post(book, "spotlight")
                book["status"] = "posting"
                state.update(step="spotlight_posted", last_cycle=today.isoformat(),
                             current_isbn=book["isbn"])
            else:
                log("Post is due but no book has its captions yet")
        else:
            log(f"Next cycle on {dt.date.fromisoformat(last) + dt.timedelta(days=CYCLE_DAYS)}")

    # keep one draft waiting for your approval
    if not any(b["status"] in ("drafted", "approved", "ready") for b in books):
        for b in by_priority(books):
            if b["status"] == "queued" and draft(b):
                break

    save_books(books)
    save_state(state)


def refresh_token():
    r = requests.get("https://graph.instagram.com/refresh_access_token", timeout=30,
                     params={"grant_type": "ig_refresh_token", "access_token": IG_TOKEN})
    print(r.json())


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        tick()
    elif args[0] == "draft" and len(args) == 2:
        rows = load_books()
        b = next((r for r in rows if r["isbn"] == args[1]), None)
        if not b:
            raise SystemExit("ISBN not in books.csv")
        draft(b)
        save_books(rows)
    elif args[0] == "refresh-token":
        refresh_token()
    else:
        print(__doc__)
