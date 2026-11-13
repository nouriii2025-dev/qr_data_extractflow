"""Fetch a page and pull out every label/value pair it exposes."""
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; GRN-Scanner/1.0)"
MIN_FIELDS_BEFORE_RENDER = 4
NOISE_KEYS = re.compile(r"build.?id|runtime|asset.?prefix|css|script|nonce|csrf|token|favicon", re.I)


class ExtractError(Exception):
    pass


# ---------- safety ----------
def check_url(url: str) -> str:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ExtractError("That scan is not a web address. Scan the QR code again.")
    try:
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ExtractError("Addresses on private networks are blocked.")
    except socket.gaierror:
        raise ExtractError("Could not find that website. Check the connection.")
    return url


# ---------- helpers ----------
def _clean(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def _nice(key):
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).replace("_", " ").replace("-", " ")
    return key.strip().title()


def _add(out, key, val):
    key, val = _clean(key).rstrip(":"), _clean(val)
    if not key or not val or len(key) > 80 or len(val) > 500 or NOISE_KEYS.search(key):
        return
    if key not in out:
        out[key] = val


def _flatten(obj, out, prefix="", depth=0):
    if depth > 5:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("@"):
                continue
            _flatten(v, out, f"{prefix} › {_nice(k)}" if prefix else _nice(k), depth + 1)
    elif isinstance(obj, list):
        if all(not isinstance(i, (dict, list)) for i in obj):
            _add(out, prefix, ", ".join(str(i) for i in obj if i not in (None, "")))
        else:
            for i, v in enumerate(obj[:30], 1):
                _flatten(v, out, f"{prefix} {i}", depth + 1)
    elif obj not in (None, ""):
        _add(out, prefix, obj)


# ---------- parsers ----------
def _from_scripts(soup, out):
    for s in soup.find_all("script"):
        t = (s.get("type") or "").lower()
        if t in ("application/ld+json", "application/json") or s.get("id") == "__NEXT_DATA__":
            try:
                _flatten(json.loads(s.string or ""), out)
            except (ValueError, TypeError):
                pass


