import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import click
import requests
from atproto import Client, models
from dotenv import load_dotenv

EDMONTON_TZ = ZoneInfo("America/Edmonton")

EXCLUDED_LOCATIONS = {
    "Capilano Bridge",
    "Emily Murphy",
    "Funicular @ 100 St",
    "Hermitage South",
    "Queen Elizabeth Park 2",
    "Sir Wilfrid Laurier Park",
    "Whitemud Ravine Boardwalk",
}

BSKY_POST_LIMIT = 300


def get_yesterday():
    return (datetime.now(EDMONTON_TZ) - timedelta(days=1)).date()


def fetch_bike_counts(target_date):
    start = target_date.strftime("%Y-%m-%dT00:00:00.000")
    end = (target_date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000")
    key_id = os.environ.get("SOCRATA_KEY_ID")
    key_secret = os.environ.get("SOCRATA_KEY_SECRET")
    auth = (key_id, key_secret) if key_id and key_secret else None

    resp = requests.get(
        "https://data.edmonton.ca/resource/tq23-qn4m.json",
        params={
            "$select": "counter_location_description, sum(total_cyclist_count) as total_cyclist_count",
            "$where": f"log_timestamp >= '{start}' AND log_timestamp < '{end}'",
            "$group": "counter_location_description",
            "$limit": 200,
        },
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()

    counts = []
    for row in resp.json():
        location = row["counter_location_description"]
        if location in EXCLUDED_LOCATIONS:
            continue
        count = int(float(row.get("total_cyclist_count", 0)) or 0)
        counts.append((location, count))

    counts.sort(key=lambda x: x[1], reverse=True)
    return counts


def fetch_weather(target_date):
    date_str = target_date.strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": 53.5461,
                "longitude": -113.4938,
                "start_date": date_str,
                "end_date": date_str,
                "daily": "temperature_2m_max,temperature_2m_min,rain_sum,snowfall_sum",
                "timezone": "America/Edmonton",
            },
            timeout=30,
        )
        resp.raise_for_status()
        daily = resp.json()["daily"]
        return {
            "temp_high": daily["temperature_2m_max"][0],
            "temp_low": daily["temperature_2m_min"][0],
            "rain": daily["rain_sum"][0],
            "snow": daily["snowfall_sum"][0],
        }
    except Exception:
        return None


def format_posts(target_date, total, counts, weather):
    day_str = target_date.strftime("%A, %-d %B")
    if weather is not None:
        precip_parts = []
        if weather["rain"] > 0:
            precip_parts.append(f"{weather['rain']:.1f}mm rain")
        if weather["snow"] > 0:
            precip_parts.append(f"{weather['snow']:.1f}cm snow")
        weather_str = f"↑{weather['temp_high']:.1f}°C ↓{weather['temp_low']:.1f}°C"
        if precip_parts:
            weather_str += ", " + ", ".join(precip_parts)
        header = f"{day_str}, {weather_str}\n\nTotal: {total:,}\n\n#YEGBike\n"
    else:
        header = f"{day_str}\n\nTotal: {total:,}\n\n#YEGBike\n"

    posts = []
    current = header

    for location, count in counts:
        line = f"\n{location}: {count:,}"
        if len(current) + len(line) <= BSKY_POST_LIMIT:
            current += line
        else:
            posts.append(current)
            current = f"{location}: {count:,}"

    posts.append(current)
    return posts


def post_thread(posts, handle, password):

    client = Client()
    client.login(handle, password)

    root_ref = None
    parent_ref = None

    for i, text in enumerate(posts):
        if i == 0:
            response = client.send_post(text=text)
            root_ref = models.create_strong_ref(response)
            parent_ref = root_ref
        else:
            reply_to = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref)
            response = client.send_post(text=text, reply_to=reply_to)
            parent_ref = models.create_strong_ref(response)


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Write posts to output.txt instead of posting to BlueSky",
)
@click.option(
    "--date",
    "target_date",
    default=None,
    help="Date to fetch (YYYY-MM-DD), defaults to yesterday",
)
def main(dry_run, target_date):
    load_dotenv()

    if target_date:
        target_date = date.fromisoformat(target_date)
    else:
        target_date = get_yesterday()

    click.echo(f"Fetching bike counts for {target_date}...")
    counts = fetch_bike_counts(target_date)

    if not counts:
        click.echo(f"No bike count data found for {target_date}")
        sys.exit(0)

    total = sum(c for _, c in counts)
    click.echo(f"Found {len(counts)} locations, total {total:,} cyclists")

    weather = fetch_weather(target_date)
    if weather is not None:
        click.echo(
            f"Weather: ↑{weather['temp_high']:.1f}°C ↓{weather['temp_low']:.1f}°C"
            f", {weather['rain']:.1f}mm rain, {weather['snow']:.1f}cm snow"
        )

    posts = format_posts(target_date, total, counts, weather)

    if dry_run:
        output = "\n\n---\n\n".join(posts)
        with open("output.txt", "w") as f:
            f.write(output)
        click.echo(f"Dry run: wrote {len(posts)} post(s) to output.txt")
        return

    handle = os.environ["BSKY_HANDLE"]
    password = os.environ["BSKY_APP_PASSWORD"]
    post_thread(posts, handle, password)
    click.echo(f"Posted {len(posts)} post(s) to BlueSky as @{handle}")


if __name__ == "__main__":
    main()
