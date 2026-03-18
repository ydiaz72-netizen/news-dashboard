#!/usr/bin/env python3
"""
news_scraper.py — generates index.html for the World News dashboard.
Fetches RSS feeds, downloads images, uses Claude API for region classification,
bias detection, and editorial analysis.
"""

import os
import re
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError
from bs4 import BeautifulSoup
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

MODEL = "claude-opus-4-6"
OUT_HTML = "index.html"
IMG_DIR = "index_files"
MAX_PER_FEED = 15

RSS_FEEDS = {
    "The Guardian":            "https://www.theguardian.com/world/rss",
    "BBC News":                "http://feeds.bbci.co.uk/news/world/rss.xml",
    "Al Jazeera":              "https://www.aljazeera.com/xml/rss/all.xml",
    "DW News":                 "https://rss.dw.com/xml/rss-en-world",
    "France 24":               "https://www.france24.com/en/rss",
    "Euronews":                "https://feeds.feedburner.com/euronews/en/news/",
    "CBC News":                "https://www.cbc.ca/cmlink/rss-world",
    "NPR":                     "https://feeds.npr.org/1001/rss.xml",
    "NPR World":               "https://feeds.npr.org/1004/rss.xml",
    "New York Times":          "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "South China Morning Post":"https://www.scmp.com/rss/2/feed",
    "ABC Australia":           "https://www.abc.net.au/news/feed/51120/rss.xml",
    "AllAfrica":               "https://allafrica.com/tools/headlines/rdf.xml",
    "CNN":                     "http://rss.cnn.com/rss/edition_world.rss",
}

SOURCE_COLORS = {
    "The Guardian":            "#005689",
    "Al Jazeera":              "#e8a000",
    "BBC News":                "#bb1919",
    "CBC News":                "#c8132a",
    "CNN":                     "#cc0001",
    "DW News":                 "#c8002d",
    "Euronews":                "#0057a8",
    "France 24":               "#003f87",
    "NPR":                     "#1a6196",
    "NPR World":               "#1a6196",
    "New York Times":          "#4a4a4a",
    "South China Morning Post":"#1c5c8a",
    "ABC Australia":           "#00427a",
    "AllAfrica":               "#2d7a2d",
}

REGIONS = ["Middle East", "Europe", "Americas", "Asia & Pacific", "Africa", "Global / Other"]

REGION_COLORS = {
    "Middle East":    "#c0392b",
    "Europe":         "#2563eb",
    "Americas":       "#7c3aed",
    "Asia & Pacific": "#059669",
    "Africa":         "#d97706",
    "Global / Other": "#475569",
}