def _from_tables(soup, out):
    for table in soup.find_all("table"):
        rows = [[_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        if all(len(r) == 2 for r in rows):
            for k, v in rows:
                _add(out, k, v)
        elif len(rows) > 1 and len(rows[0]) > 1:
            head = rows[0]
            for n, row in enumerate(rows[1:], 1):
                for h, v in zip(head, row):
                    _add(out, h if n == 1 else f"{h} (row {n})", v)


def _from_definition_lists(soup, out):
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                _add(out, dt.get_text(" "), dd.get_text(" "))


def _from_label_value_blocks(soup, out):
    for el in soup.find_all(class_=re.compile(r"label|key", re.I)):
        sib = el.find_next_sibling()
        if sib and len(sib.get_text(strip=True)) < 300:
            _add(out, el.get_text(" "), sib.get_text(" "))


def _from_meta(soup, out):
    for m in soup.find_all("meta"):
        name = m.get("property") or m.get("name") or ""
        if name.startswith(("og:", "product:")) or name in ("description", "author", "keywords"):
            if name not in ("og:image", "og:url"):
                _add(out, _nice(name.replace(":", " ")), m.get("content", ""))


def _from_text_lines(soup, out):
    for line in soup.get_text("\n").splitlines():
        m = re.match(r"^\s*([A-Za-z][\w /().-]{1,40}?)\s*:\s*(\S.{0,200})$", line)
        if m:
            _add(out, m.group(1), m.group(2))


# ---------- fetching ----------
def _render_with_playwright(url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA)
        page.goto(url, wait_until="networkidle", timeout=30000)
        html = page.content()
        browser.close()
    return html


def _parse(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    _from_scripts(soup, out)
    for s in soup(["script", "style", "noscript"]):
        s.decompose()
    for fn in (_from_tables, _from_definition_lists, _from_label_value_blocks, _from_meta, _from_text_lines):
        fn(soup, out)
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"]
    elif soup.title and soup.title.string:
        title = soup.title.string
    elif soup.h1:
        title = soup.h1.get_text(" ")
    return _clean(title), out


# ---------- key fields for goods receiving ----------
KEY_LABELS = ["Type", "Instrument name", "Serial number", "Item number",
              "Model number", "Range", "Size", "Mounting"]

# Label wording differs between suppliers, so each field has several accepted labels.
FIELD_RULES = {
    "Type": [r"^type$", r"^product type$", r"^(instrument|gauge|series|model) type$", r"^type (code|designation)$"],
    "Instrument name": [r"^(instrument|product|device) (name|description)$", r"^(instrument|product|device)$",
                        r"^product designation$", r"^designation$", r"^description$", r"^name$"],
    "Serial number": [r"serial", r"^s n$", r"^sn$"],
    "Item number": [r"^item( number| no| nr| code)?$", r"article", r"^material( number| no)?$",
                    r"part (number|no)", r"^order (number|no|code)"],
    "Model number": [r"^model( number| no| code)?$", r"^model "],
    "Range": [r"range", r"scale", r"measuring", r"^pressure$"],
    "Size": [r"^size$", r"connection size", r"thread", r"process connection", r"^connection$", r"^port"],
    "Mounting": [r"mount", r"connection (position|location|orientation)", r"^orientation$"],
}

TYPE_RE = re.compile(r"\b(?:Type\s*)?\d{2}[\dX]\.\d{2}\b(?!\.\d)", re.I)      # e.g. Type 232.50 / 23X.50
MODEL_RE = re.compile(r"\b\d{3}\.\d{2}\.\d{2,3}\b")                          # e.g. 232.50.063
SIZE_RE = re.compile(r"\b(?:G|R|Rp|Rc)\s?\d+(?:/\d+)?(?:\s?B)?\b|\b\d+(?:[-\s]\d+)?(?:/\d+)?\s?(?:NPT|BSPP|BSPT|BSP)\b|\bM\d+\s?x\s?[\d.]+", re.I)
MOUNT_RE = re.compile(r"\b(?:(?:lower|bottom|radial|center|centre|back|panel|surface|flush|wall|pipe)[\s-]+)+(?:mount(?:ing)?|connection)\b", re.I)

_NUM = r"[-+]?\d+(?:[.,]\d+)?"
_UNIT = r"(barg|bar\s?g|bar|psig|psi|mpa|kpa|mbar|kgf/cm²|kgf/cm2)"
_PAIR = re.compile(rf"({_NUM})\s*(?:\.{{2,3}}|…|\bto\b|–|—|-)\s*({_NUM})\s*{_UNIT}(?![a-z])", re.I)
_ONE = re.compile(rf"({_NUM})\s*{_UNIT}(?![a-z])", re.I)


def _norm(k):
    return re.sub(r"[^a-z0-9]+", " ", k.lower()).strip()


def _num(n):
    f = float(n.replace(",", ".").lstrip("+"))
    return str(int(f)) if f == int(f) else str(f)


def parse_range(text):
    """'700 barg' -> '0 to 700 barg'; '-700 barg' -> '-700 to 0 barg'; '0...16 bar' -> '0 to 16 bar'."""
    m = _PAIR.search(text or "")
    if m:
        return f"{_num(m.group(1))} to {_num(m.group(2))} {m.group(3).lower().replace(' ', '')}"
    m = _ONE.search(text or "")
    if m:
        n, unit = _num(m.group(1)), m.group(2).lower().replace(" ", "")
        return f"{n} to 0 {unit}" if float(n) < 0 else f"0 to {n} {unit}"
    return None


def build_key_details(title, details):
    """Put the eight fields the receptionist checks first, then everything else."""
    norm = {k: _norm(k) for k in details}
    used = set()

    def pick(label):
        for pat in FIELD_RULES[label]:
            for k, n in norm.items():
                if k not in used and re.search(pat, n):
                    return k
        return None

    def scan_values(rx, maxlen=120):
        for k, v in details.items():
            m = rx.search(v) if len(v) <= maxlen else None
            if m:
                return k, m.group(0)
        return None, None

    out = {}
    # Type
    k = pick("Type")
    if k:
        out["Type"] = details[k]; used.add(k)
    else:
        m = TYPE_RE.search(title or "")
        out["Type"] = m.group(0) if m else (scan_values(TYPE_RE)[1] or "")
    # Instrument name
    k = pick("Instrument name")
    if k:
        out["Instrument name"] = details[k]; used.add(k)
    else:
        out["Instrument name"] = title or ""
    # Serial and item number
    for label in ("Serial number", "Item number"):
        k = pick(label)
        out[label] = details[k] if k else ""
        if k:
            used.add(k)
    # Model number
    k = pick("Model number")
    if k:
        m = MODEL_RE.search(details[k])
        out["Model number"] = m.group(0) if m else details[k]; used.add(k)
    else:
        m = MODEL_RE.search(title or "")
        out["Model number"] = m.group(0) if m else (scan_values(MODEL_RE)[1] or "")
    # Range
    rng, k = None, pick("Range")
    if k:
        rng = parse_range(details[k]) or details[k]; used.add(k)
    else:
        for kk, v in details.items():
            r = parse_range(v) if len(v) <= 80 and kk not in used else None
            if r:
                rng = r; used.add(kk); break
    out["Range"] = rng or ""
    # Size
    k = pick("Size")
    if k:
        m = SIZE_RE.search(details[k])
        out["Size"] = m.group(0) if m else details[k]; used.add(k)
    else:
        out["Size"] = scan_values(SIZE_RE, 60)[1] or ""
    # Mounting
    k = pick("Mounting")
    if k:
        out["Mounting"] = details[k]; used.add(k)
    else:
        out["Mounting"] = scan_values(MOUNT_RE, 80)[1] or ""

    for k, v in details.items():
        if k in used:
            continue
        out[f"{k} (page)" if k in KEY_LABELS else k] = v
    return out


def extract(url: str) -> dict:
    url = check_url(url)
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ExtractError(f"The website did not respond ({e.__class__.__name__}).")
    check_url(r.url)  # re-check after redirects
    title, details = _parse(r.text)
    rendered = False
    if len(details) < MIN_FIELDS_BEFORE_RENDER:
        html = _render_with_playwright(r.url)
        if html:
            title2, details2 = _parse(html)
            if len(details2) > len(details):
                title, details, rendered = title2 or title, details2, True
    if not details:
        raise ExtractError(
            "No product details found on this page. If the page loads its content with JavaScript, "
            "install Playwright (see README) so the page can be rendered."
        )
    details = build_key_details(title, details)
    found = sum(1 for k in KEY_LABELS if details.get(k))
    return {"url": r.url, "title": title, "details": details, "key_labels": KEY_LABELS,
            "count": sum(1 for v in details.values() if v), "found": found, "rendered": rendered}
