#!/usr/bin/env python3
"""Track Orient Kamasu prices across shops that deliver to Romania.

Run it on a schedule. It prints a report and exits 1 if anything needs
your attention (price drop, restock, or a broken scraper).

  python3 kamasu_tracker.py
  python3 kamasu_tracker.py --quiet   # only print when something changed
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HISTORY = Path(__file__).with_name("price_history.json")

# Alert when a price drops below this (RON). Set per your budget.
TARGET_RON = 1300

# Rough EUR->RON. Only used for shops that price in euro, to compare
# against TARGET_RON. Not accurate enough to spend money on blindly.
EUR_RON = 5.08

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

# Add or remove freely. `currency` is the shop's own currency.
# Everything here delivers to Romania.
PRODUCTS = [
    # --- Romanian shops ---
    {"shop": "timestore.ro", "variant": "red RA-AA0003R39B", "currency": "RON",
     "url": "https://www.timestore.ro/ceasuri-barbati-orient-contemporary-ra-aa0003r39b"},
    {"shop": "timestore.ro", "variant": "blue RA-AA0002L39B", "currency": "RON",
     "url": "https://www.timestore.ro/ceasuri-barbati-orient-kamasu-ra-aa0002l39b"},
    {"shop": "timestore.ro", "variant": "RA-AA0821S39B", "currency": "RON",
     "url": "https://www.timestore.ro/ceasuri-barbati-orient-kamasu-ra-aa0821s39b"},
    {"shop": "istimo.ro", "variant": "blue RA-AA0002L39B", "currency": "RON",
     "url": "https://www.istimo.ro/p/orient-kamasu-ra-aa0002l39b"},
    {"shop": "eceasuri.ro", "variant": "red RA-AA0003R19B", "currency": "RON",
     "url": "https://eceasuri.ro/ceas-barbatesc-orient-kamasu-automatic-ra-aa0003r19b"},
    # --- EU shops that ship to RO ---
    {"shop": "helveti.ro", "variant": "red RA-AA0003R", "currency": "EUR",
     "url": "https://www.helveti.ro/orient-kamasu-ra-aa0003r"},
    {"shop": "helveti.ro", "variant": "blue RA-AA0002L", "currency": "EUR",
     "url": "https://www.helveti.ro/orient-kamasu-ra-aa0002l"},
    {"shop": "kulta-center.com", "variant": "blue RA-AA0002L19B", "currency": "EUR",
     "url": "https://www.kulta-center.com/en/orient-kamasu-watch-ra-aa0002l19b"},
]

# Text that means "you can't buy it right now", lowercased.
OOS_MARKERS = [
    "nu se află în stoc", "nu se afla in stoc", "produsul nu mai este disponibil",
    "stoc epuizat", "indisponibil", "out of stock", "sold out",
]


def _walk_jsonld(node):
    """Yield every dict inside a JSON-LD blob, however nested."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_jsonld(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)


