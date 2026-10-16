# Samsun sea temperature scraper

`scrape_sea_temp.py` fetches the Meteoroloji Genel Müdürlüğü (MGM) Piri Reis
sea-temperature page and prints the latest reading for stations in Samsun,
including the station name and measurement timestamp.

## Run

Requires Python 3. The script uses only the Python standard library, so no
package installation is needed.

```sh
python3 scrape_sea_temp.py
```

Example output:

```text
İLKADIM/SAMSUN LİMAN KUZEY MENDİREK FENERİ: 20.5 °C (measured 2026-10-16T07:54:00.000Z)
```

The value changes as MGM updates its station data. The source is the
[MGM Piri Reis sea-temperature page](https://pirireis.mgm.gov.tr/deniz-suyu-sicakliklari).
