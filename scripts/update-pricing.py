#!/usr/bin/env python3
"""Scrape Anthropic's pricing page and emit an updated pricing.json to stdout.

Keeps the regex keys already in pricing.json as the source of truth for which
model names group together. Per-key rates are pulled from the live page; the
script errors out if a key matches multiple models with diverging rates (which
means the regex needs to be split into separate keys).

Usage:
    scripts/update-pricing.py > pricing.json.new && mv pricing.json.new pricing.json
"""
import datetime
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_FILE = Path(__file__).resolve().parent.parent / "pricing.json"


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "cccc-update-pricing"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")


def parse_price(text):
    m = re.search(r"\$([\d.]+)\s*/\s*MTok", text)
    if not m:
        raise ValueError(f"no price in cell: {text!r}")
    return float(m.group(1))


def find_pricing_table(tables):
    for t in tables:
        if not t:
            continue
        header = " | ".join(t[0])
        if "Base Input" in header and "Output Tokens" in header and "Cache Hits" in header:
            return t
    raise SystemExit("ERROR: could not find model pricing table; page format may have changed")


def extract_validity(name, today):
    """Return True if a date-qualified model name is valid today."""
    m = re.search(r"through\s+(\w+\s+\d{1,2},?\s*\d{4})", name)
    if m:
        end = datetime.datetime.strptime(m.group(1).replace(",", ""), "%B %d %Y").date()
        return today <= end
    m = re.search(r"starting\s+(\w+\s+\d{1,2},?\s*\d{4})", name)
    if m:
        start = datetime.datetime.strptime(m.group(1).replace(",", ""), "%B %d %Y").date()
        return today >= start
    return True


def model_name_to_api_id(name):
    name = re.sub(r"\(.*?\)", "", name).strip()
    name = re.sub(r"(?:through|starting|until|from)\s+\w+\s+\d{1,2},?\s*\d{4}.*", "", name).strip()
    return re.sub(r"\s+", "-", name.lower().replace(".", "-"))


def main():
    existing = json.loads(PRICING_FILE.read_text())
    html = fetch(PRICING_URL)
    parser = TableParser()
    parser.feed(html)
    table = find_pricing_table(parser.tables)

    today = datetime.date.today()
    page_models_raw = []
    for row in table[1:]:
        if len(row) < 6:
            continue
        try:
            rates = {
                "input": parse_price(row[1]),
                "output": parse_price(row[5]),
                "cache_write": parse_price(row[2]),
                "cache_read": parse_price(row[4]),
            }
        except ValueError:
            continue
        page_models_raw.append((row[0], model_name_to_api_id(row[0]), rates))

    seen_ids = {}
    page_models = []
    for name, api_id, rates in page_models_raw:
        if api_id in seen_ids:
            prev_name, prev_rates = seen_ids[api_id]
            if rates == prev_rates:
                continue
            if extract_validity(name, today):
                seen_ids[api_id] = (name, rates)
                page_models = [(n, a, r) for n, a, r in page_models if a != api_id]
                page_models.append((name, api_id, rates))
            continue
        seen_ids[api_id] = (name, rates)
        page_models.append((name, api_id, rates))

    key_matches = {key: [] for key in existing["models"]}
    for name, api_id, rates in page_models:
        for key in existing["models"]:
            if re.search(key, api_id):
                key_matches[key].append((name, rates))
                break

    updated_models = {}
    for key in existing["models"]:
        matches = key_matches[key]
        if not matches:
            print(f"WARN: no page model matched key {key!r}; keeping existing rates", file=sys.stderr)
            updated_models[key] = existing["models"][key]
            continue
        first = matches[0][1]
        for name, r in matches[1:]:
            if r != first:
                names = ", ".join(n for n, _ in matches)
                raise SystemExit(
                    f"ERROR: key {key!r} matches multiple page models with different rates "
                    f"({names}); split this regex into separate keys"
                )
        updated_models[key] = first

    ws_match = re.search(r"\$(\d+(?:\.\d+)?)\s*per\s*1[,]?000\s*searches", html)
    web_search_cost = (
        float(ws_match.group(1)) / 1000 if ws_match
        else existing.get("web_search_cost_per_request", 0.01)
    )

    fmt = lambda v: f"{v:.2f}"
    lines = ["{"]
    lines.append(f'  "_source": {json.dumps(PRICING_URL)},')
    lines.append(f'  "_updated": {json.dumps(datetime.date.today().isoformat())},')
    lines.append(f'  "_rates": {json.dumps(existing["_rates"])},')
    lines.append(f'  "_keys": {json.dumps(existing["_keys"])},')
    lines.append('  "models": {')
    keys = list(updated_models)
    key_field_width = max(len(k) + 3 for k in keys)  # quotes + colon
    for i, k in enumerate(keys):
        rates_str = ", ".join(f'"{rk}": {fmt(rv)}' for rk, rv in updated_models[k].items())
        comma = "" if i == len(keys) - 1 else ","
        key_field = f'"{k}":'.ljust(key_field_width)
        lines.append(f'    {key_field} {{ {rates_str} }}{comma}')
    lines.append("  },")
    lines.append(f'  "web_search_cost_per_request": {fmt(web_search_cost)}')
    lines.append("}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