BIAS_TAG_COLORS = {
    "Opinion/Editorial":  "#e67e22",
    "Sensationalist":     "#e74c3c",
    "Political Slant":    "#9b59b6",
    "Misleading Headline":"#c0392b",
    "Cherry-picked Data": "#d35400",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def safe_filename(title: str, idx: int) -> str:
    s = re.sub(r"[^\w\s]", "_", title)
    s = re.sub(r"\s+", "_", s).strip("_")
    return f"{s[:60]}_{idx}.jpg"


def parse_date(date_str: str) -> str:
    if not date_str:
        return ""
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        from dateutil import parser as dp
        return dp.parse(date_str).strftime("%Y-%m-%d")
    except Exception:
        pass
    return date_str[:10] if len(date_str) >= 10 else date_str


def extract_image_url(item: ET.Element, ns: dict) -> str:
    """Extract best image URL from an RSS <item> element."""
    media_ns = "http://search.yahoo.com/mrss/"
    for tag in (f"{{{media_ns}}}content", f"{{{media_ns}}}thumbnail"):
        el = item.find(tag)
        if el is not None:
            url = el.get("url", "")
            if url.startswith("http"):
                return url
    el = item.find("enclosure")
    if el is not None:
        url = el.get("url", "")
        mtype = el.get("type", "")
        if url.startswith("http") and "image" in mtype:
            return url
    desc_el = item.find("description")
    if desc_el is not None and desc_el.text:
        soup = BeautifulSoup(desc_el.text, "html.parser")
        img = soup.find("img")
        if img and str(img.get("src", "")).startswith("http"):
            return img["src"]
    return ""


def fetch_rss(source: str, url: str, max_items: int = MAX_PER_FEED) -> list:
    articles = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        content = resp.content
    except Exception as e:
        print(f"  [WARN] Could not fetch {source}: {e}")
        return []
    try:
        root = ET.fromstring(content)
    except ParseError as e:
        print(f"  [WARN] Could not parse XML for {source}: {e}")
        return []

    ns = {"dc": "http://purl.org/dc/elements/1.1/"}

    # RSS 2.0
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else []

    # Atom
    if not items:
        atom_ns = "http://www.w3.org/2005/Atom"
        items = root.findall(f"{{{atom_ns}}}entry")

    # RDF 1.0
    if not items:
        items = root.findall("{http://purl.org/rss/1.0/}item")

    for i, item in enumerate(items[:max_items]):
        def text(tag):
            el = item.find(tag, ns)
            return (el.text or "").strip() if el is not None else ""

        title = text("title") or ""
        if "<" in title:
            title = BeautifulSoup(title, "html.parser").get_text()
        title = title.strip()

        link_el = item.find("link")
        link = ""
        if link_el is not None:
            link = (link_el.text or "").strip()
            if not link:
                link = link_el.get("{http://www.w3.org/1999/xlink}href", "").strip()
        if not link:
            link = text("guid")

        pub_date = ""
        for dtag in (
            "pubDate",
            "{http://purl.org/dc/elements/1.1/}date",
            "{http://www.w3.org/2005/Atom}updated",
            "{http://www.w3.org/2005/Atom}published",
        ):
            el = item.find(dtag)
            if el is not None and el.text:
                pub_date = parse_date(el.text.strip())
                break

        summary = ""
        for stag in (
            "description",
            "summary",
            "{http://www.w3.org/2005/Atom}summary",
            "{http://www.w3.org/2005/Atom}content",
        ):
            el = item.find(stag)
            if el is not None and el.text:
                summary = BeautifulSoup(el.text, "html.parser").get_text(" ", strip=True)[:400]
                break

        img_url = extract_image_url(item, ns)

        if title and link:
            articles.append({
                "source": source,
                "title": title,
                "url": link,
                "date": pub_date,
                "summary": summary,
                "img_url": img_url,
                "local_img": "",
                "region": "",
                "biased": False,
                "bias_tags": [],
                "bias_explanation": "",
                "who_benefits": "",
            })

    return articles


def download_image(img_url: str, filename: str, img_dir: str) -> str:
    if not img_url:
        return ""
    dest = os.path.join(img_dir, filename)
    if os.path.exists(dest):
        return f"{img_dir}/{filename}"
    try:
        resp = requests.get(img_url, headers=HEADERS, timeout=15, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and "octet" not in content_type:
            return ""
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return f"{img_dir}/{filename}"
    except Exception as e:
        print(f"  [WARN] Image download failed: {e}")
        return ""


def classify_and_detect_bias(client: Anthropic, articles: list) -> list:
    """Classify articles by region and detect bias in one Claude call."""
    summaries = []
    for i, a in enumerate(articles):
        summaries.append(
            f'{i}. SOURCE={a["source"]} | TITLE={a["title"][:120]} | SUMMARY={a["summary"][:200]}'
        )

    prompt = f"""Classify each news article by region and detect bias.

Allowed regions: "Middle East", "Europe", "Americas", "Asia & Pacific", "Africa", "Global / Other"

Bias detection rules:
- Opinion/editorial pieces → biased=true, tags=["Opinion/Editorial"]
- Sensationalist language → biased=true, tags=["Sensationalist"]
- Clear political slant → biased=true, tags=["Political Slant"]
- Straight factual reporting → biased=false, tags=[]

For each article return a JSON object:
{{"idx": N, "region": "...", "biased": true/false, "bias_tags": [...], "bias_explanation": "one sentence", "who_benefits": "SOURCE leans LEAN. Short note."}}

Articles:
{chr(10).join(summaries)}

Return a JSON array only. No markdown, no extra text."""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            results = json.loads(m.group())
            for r in results:
                idx = r.get("idx")
                if idx is not None and 0 <= idx < len(articles):
                    articles[idx]["region"] = r.get("region", "Global / Other")
                    articles[idx]["biased"] = bool(r.get("biased", False))
                    articles[idx]["bias_tags"] = r.get("bias_tags", [])
                    articles[idx]["bias_explanation"] = r.get("bias_explanation", "")
                    articles[idx]["who_benefits"] = r.get("who_benefits", "")
    except Exception as e:
        print(f"  [WARN] Classification failed: {e}")

    # Fallback for any unclassified articles
    for a in articles:
        if not a["region"]:
            a["region"] = "Global / Other"

    return articles


def generate_analysis(client: Anthropic, articles: list, region: str = None) -> dict:
    """Generate 'Today in the News' + 'What to Expect Next' bullets."""
    scope_label = f"the {region} region" if region else "global news"
    summaries = "\n".join(
        f"- [{a['source']}] {a['title']}: {a['summary'][:200]}"
        for a in articles[:30]
    )

    prompt = f"""Based on these news articles covering {scope_label}, write two lists:

1. "today": 5–6 bullet points summarising the key news developments.
   Each bullet should be 2–3 sentences, factual and specific (include names, places, numbers).

2. "forward": 5 bullet points of forward-looking analysis grounded in historical patterns.
   Each bullet should be 2–3 sentences and reference relevant historical precedents.

Articles:
{summaries}

Return JSON only: {{"today": ["bullet1", "bullet2", ...], "forward": ["bullet1", ...]}}
No markdown fences, no extra text."""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"  [WARN] Analysis failed for {scope_label}: {e}")

    return {"today": [], "forward": []}


# ── HTML rendering ─────────────────────────────────────────────────────────────

CSS = """@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}
a{color:#60a5fa;text-decoration:none}
a:hover{text-decoration:underline}

/* ── Sticky header ── */
.site-header{
  position:sticky;top:0;z-index:100;
  background:rgba(15,17,23,0.95);backdrop-filter:blur(10px);
  border-bottom:1px solid #1e2535;padding:12px 16px;
  display:flex;align-items:center;gap:12px;flex-wrap:wrap
}
.site-header h1{font-size:1.05em;font-weight:700;color:#f1f5f9}
.site-header h1 small{font-size:0.6em;font-weight:400;color:#64748b;margin-left:6px}
.stats-pills{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.pill{background:#1e2535;color:#94a3b8;border-radius:20px;
      padding:3px 10px;font-size:0.72em;font-weight:500;white-space:nowrap}
.pill b{color:#e2e8f0}
.pill.warn b{color:#f87171}

/* ── Main layout ── */
.page-wrap{max-width:1280px;margin:0 auto;padding:16px 12px}

/* ── Filter bar ── */
.filter-bar{
  display:flex;gap:8px;align-items:center;
  margin-bottom:20px;
  overflow-x:auto;-webkit-overflow-scrolling:touch;
  padding-bottom:6px;scrollbar-width:none
}
.filter-bar::-webkit-scrollbar{display:none}
.filter-bar span{font-size:0.78em;color:#64748b;white-space:nowrap;margin-right:2px}
.filter-btn{
  background:#1e2535;border:1px solid #2d3748;color:#94a3b8;
  border-radius:20px;padding:6px 14px;font-size:0.78em;cursor:pointer;
  font-family:inherit;transition:all .15s;white-space:nowrap;flex-shrink:0
}
.filter-btn:hover,.filter-btn.active{
  background:#2563eb;border-color:#2563eb;color:#fff
}

/* ── Analysis grid ── */
.analysis-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:24px}
.analysis-box{background:#161b27;border-radius:12px;padding:18px 20px;
              border:1px solid #1e2535}
.analysis-box.today{border-top:3px solid #2563eb}
.analysis-box.expect{border-top:3px solid #059669}
.analysis-box .box-label{font-size:0.68em;font-weight:600;text-transform:uppercase;
                          letter-spacing:.1em;color:#64748b;margin-bottom:4px}
.analysis-box h2{font-size:1em;font-weight:700;color:#f1f5f9;margin-bottom:12px}
.analysis-box ul{padding-left:16px}
.analysis-box li{margin-bottom:9px;line-height:1.6;font-size:0.855em;color:#cbd5e1}

/* ── Article cards ── */
.cards-section{display:flex;flex-direction:column;gap:14px}
.card{
  background:#161b27;border-radius:12px;
  border:1px solid #1e2535;
  display:flex;flex-direction:row;gap:0;overflow:hidden;
  transition:border-color .15s,box-shadow .15s
}
.card:hover{border-color:#2d3748;box-shadow:0 4px 24px rgba(0,0,0,.4)}
.card.biased{border-left:3px solid #ef4444}

.card-image{flex:0 0 260px;max-width:260px;position:relative}
.card-image img{width:100%;height:100%;object-fit:cover;display:block}
.card-image .no-img{
  width:100%;height:100%;min-height:160px;background:linear-gradient(135deg,#1a1f2e 0%,#0f1117 100%);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:10px;border-right:1px solid #1e2535
}
.card-image .no-img svg{opacity:0.25}
.card-image .no-img span{font-size:0.72em;font-weight:500;color:#374151;
                          letter-spacing:.06em;text-transform:uppercase}

.card-body{flex:1;padding:16px 18px;display:flex;flex-direction:column;gap:9px;min-width:0}

.card-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.source-badge{
  border-radius:4px;padding:3px 10px;font-size:0.68em;font-weight:600;
  color:#fff;letter-spacing:.02em;white-space:nowrap
}
.date-label{font-size:0.72em;color:#64748b}

.card-title{font-size:0.97em;font-weight:600;line-height:1.45;color:#f1f5f9}
.card-summary{font-size:0.84em;color:#94a3b8;line-height:1.65}
.read-link{font-size:0.8em;color:#60a5fa;font-weight:500}

.bias-tags{display:flex;gap:6px;flex-wrap:wrap}
.bias-badge{border-radius:4px;padding:2px 9px;font-size:0.7em;
            font-weight:500;color:#fff;white-space:nowrap}

.bias-panel{background:#1a1f2e;border:1px solid #2d3748;border-radius:8px;
            padding:12px 14px;margin-top:2px}
.bias-panel .bp-label{font-size:0.68em;font-weight:700;text-transform:uppercase;
                       letter-spacing:.08em;color:#f87171;margin-bottom:8px;display:block}
.bias-panel .bp-expl{font-size:0.8em;color:#cbd5e1;line-height:1.6;margin-bottom:10px}
.who-box{background:#1e1a0e;border:1px solid #44370a;border-radius:6px;
         padding:9px 12px;font-size:0.78em;color:#fcd34d;line-height:1.55}
.who-box b{color:#fde68a}

/* ── Region sections ── */
.region-section{margin-bottom:40px}
.region-header{
  display:flex;align-items:center;gap:12px;
  padding:10px 16px;margin-bottom:16px;
  background:#161b27;border-radius:8px;border:1px solid #1e2535
}
.region-label{font-size:0.7em;font-weight:800;letter-spacing:.12em}
.region-count{font-size:0.75em;color:#64748b;margin-left:auto}
.region-analysis{margin-bottom:16px}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:#0f1117}
::-webkit-scrollbar-thumb{background:#2d3748;border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:#4a5568}

/* ── Mobile (max 640px) ── */
@media(max-width:640px){
  .site-header{padding:10px 12px;gap:8px}
  .site-header h1{font-size:0.95em}
  .stats-pills{gap:4px}
  .pill{font-size:0.68em;padding:2px 8px}
  .page-wrap{padding:12px 10px}
  .analysis-grid{grid-template-columns:1fr}
  .card{flex-direction:column}
  .card-image{flex:none;max-width:100%;width:100%;height:200px}
  .card-image img{height:200px;width:100%;object-fit:cover}
  .card-image .no-img{min-height:120px;height:120px}
  .card-body{padding:14px 14px}
  .card-title{font-size:0.95em}
  .card-summary{font-size:0.82em}
}

/* ── Tablet (641px – 900px) ── */
@media(min-width:641px) and (max-width:900px){
  .card-image{flex:0 0 200px;max-width:200px}
  .analysis-grid{grid-template-columns:1fr}
  .page-wrap{padding:16px 14px}
}"""

JS = """function filterSource(source) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.currentTarget.classList.add('active');
  document.querySelectorAll('.card[data-source]').forEach(c => {
    c.style.display = (source === 'all' || c.dataset.source === source) ? 'flex' : 'none';
  });
  // Show/hide region sections based on visible cards
  document.querySelectorAll('.region-section').forEach(sec => {
    const visible = sec.querySelectorAll('.card[style*="flex"], .card:not([style])').length;
    sec.style.display = (source === 'all') ? 'block' : (sec.querySelectorAll('.card[data-source="' + source + '"]').length ? 'block' : 'none');
  });
}
function filterRegion(region) {
  document.querySelectorAll('.region-tab').forEach(b => b.classList.remove('active'));
  event.currentTarget.classList.add('active');
  document.querySelectorAll('.region-section').forEach(sec => {
    sec.style.display = (region === 'all' || sec.dataset.region === region) ? 'block' : 'none';
  });
  // Reset source filter
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.card').forEach(c => c.style.display = 'flex');
  document.querySelector('.filter-btn[data-src="all"]').classList.add('active');
}"""

NO_IMG = (
    "<div class='no-img'>"
    "<svg width='40' height='40' viewBox='0 0 24 24' fill='none' stroke='#94a3b8' stroke-width='1.2'>"
    "<rect x='3' y='3' width='18' height='18' rx='2'/>"
    "<circle cx='8.5' cy='8.5' r='1.5'/>"
    "<polyline points='21 15 16 10 5 21'/>"
    "</svg>"
    "<span>No image available</span>"
    "</div>"
)


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )


