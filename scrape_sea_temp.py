#!/usr/bin/env python3
"""Print the latest sea temperature reported for Samsun by MGM."""

import json
import re
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


URL = "https://pirireis.mgm.gov.tr/deniz-suyu-sicakliklari"


def fetch_samsun_reading():
    request = Request(URL, headers={"User-Agent": "Mozilla/5.0 (compatible; sea-temp-scraper/1.0)"})
    with urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8", errors="replace")

    # MGM embeds station readings as JSON objects in the page's application data.
    for match in re.finditer(r'\{[^{}]*"denizSicaklik"\s*:\s*[^,}]+,[^{}]*\}', page):
        try:
            station = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if station.get("il", "").casefold() == "samsun":
            return station
    raise RuntimeError("No Samsun sea-temperature reading was found in MGM's page data.")


def main():
    try:
        reading = fetch_samsun_reading()
    except (URLError, TimeoutError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(
        f"{reading.get('istAd', 'Samsun')}: "
        f"{reading['denizSicaklik']} °C "
        f"(measured {reading.get('denizVeriZamani', 'time unavailable')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