def from_jsonld(soup):
    """Most shops emit schema.org Product/Offer. This is the reliable path."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_jsonld(data):
            price = node.get("price") or node.get("lowPrice")
            if price is not None:
                try:
                    return normalize_amount(str(price))
                except ValueError:
                    continue
    return None


def from_meta(soup):
    """Open Graph / product meta tags. Helveti exposes price this way."""
    keys = ["product:price:amount", "og:price:amount", "twitter:data1"]
    for key in keys:
        tag = soup.find("meta", property=key) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            cleaned = re.sub(r"[^\d.,]", "", tag["content"])
            try:
                return normalize_amount(cleaned)
            except ValueError:
                continue
    return None


# Phrases that mark a number as the inflated list price, not what you pay.
RRP_MARKERS = (
    "recomandat", "prp", "msrp", "pret intreg", "preț întreg",
    "originally", "economis", "was ", "list price",
)

AMOUNT = r"\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?"


def normalize_amount(raw):
    """Parse '1.311', '258.00', '1,311.00' and '436,00' correctly.

    Romanian shops write 1.311 lei (dot = thousands); English-language ones
    write 258.00 (dot = decimal). Guess from the digit count after the last
    separator: exactly 3 digits means thousands, otherwise decimal.
    """
    raw = raw.replace(" ", "").replace("\xa0", "")
    seps = [c for c in raw if c in ".,"]
    if not seps:
        return float(raw)
    last = raw.rfind(seps[-1])
    tail = raw[last + 1:]
    if len(tail) == 3:  # 1.311 / 1,311 -> thousands, no decimal part
        return float(raw.replace(".", "").replace(",", ""))
    whole = raw[:last].replace(".", "").replace(",", "")
    return float(f"{whole}.{tail}")


def from_text(soup, currency):
    """Last resort: scan visible text. The weakest method -- treat with suspicion.

    Two traps this avoids: pages lead with shipping costs and loyalty points
    (small numbers), and they show an inflated RRP next to the real price
    (large number). So we drop anything sitting near an RRP phrase, then take
    the largest of what survives.
    """
    text = soup.get_text(" ", strip=True)
    unit = r"lei|RON" if currency == "RON" else r"€|EUR"
    # Currency can sit on either side: "1.311 lei" and "€258" are both valid.
    pattern = rf"(?:(?:{unit})\s*({AMOUNT})|({AMOUNT})\s*(?:{unit}))"

    found = []
    for match in re.finditer(pattern, text, re.IGNORECASE):
        raw = match.group(1) or match.group(2)
        context = text[max(0, match.start() - 45):match.start()].lower()
        if any(marker in context for marker in RRP_MARKERS):
            continue
        try:
            value = normalize_amount(raw)
        except ValueError:
            continue
        if 100 <= value <= 20000:  # a Kamasu is never outside this
            found.append(value)
    return max(found) if found else None


def scrape(product):
    """Return (price, in_stock, method, error)."""
    try:
        resp = requests.get(product["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return None, None, None, f"fetch failed: {type(exc).__name__}"

    soup = BeautifulSoup(resp.text, "html.parser")
    lowered = soup.get_text(" ", strip=True).lower()
    in_stock = not any(marker in lowered for marker in OOS_MARKERS)

    for method, extractor in (
        ("json-ld", lambda: from_jsonld(soup)),
        ("meta", lambda: from_meta(soup)),
        ("text", lambda: from_text(soup, product["currency"])),
    ):
        price = extractor()
        if price is not None:
            return price, in_stock, method, None

    return None, in_stock, None, "no price found -- selectors likely broken"


def to_ron(price, currency):
    return price if currency == "RON" else round(price * EUR_RON)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true",
                        help="only print if something changed or broke")
    args = parser.parse_args()

    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    history.pop("_meta", None)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    alerts, rows = [], []

    for product in PRODUCTS:
        key = f"{product['shop']}|{product['variant']}"
        price, in_stock, method, error = scrape(product)
        entries = history.setdefault(key, [])
        previous = entries[-1] if entries else None

        if error:
            alerts.append(f"BROKEN  {key}: {error}")
            rows.append((key, "--", "?", error))
            entries.append({"at": now, "error": error, "url": product["url"],
                            "shop": product["shop"], "variant": product["variant"]})
        else:
            ron = to_ron(price, product["currency"])
            stock_label = "in stock" if in_stock else "OUT"
            rows.append((key, f"{ron} RON", stock_label, f"via {method}"))

            if ron <= TARGET_RON and in_stock:
                alerts.append(f"TARGET  {key}: {ron} RON -> {product['url']}")
            if previous and previous.get("price_ron"):
                if ron < previous["price_ron"]:
                    drop = previous["price_ron"] - ron
                    alerts.append(f"DROP    {key}: -{drop} RON (now {ron})")
                if in_stock and not previous.get("in_stock", True):
                    alerts.append(f"RESTOCK {key}: {ron} RON -> {product['url']}")

            entries.append({"at": now, "price_ron": ron, "in_stock": in_stock,
                            "raw": price, "currency": product["currency"],
                            "method": method, "url": product["url"],
                            "shop": product["shop"], "variant": product["variant"]})

        time.sleep(3)  # be a polite guest on someone else's server

    history["_meta"] = {"target_ron": TARGET_RON, "checked_at": now}
    HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False))

    if alerts or not args.quiet:
        print(f"Orient Kamasu -- {now}\n")
        width = max(len(r[0]) for r in rows)
        for name, price, stock, note in rows:
            print(f"  {name:<{width}}  {price:>10}  {stock:<9}  {note}")
        if alerts:
            print("\n" + "\n".join("  " + a for a in alerts))

    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
