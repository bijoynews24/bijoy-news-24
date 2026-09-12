#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
বাংলা নিউজ পোর্টাল - অটোমেটিক সাইট জেনারেটর
=================================================
এই স্ক্রিপ্টটি কিছু পাবলিক RSS ফিড থেকে খবর সংগ্রহ করে,
docs/index.html নামে একটা প্রফেশনাল-লুকিং নিউজ পোর্টাল পেজ তৈরি করে।

কোনো তৃতীয়-পক্ষ লাইব্রেরি লাগে না (শুধু Python-এর built-in মডিউল),
তাই GitHub Actions-এ `pip install` ছাড়াই চলে।

⚠️ গুরুত্বপূর্ণ: এই স্ক্রিপ্ট শুধু হেডলাইন + ছোট সারাংশ + মূল লিংক নেয়,
   পুরো আর্টিকেল কপি করে না (কপিরাইট মেনে চলার জন্য)।
"""

import html
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# ১. কনফিগারেশন — এখানে সাইটের নাম ও নিউজ সোর্স বদলাতে পারবে
# ---------------------------------------------------------------------------

SITE_NAME = "BIJOY NEWS 24"
SITE_TAGLINE = "সর্বশেষ জাতীয় ও আন্তর্জাতিক খবর, স্বয়ংক্রিয়ভাবে হালনাগাদ"

# একাধিক সোর্স দেওয়া হলো রিডানডেন্সির জন্য — একটা ফেইল করলে বাকিগুলো কাজ করবে।
FEEDS = [
    "https://risingbd.com/rss/rss.xml",
    "https://www.jagonews24.com/rss/rss.xml",
]

# RSS লিংকের URL অংশ দেখে ক্যাটাগরি বের করার ম্যাপ (risingbd স্টাইল)
CATEGORY_MAP = {
    "national": "জাতীয়",
    "bangladesh": "বাংলাদেশ",
    "politics": "রাজনীতি",
    "international": "আন্তর্জাতিক",
    "sports": "খেলাধুলা",
    "entertainment": "বিনোদন",
    "economics": "অর্থনীতি",
    "lifestyle": "লাইফস্টাইল",
    "campus": "ক্যাম্পাস",
    "health": "স্বাস্থ্য",
    "law-crime": "অপরাধ",
    "media": "মিডিয়া",
    "feature": "ফিচার",
    "opinion": "মতামত",
    "art-literature": "শিল্প-সাহিত্য",
    "risingbd-special": "বিশেষ প্রতিবেদন",
    "technology": "প্রযুক্তি",
    "education": "শিক্ষা",
}
DEFAULT_CATEGORY = "সর্বশেষ"

MAX_ITEMS_PER_FEED = 60
MAX_HERO_ITEMS = 3
MAX_PER_CATEGORY = 8
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")

# --- AI রি-রাইট কনফিগারেশন (সম্পূর্ণ ফ্রি — Google Gemini free tier) -------
# GEMINI_API_KEY একটা GitHub Actions "secret" হিসেবে সেট করতে হবে
# (README.md-এ ধাপগুলো লেখা আছে)। Google AI Studio থেকে বিনামূল্যে এই key
# পাওয়া যায় — কোনো কার্ড বা বিলিং লাগে না। key না থাকলে স্ক্রিপ্ট এমনিতেই
# মূল সারাংশ ব্যবহার করবে, ভেঙে পড়বে না।
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
AI_MODEL = "gemini-2.5-flash"            # Gemini-এর স্থায়ী ফ্রি-টায়ার মডেল
MAX_AI_REWRITES_PER_RUN = 20             # ফ্রি টায়ারের রেট-লিমিট (প্রতি মিনিটে ~১০টা রিকোয়েস্ট) মাথায় রেখে
AI_TIMEOUT = 20
AI_PACE_DELAY_SEC = 6.5                  # প্রতি কলের মাঝে বিরতি, রেট-লিমিটে যেন না লাগে
AI_RETRY_DELAY_SEC = 8

BD_TZ = timezone(timedelta(hours=6))


# ---------------------------------------------------------------------------
# ২. RSS ফেচ ও পার্স করা
# ---------------------------------------------------------------------------

def fetch_feed_bytes(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (BanglaNewsBot/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def guess_category_from_link(link: str) -> str:
    try:
        path = re.sub(r"^https?://[^/]+/", "", link)
        segment = path.split("/")[0].lower()
        return CATEGORY_MAP.get(segment, DEFAULT_CATEGORY)
    except Exception:
        return DEFAULT_CATEGORY


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def find_image(item_el) -> str:
    ns = {"media": "http://search.yahoo.com/mrss/"}
    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        el = item_el.find(tag)
        if el is not None and el.get("url"):
            return el.get("url")
    enclosure = item_el.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        return enclosure.get("url")
    content_encoded = item_el.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if content_encoded is not None and content_encoded.text:
        m = re.search(r'<img[^>]+src="([^"]+)"', content_encoded.text)
        if m:
            return m.group(1)
    return ""


def parse_rss_bytes(raw: bytes, source_name: str):
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items

    channel = root.find("channel")
    if channel is None:
        return items

    for item_el in channel.findall("item")[:MAX_ITEMS_PER_FEED]:
        title = strip_html(item_el.findtext("title", default=""))
        link = (item_el.findtext("link", default="") or "").strip()
        description = strip_html(item_el.findtext("description", default=""))
        pub_date_raw = item_el.findtext("pubDate", default="")
        image = find_image(item_el)

        if not title or not link:
            continue

        items.append({
            "title": title,
            "link": link,
            "summary": description[:220],
            "image": image,
            "category": guess_category_from_link(link),
            "source": source_name,
            "pub_date_raw": pub_date_raw,
        })
    return items


def source_name_from_url(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


def collect_all_items():
    all_items = []
    for feed_url in FEEDS:
        try:
            raw = fetch_feed_bytes(feed_url)
            src = source_name_from_url(feed_url)
            all_items.extend(parse_rss_bytes(raw, src))
            print(f"✔ ফিড সংগ্রহ সম্পন্ন: {feed_url} ({len(all_items)} সহ)")
        except Exception as exc:
            print(f"✘ ফিড ব্যর্থ হয়েছে ({feed_url}): {exc}", file=sys.stderr)
    # ডুপ্লিকেট লিংক বাদ দেওয়া
    seen = set()
    unique_items = []
    for it in all_items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        unique_items.append(it)
    return unique_items


# ---------------------------------------------------------------------------
# ৩. AI দিয়ে নিজস্ব ভাষায় ব্রিফ লেখা (ঐচ্ছিক — API key থাকলে চলবে)
# ---------------------------------------------------------------------------

def ai_rewrite_brief(title: str, summary: str) -> str:
    """RSS-এর ছোট সারাংশ থেকে ৪-৫ লাইনের একটা নিজস্ব ভাষায় লেখা নিউজ ব্রিফ
    বানায় (Google Gemini-এর ফ্রি টায়ার দিয়ে)। মূল আর্টিকেলের বাক্য/স্ট্রাকচার
    কপি করে না — শুধু তথ্যটা ভিত্তি ধরে নতুন করে লেখে। key না থাকলে বা
    ব্যর্থ হলে মূল সারাংশ ফেরত দেয়, স্ক্রিপ্ট কখনো ভেঙে পড়ে না।
    """
    if not GEMINI_API_KEY:
        return summary

    prompt = (
        "তুমি একজন বাংলা নিউজ এডিটর। নিচের হেডলাইন ও সংক্ষিপ্ত তথ্যের "
        "ভিত্তিতে ৪-৫ লাইনের একটা সহজ, স্বাভাবিক বাংলায় নিউজ ব্রিফ লেখো। "
        "সম্পূর্ণ নিজের ভাষায় লিখবে, দেওয়া বাক্যগুলো হুবহু কপি করবে না, "
        "নতুন কোনো তথ্য বানিয়ে বলবে না, শুধু ভাষা ও গঠন নতুন করে সাজাবে। "
        "শুধু ব্রিফের টেক্সট ফেরত দাও, আর কিছু না।\n\n"
        f"হেডলাইন: {title}\n"
        f"তথ্য: {summary}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 300, "temperature": 0.4},
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if not candidates:
                    return summary
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts).strip()
                return text or summary
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                time.sleep(AI_RETRY_DELAY_SEC)
                continue
            print(f"⚠ AI রি-রাইট ব্যর্থ ({exc.code}), মূল সারাংশ ব্যবহার হচ্ছে", file=sys.stderr)
            return summary
        except Exception as exc:
            print(f"⚠ AI রি-রাইট ব্যর্থ ({exc}), মূল সারাংশ ব্যবহার হচ্ছে", file=sys.stderr)
            return summary
    return summary


def apply_ai_rewrites(items):
    if not GEMINI_API_KEY:
        print("ℹ️  GEMINI_API_KEY সেট করা নেই — AI রি-রাইট ছাড়াই মূল সারাংশ ব্যবহার হচ্ছে।")
        return items
    count = 0
    for it in items:
        if count >= MAX_AI_REWRITES_PER_RUN:
            break
        it["summary"] = ai_rewrite_brief(it["title"], it["summary"])
        count += 1
        time.sleep(AI_PACE_DELAY_SEC)   # ফ্রি টায়ারের প্রতি-মিনিট রেট-লিমিট সম্মান করে চলার জন্য
    print(f"✅ AI দিয়ে {count}টি ব্রিফ রি-রাইট করা হয়েছে (ফ্রি Gemini টায়ার)")
    return items


# ---------------------------------------------------------------------------
# ৪. HTML তৈরি করা
# ---------------------------------------------------------------------------

CARD_TEMPLATE = """
<article class="card">
  {image_html}
  <div class="card-body">
    <span class="card-cat">{category}</span>
    <h3 class="card-title"><a href="{link}" target="_blank" rel="noopener">{title}</a></h3>
    <p class="card-summary">{summary}</p>
    <div class="card-meta">
      <span class="card-source">{source}</span>
      <a class="card-readmore" href="{link}" target="_blank" rel="noopener">মূল খবর পড়ুন →</a>
    </div>
  </div>
