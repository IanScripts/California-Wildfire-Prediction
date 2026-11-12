
import os
import requests

API_URL = (
    "https://et.water.ca.gov/"
    "StationWeb/GetDataByStationNumber"
)

api_key = os.getenv("CIMIS_API_KEY")

if not api_key:
    raise SystemExit("CIMIS_API_KEY is not set.")

params = {
    "appKey": api_key,
    "stationNbrs": "2",
    "startDate": "2010-01-01",
    "endDate": "2010-01-02",
    "isHourly": "true",
    "unitOfMeasure": "E",
}

try:
    response = requests.get(
        API_URL,
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )

    print("Status:", response.status_code)
    print(
        "Content type:",
        response.headers.get("Content-Type")
    )

    if "json" in response.headers.get(
        "Content-Type", ""
    ).lower():
        data = response.json()
        print("JSON response received!")
        print("Top-level keys:", list(data.keys()))
        print(str(data)[:800])
    else:
        print("Non-JSON response received.")
        print(
            response.text[:300].replace(
                api_key, "[REDACTED]"
            )
        )

except requests.RequestException as error:
    print("Request failed:", type(error).__name__)