def render_card(a: dict) -> str:
    color = SOURCE_COLORS.get(a["source"], "#374151")
    biased_class = " biased" if a["biased"] else ""

    img_html = (
        f"<img src='{esc(a['local_img'])}' alt='' loading='lazy'>"
        if a["local_img"] else NO_IMG
    )

    bias_tags_html = ""
    if a["bias_tags"]:
        tags = "".join(
            f'<span class="bias-badge" style="background:{BIAS_TAG_COLORS.get(t, "#7f8c8d")}">'
            f"{esc(t)}</span>"
            for t in a["bias_tags"]
        )
        bias_tags_html = f"<div class='bias-tags'>{tags}</div>"

    bias_panel_html = ""
    if a["biased"] and (a["bias_explanation"] or a["who_benefits"]):
        bias_panel_html = (
            "<div class='bias-panel'>"
            "<span class='bp-label'>Bias Analysis</span>"
            f"<div class='bp-expl'><b>What makes it biased:</b><br>{esc(a['bias_explanation'])}</div>"
            f"<div class='who-box'><b>Who this benefits:</b><br>{esc(a['who_benefits'])}</div>"
            "</div>"
        )

    summary_html = (
        f"<div class='card-summary'>{esc(a['summary'])}</div>" if a["summary"] else ""
    )

    return (
        f"<div class='card{biased_class}' data-source='{esc(a['source'])}'>\n"
        f"  <div class='card-image'>\n    {img_html}\n  </div>\n"
        f"  <div class='card-body'>\n"
        f"    <div class='card-top'>\n"
        f"      <span class='source-badge' style='background:{color}'>{esc(a['source'])}</span>\n"
        f"      <span class='date-label'>{esc(a['date'])}</span>\n"
        f"    </div>\n"
        f"    <div class='card-title'>{esc(a['title'])}</div>\n"
        f"    {bias_tags_html}\n"
        f"    <a class='read-link' href='{esc(a['url'])}' target='_blank'>Read full article &rarr;</a>\n"
        f"    {summary_html}\n"
        f"    {bias_panel_html}\n"
        f"  </div>\n"
        f"</div>"
    )