</article>
"""

HERO_TEMPLATE = """
<article class="hero-card {extra_class}">
  {image_html}
  <div class="hero-body">
    <span class="card-cat">{category}</span>
    <h2 class="hero-title"><a href="{link}" target="_blank" rel="noopener">{title}</a></h2>
    <p class="hero-summary">{summary}</p>
    <div class="card-meta">
      <span class="card-source">{source}</span>
      <a class="card-readmore" href="{link}" target="_blank" rel="noopener">মূল খবর পড়ুন →</a>
    </div>
  </div>
</article>
"""


def render_image(image_url: str, alt: str) -> str:
    if not image_url:
        return '<div class="card-img placeholder" aria-hidden="true"></div>'
    safe_alt = html.escape(alt)
    return f'<div class="card-img"><img src="{html.escape(image_url)}" alt="{safe_alt}" loading="lazy"></div>'


def render_card(item, template=CARD_TEMPLATE, extra_class=""):
    return template.format(
        image_html=render_image(item["image"], item["title"]),
        category=html.escape(item["category"]),
        link=html.escape(item["link"]),
        title=html.escape(item["title"]),
        summary=html.escape(item["summary"]),
        source=html.escape(item["source"]),
        extra_class=extra_class,
    )


def build_html(items):
    if not items:
        raise SystemExit("কোনো খবর পাওয়া যায়নি — সব ফিড ব্যর্থ হয়েছে।")

    now_bd = datetime.now(BD_TZ)
    updated_str = now_bd.strftime("%d %B %Y, %I:%M %p")

    hero_items = items[:MAX_HERO_ITEMS]
    rest_items = items[MAX_HERO_ITEMS:]

    # ক্যাটাগরি অনুযায়ী ভাগ করা, insertion অর্ডার বজায় রেখে
    by_category = {}
    for it in rest_items:
        by_category.setdefault(it["category"], []).append(it)

    ticker_items = items[:10]
    ticker_html = "".join(
        f'<a href="{html.escape(it["link"])}" target="_blank" rel="noopener">{html.escape(it["title"])}</a>'
        for it in ticker_items
    )

    hero_html = ""
    if hero_items:
        lead = hero_items[0]
        hero_html += render_card(lead, HERO_TEMPLATE, extra_class="hero-lead")
        for it in hero_items[1:]:
            hero_html += render_card(it, HERO_TEMPLATE, extra_class="hero-secondary")

    nav_links = "".join(
        f'<a href="#cat-{i}">{html.escape(cat)}</a>'
        for i, cat in enumerate(by_category.keys())
    )

    sections_html = ""
    for i, (cat, cat_items) in enumerate(by_category.items()):
        cards = "".join(render_card(it) for it in cat_items[:MAX_PER_CATEGORY])
        sections_html += f"""
        <section class="category-section" id="cat-{i}">
          <div class="section-heading">
            <h2>{html.escape(cat)}</h2>
            <span class="section-rule"></span>
          </div>
          <div class="card-grid">{cards}</div>
        </section>
        """

    sources_credit = ", ".join(sorted(set(it["source"] for it in items)))

    return PAGE_TEMPLATE.format(
        site_name=html.escape(SITE_NAME),
        tagline=html.escape(SITE_TAGLINE),
        updated=html.escape(updated_str),
        ticker_html=ticker_html,
        nav_links=nav_links,
        hero_html=hero_html,
        sections_html=sections_html,
        sources_credit=html.escape(sources_credit),
    )


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{site_name} — {tagline}</title>
<meta name="description" content="{tagline}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+Bengali:wght@500;700;900&family=Hind+Siliguri:wght@400;500;600;700&family=Anton&display=swap" rel="stylesheet">
<style>
  :root {{
    --paper: #FFFFFF;
    --ink: #111014;
    --ink-soft: #5C5860;
    --black: #0C0B0E;
    --red: #E4102B;
    --red-deep: #B80C22;
    --silver: #D7DADF;
    --card-bg: #FFFFFF;
    --hairline: #E8E6E9;
    --glow: rgba(228, 16, 43, 0.25);
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: 'Hind Siliguri', sans-serif;
    line-height: 1.6;
  }}
  a {{ color: inherit; text-decoration: none; }}
  h1, h2, h3 {{ font-family: 'Noto Serif Bengali', serif; margin: 0; }}

  /* ---------- মাস্টহেড: কালো + উজ্জ্বল লাল + সোনালি — এলিট এডিটোরিয়াল লুক ---------- */
  .masthead {{
    background: var(--black);
    position: relative;
    overflow: hidden;
    border-bottom: 3px solid var(--red);
  }}
  .masthead::before {{
    content: "";
    position: absolute; inset: 0;
    background: linear-gradient(100deg, transparent 0%, rgba(228,16,43,0.18) 45%, transparent 70%);
    background-size: 220% 100%;
    animation: sheen-sweep 7s ease-in-out infinite;
  }}
  @keyframes sheen-sweep {{
    0% {{ background-position: 200% 0; }}
    100% {{ background-position: -20% 0; }}
  }}
  .masthead::after {{
    content: "";
    position: absolute; inset: 0;
    background: radial-gradient(520px 200px at 85% 0%, rgba(255,255,255,0.06), transparent 70%);
  }}
  .masthead-inner {{
    max-width: 1160px;
    margin: 0 auto;
    padding: 26px 20px 22px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: 10px;
    position: relative;
  }}
  .brand {{ display: flex; align-items: center; gap: 14px; }}
  .brand .accent-bar {{
    width: 6px;
    align-self: stretch;
    background: linear-gradient(var(--red), var(--red-deep));
    border-radius: 2px;
  }}
  .masthead .brand h1 {{
    font-family: 'Anton', 'Arial Black', 'Impact', 'Hind Siliguri', sans-serif;
    font-size: clamp(2.2rem, 6vw, 3.5rem);
    font-weight: 400;
    letter-spacing: 1.5px;
    color: #FFFFFF;
    text-shadow: 0 0 22px rgba(228,16,43,0.4), 0 2px 2px rgba(0,0,0,0.5);
    line-height: 1;
  }}
  .masthead .brand p {{
    margin: 6px 0 0;
    color: rgba(255,255,255,0.6);
    font-size: 0.92rem;
    font-weight: 600;
    letter-spacing: 0.2px;
  }}
  .masthead .updated {{
    font-size: 0.85rem;
    color: rgba(255,255,255,0.55);
    text-align: right;
  }}
  @media (prefers-reduced-motion: reduce) {{
    .masthead::before {{ animation: none; }}
  }}

  /* ---------- ব্রেকিং টিকার ---------- */
  .ticker-bar {{
    background: var(--red);
    color: #fff;
    overflow: hidden;
    white-space: nowrap;
    position: relative;
    border-bottom: 1px solid rgba(0,0,0,0.15);
  }}
  .ticker-bar .ticker-label {{
    position: absolute;
    top: 0; bottom: 0; right: 0;
    background: var(--black);
    color: #FFFFFF;
    padding: 8px 18px;
    font-weight: 700;
    font-size: 0.85rem;
    display: flex;
    align-items: center;
    gap: 6px;
    z-index: 2;
    box-shadow: -14px 0 18px -6px rgba(0,0,0,0.35);
  }}
  .ticker-label .dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--red);
    animation: pulse-dot 1.4s ease-in-out infinite;
  }}
  @keyframes pulse-dot {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.35; transform: scale(0.7); }}
  }}
  .ticker-track {{
    display: inline-block;
    padding: 9px 0;
    padding-right: 150px;
    animation: scroll-ticker 55s linear infinite;
  }}
  .ticker-track a {{
    margin-right: 42px;
    font-size: 0.9rem;
    color: #FFFFFF;
  }}
  .ticker-track a:hover {{ color: var(--black); }}
  @keyframes scroll-ticker {{
    from {{ transform: translateX(0); }}
    to {{ transform: translateX(-50%); }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .ticker-track {{ animation: none; overflow-x: auto; }}
    .ticker-label .dot {{ animation: none; }}
  }}

  /* ---------- ক্যাটাগরি নেভ ---------- */
  .category-nav {{
    max-width: 1160px;
    margin: 0 auto;
    padding: 12px 20px;
    display: flex;
    gap: 24px;
    overflow-x: auto;
    border-bottom: 1px solid var(--hairline);
    font-size: 0.92rem;
    font-weight: 700;
  }}
  .category-nav a {{
    white-space: nowrap;
    color: var(--ink);
    position: relative;
    padding-bottom: 4px;
  }}
  .category-nav a::after {{
    content: "";
    position: absolute; left: 0; right: 100%; bottom: 0;
    height: 2px; background: var(--red);
    transition: right 0.25s ease;
  }}
  .category-nav a:hover {{ color: var(--red); }}
  .category-nav a:hover::after {{ right: 0; }}

  main {{ max-width: 1160px; margin: 0 auto; padding: 30px 20px 60px; }}

  /* ---------- হিরো গ্রিড ---------- */
  .hero-grid {{
    display: grid;
    grid-template-columns: 1.4fr 1fr;
    gap: 24px;
    margin-bottom: 50px;
  }}
  .hero-lead {{ grid-row: span 2; }}
  @media (max-width: 800px) {{
    .hero-grid {{ grid-template-columns: 1fr; }}
    .hero-lead {{ grid-row: auto; }}
  }}

  .hero-card, .card {{
    background: var(--card-bg);
    border: 1px solid var(--hairline);
    border-top: 3px solid transparent;
    transition: transform 0.22s ease, box-shadow 0.22s ease, border-color 0.22s ease;
  }}
  .hero-card:hover, .card:hover {{
    transform: translateY(-5px);
    box-shadow: 0 18px 36px -18px var(--glow), 0 4px 10px -6px rgba(0,0,0,0.08);
    border-top-color: var(--red);
  }}
  .hero-card .card-img img, .card .card-img img {{
    width: 100%; height: 100%; object-fit: cover; display: block;
    transition: transform 0.4s ease;
  }}
  .hero-card:hover .card-img img, .card:hover .card-img img {{ transform: scale(1.04); }}
  .hero-lead .card-img {{ aspect-ratio: 16/10; position: relative; overflow: hidden; }}
  .hero-lead .card-img::after {{
    content: "";
    position: absolute; inset: 0;
    background: linear-gradient(to top, rgba(12,11,14,0.6), transparent 55%);
  }}
  .hero-secondary {{ display: flex; }}
  .hero-secondary .card-img {{ width: 40%; flex-shrink: 0; aspect-ratio: 1/1; overflow: hidden; }}
  .hero-secondary .hero-body {{ flex: 1; }}
  .card-img.placeholder {{
    background: linear-gradient(135deg, var(--red), var(--black));
    aspect-ratio: 16/10;
  }}

  .card-cat {{
    display: inline-block;
    background: var(--red);
    color: #FFFFFF;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 2px;
    margin-bottom: 8px;
    letter-spacing: 0.4px;
  }}
  .hero-body, .card-body {{ padding: 18px 20px 20px; }}
  .hero-lead .hero-title {{ font-size: clamp(1.5rem, 3vw, 2.15rem); line-height: 1.32; }}
  .hero-secondary .hero-title {{ font-size: 1.05rem; }}
  .hero-title a:hover, .card-title a:hover {{ color: var(--red); }}
  .hero-summary {{ color: var(--ink-soft); margin: 10px 0 0; font-size: 0.95rem; }}
  .hero-secondary .hero-summary {{ display: none; }}

  .card-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 22px;
  }}
  .card-title {{ font-size: 1.05rem; line-height: 1.4; margin-top: 2px; }}
  .card-summary {{ color: var(--ink-soft); font-size: 0.88rem; margin: 8px 0 0; }}
  .card-img {{ aspect-ratio: 16/10; overflow: hidden; }}

  .card-meta {{
    margin-top: 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.78rem;
    color: var(--ink-soft);
    border-top: 1px solid var(--hairline);
    padding-top: 10px;
  }}
  .card-readmore {{ color: var(--red); font-weight: 700; }}

  .section-heading {{
    display: flex;
    align-items: center;
    gap: 14px;
    margin: 50px 0 22px;
  }}
  .section-heading h2 {{
    font-size: 1.55rem;
    color: var(--ink);
    white-space: nowrap;
    position: relative;
    padding-left: 16px;
  }}
  .section-heading h2::before {{
    content: "";
    position: absolute; left: 0; top: 6px; bottom: 6px; width: 5px;
    background: linear-gradient(var(--red), var(--red-deep));
  }}
  .section-rule {{ flex: 1; height: 1px; background: var(--hairline); }}
  .category-section:first-of-type .section-heading {{ margin-top: 0; }}

  footer {{
    background: var(--black);
    color: rgba(255,255,255,0.7);
    padding: 26px 20px 44px;
    font-size: 0.85rem;
    border-top: 3px solid var(--red);
  }}
  footer .footer-inner {{ max-width: 1160px; margin: 0 auto; }}
  footer strong {{ color: #FFFFFF; }}

  :focus-visible {{ outline: 2px solid var(--red); outline-offset: 2px; }}
</style>
</head>
<body>

  <header class="masthead">
    <div class="masthead-inner">
      <div class="brand">
        <span class="accent-bar" aria-hidden="true"></span>
        <div>
          <h1>{site_name}</h1>
          <p>{tagline}</p>
        </div>
      </div>
      <div class="updated">সর্বশেষ হালনাগাদ: {updated}</div>
    </div>
  </header>

  <div class="ticker-bar">
    <div class="ticker-track">{ticker_html}{ticker_html}</div>
    <span class="ticker-label"><span class="dot"></span> ব্রেকিং</span>
  </div>

  <nav class="category-nav">{nav_links}</nav>

  <main>
    <div class="hero-grid">{hero_html}</div>
    {sections_html}
  </main>

  <footer>
    <div class="footer-inner">
      <p><strong>{site_name}</strong> একটি স্বয়ংক্রিয় নিউজ অ্যাগ্রিগেটর — খবরের সংক্ষিপ্ত ব্রিফ AI দিয়ে নিজস্ব ভাষায় লেখা, পুরো খবর পড়তে মূল সংবাদমাধ্যমের লিংকে যান।</p>
      <p>খবরের সূত্র: {sources_credit}</p>
    </div>
  </footer>

</body>
</html>
"""


def main():
    items = collect_all_items()
    items = apply_ai_rewrites(items)
    page = build_html(items)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"✅ সাইট তৈরি সম্পন্ন: {out_path} ({len(items)}টি খবর)")


if __name__ == "__main__":
    main()
