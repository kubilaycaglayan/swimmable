# Weather data: current temperature for Atakum

Use the Open-Meteo Forecast API for the current weather reading in Atakum,
Samsun, Türkiye. The request below returns the current temperature and useful
wind context in Celsius and kilometres per hour, with timestamps in local
Türkiye time.

## Location

- Name: Atakum, Samsun, Türkiye
- Latitude: `41.3378`
- Longitude: `36.2691`
- Time zone: `Europe/Istanbul`

These coordinates are the Atakum location used for this integration. Open-Meteo
forecasts are coordinate-based, so use the same coordinates consistently when
comparing readings over time.

## API request

Ready-to-use URL:

```text
https://api.open-meteo.com/v1/forecast?latitude=41.3378&longitude=36.2691&current=temperature_2m,wind_speed_10m,wind_direction_10m&temperature_unit=celsius&wind_speed_unit=kmh&timezone=Europe%2FIstanbul
```

No API key is needed for the free endpoint. Example with `curl` and `jq`:

```sh
curl -s 'https://api.open-meteo.com/v1/forecast?latitude=41.3378&longitude=36.2691&current=temperature_2m,wind_speed_10m,wind_direction_10m&temperature_unit=celsius&wind_speed_unit=kmh&timezone=Europe%2FIstanbul' \
  | jq '.current | {time, temperature_2m, wind_speed_10m, wind_direction_10m}'
```

## Reading the response

Read values from the top-level `current` object:

- `temperature_2m`: current air temperature at 2 metres, in °C.
- `wind_speed_10m`: current wind speed at 10 metres, in km/h.
- `wind_direction_10m`: wind direction in degrees, clockwise from north.
- `time`: timestamp for the current reading, in `Europe/Istanbul` local time.

The endpoint returns a model-based current estimate, not a thermometer reading
from a specified Atakum weather station. Show the returned timestamp alongside
the temperature so users can see how fresh the value is. The API refreshes on
its model update schedule; requesting it three times per day does not guarantee
three distinct underlying weather updates.

## Usage and attribution

The free Open-Meteo API is for non-commercial use, subject to its rate limits
and CC BY 4.0 attribution requirements. For commercial use, use an Open-Meteo
subscription and its customer endpoint. See the [Forecast API documentation](https://open-meteo.com/en/docs),
[terms](https://open-meteo.com/en/terms), and [pricing](https://open-meteo.com/en/pricing).

Suggested attribution: “Weather data by [Open-Meteo](https://open-meteo.com/).”