def render_html(
    articles: list,
    global_analysis: dict,
    region_analyses: dict,
    today: str,
    all_sources: list,
) -> str:
    total = len(articles)
    flagged = sum(1 for a in articles if a["biased"])
    by_region = {r: [a for a in articles if a["region"] == r] for r in REGIONS}

    dates = sorted(d for a in articles if (d := a["date"]))
    date_range = (
        f"{dates[0]} &mdash; {dates[-1]}" if len(dates) >= 2
        else (dates[0] if dates else today)
    )

    parts = [
        f"<!DOCTYPE html><html lang='en'>\n"
        f"<head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        f"<title>World News — {today}</title>\n"
        f"<style>\n{CSS}\n</style>\n\n"
        f"<script>\n{JS}\n</script>\n"
        f"</head><body>\n",

        f"<header class='site-header'>\n"
        f"  <h1>World News <small>{date_range}</small></h1>\n"
        f"  <div class='stats-pills'>\n"
        f"    <span class='pill'><b>{total}</b> articles</span>\n"
        f"    <span class='pill warn'><b>{flagged}</b> flagged</span>\n"
        f"  </div>\n"
        f"</header>\n",

        "<div class='page-wrap'>\n",
    ]

    # Global analysis boxes
    g_today = "".join(f"<li>{esc(b)}</li>\n" for b in global_analysis.get("today", []))
    g_fwd = "".join(f"<li>{esc(b)}</li>\n" for b in global_analysis.get("forward", []))
    parts.append(
        f"<div class='analysis-grid'>\n"
        f"  <div class='analysis-box today'>\n"
        f"    <div class='box-label'>Global Overview</div>\n"
        f"    <h2>Today in the News</h2>\n"
        f"   <ul>\n{g_today}</ul>\n"
        f"  </div>\n"
        f"  <div class='analysis-box expect'>\n"
        f"    <div class='box-label'>Forward Look</div>\n"
        f"    <h2>What to Expect Next</h2>\n"
        f"   <ul>\n{g_fwd}</ul>\n"
        f"  </div>\n"
        f"</div>\n"
    )

    # Region filter bar
    region_btns = [
        "<button class='region-tab filter-btn active' onclick='filterRegion(\"all\")'>All Regions</button>"
    ]
    for r in REGIONS:
        if not by_region[r]:
            continue
        c = REGION_COLORS[r]
        count = len(by_region[r])
        region_btns.append(
            f"<button class='region-tab filter-btn' style='border-color:{c}' "
            f"onclick='filterRegion(\"{r}\")'>{r} <span style='opacity:.7'>({count})</span></button>"
        )
    parts.append(
        "<div class='filter-bar' style='margin-bottom:10px'>\n  <span>Jump to region:</span>\n  "
        + "\n  ".join(region_btns) + "\n</div>\n"
    )

    # Source filter bar
    src_btns = [
        "<button class='filter-btn active' data-src='all' onclick='filterSource(\"all\")'>All</button>"
    ]
    for s in sorted(all_sources):
        src_btns.append(
            f"<button class='filter-btn' data-src='{esc(s)}' "
            f"onclick='filterSource(\"{esc(s)}\")'>{esc(s)}</button>"
        )
    parts.append(
        "<div class='filter-bar'>\n  <span>Filter by source:</span>\n  "
        + "\n  ".join(src_btns) + "\n</div>\n"
    )

    # Region sections
    for region in REGIONS:
        arts = by_region[region]
        if not arts:
            continue
        color = REGION_COLORS[region]
        r_analysis = region_analyses.get(region, {"today": [], "forward": []})

        parts.append(f"<div class='region-section' data-region='{esc(region)}'>\n")
        parts.append(
            f"  <div class='region-header' style='border-left:4px solid {color}'>\n"
            f"    <span class='region-label' style='color:{color}'>{region.upper()}</span>\n"
            f"    <span class='region-count'>{len(arts)} articles</span>\n"
            f"  </div>\n"
        )

        r_today_bullets = r_analysis.get("today", [])
        r_fwd_bullets = r_analysis.get("forward", [])
        if r_today_bullets or r_fwd_bullets:
            r_today = "".join(f"<li>{esc(b)}</li>\n" for b in r_today_bullets)
            r_fwd = "".join(f"<li>{esc(b)}</li>\n" for b in r_fwd_bullets)
            parts.append(
                f"  <div class='analysis-grid region-analysis'>\n"
                f"    <div class='analysis-box today'>\n"
                f"      <div class='box-label'>{esc(region)}</div>\n"
                f"      <h2>Today in the News</h2>\n"
                f"      <ul>\n{r_today}</ul>\n"
                f"    </div>\n"
                f"    <div class='analysis-box expect'>\n"
                f"      <div class='box-label'>{esc(region)} — Forward Look</div>\n"
                f"      <h2>What to Expect Next</h2>\n"
                f"      <ul>\n{r_fwd}</ul>\n"
                f"    </div>\n"
                f"  </div>\n"
            )

        parts.append("  <div class='cards-section'>\n")
        for a in arts:
            parts.append(render_card(a) + "\n")
        parts.append("  </div>\n</div>\n")

    parts.append("</div>\n</body></html>")
    return "".join(parts)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== News Dashboard Generator — {today} ===\n")

    Path(IMG_DIR).mkdir(exist_ok=True)
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # 1. Fetch RSS feeds
    all_articles = []
    for source, url in RSS_FEEDS.items():
        print(f"Fetching {source}...")
        arts = fetch_rss(source, url)
        print(f"  → {len(arts)} articles")
        all_articles.extend(arts)
    print(f"\nTotal fetched: {len(all_articles)}")

    # 2. Download images
    print("\nDownloading images...")
    for i, a in enumerate(all_articles):
        if a["img_url"]:
            fname = safe_filename(a["title"], i)
            a["local_img"] = download_image(a["img_url"], fname, IMG_DIR)

    # 3. Classify + detect bias in batches of 50
    print("\nClassifying articles and detecting bias...")
    chunk_size = 50
    for start in range(0, len(all_articles), chunk_size):
        chunk = all_articles[start : start + chunk_size]
        print(f"  Batch {start}–{start + len(chunk)}...")
        classify_and_detect_bias(client, chunk)

    # 4. Generate analyses
    print("\nGenerating analyses...")
    print("  Global analysis...")
    global_analysis = generate_analysis(client, all_articles)

    by_region = {r: [a for a in all_articles if a["region"] == r] for r in REGIONS}
    region_analyses = {}
    for region, arts in by_region.items():
        if arts:
            print(f"  {region} ({len(arts)} articles)...")
            region_analyses[region] = generate_analysis(client, arts, region)

    # 5. Render
    all_sources = sorted(set(a["source"] for a in all_articles))
    print("\nRendering HTML...")
    html = render_html(all_articles, global_analysis, region_analyses, today, all_sources)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    flagged = sum(1 for a in all_articles if a["biased"])
    print(f"\n✅  {OUT_HTML} written")
    print(f"   {len(all_articles)} articles | {flagged} flagged | {len(all_sources)} sources")


if __name__ == "__main__":
    main()
