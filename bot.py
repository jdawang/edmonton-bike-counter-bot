import os
import re
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

    query = (
        "SELECT `counter_location_description`, sum(`total_cyclist_count`) AS `total_cyclist_count`"
        f" WHERE `log_timestamp` >= '{start}' AND `log_timestamp` < '{end}'"
        " GROUP BY `counter_location_description`"
        " LIMIT 200"
    )
    resp = requests.get(
        "https://data.edmonton.ca/api/v3/views/tq23-qn4m/query.json",
        params={"query": query},
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


def fetch_daily_totals(start_date, end_date):
    start = start_date.strftime("%Y-%m-%dT00:00:00.000")
    end = (end_date + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000")
    excluded = ", ".join(f"'{loc}'" for loc in EXCLUDED_LOCATIONS)
    query = (
        "SELECT date_trunc_ymd(`log_timestamp`) AS day,"
        " sum(`total_cyclist_count`) AS total"
        f" WHERE `log_timestamp` >= '{start}' AND `log_timestamp` < '{end}'"
        f" AND `counter_location_description` NOT IN ({excluded})"
        " GROUP BY day ORDER BY day LIMIT 400"
    )
    key_id = os.environ.get("SOCRATA_KEY_ID")
    key_secret = os.environ.get("SOCRATA_KEY_SECRET")
    auth = (key_id, key_secret) if key_id and key_secret else None
    resp = requests.get(
        "https://data.edmonton.ca/api/v3/views/tq23-qn4m/query.json",
        params={"query": query},
        auth=auth,
        timeout=60,
    )
    resp.raise_for_status()
    result = []
    for row in resp.json():
        day = date.fromisoformat(row["day"][:10])
        total = int(float(row.get("total", 0)) or 0)
        result.append((day, total))
    return result


def generate_trend_chart(this_year_data, last_year_data, target_date):
    import io
    import polars as pl
    from lets_plot import (
        ggplot,
        aes,
        geom_point,
        geom_smooth,
        scale_x_datetime,
        labs,
        theme_minimal,
    )

    def to_df(data, year_label):
        return pl.DataFrame(
            {
                "day": pl.Series(
                    [date(2000, d.month, d.day) for d, _ in data], dtype=pl.Date
                ),
                "total": [t for _, t in data],
                "year": year_label,
            }
        )

    df = pl.concat(
        [
            to_df(last_year_data, str(target_date.year - 1)),
            to_df(this_year_data, str(target_date.year)),
        ]
    )

    plot = (
        ggplot(df, aes("day", "total", color="year"))
        + geom_point(alpha=0.4, size=1.5)
        + geom_smooth(method="loess", size=1.2, se=False)
        + scale_x_datetime(name="", break_width="1 month", format="%b")
        + labs(
            y="Cyclists", color="Year", title="Edmonton daily cyclists — year over year"
        )
        + theme_minimal()
    )

    buf = io.BytesIO()
    plot.to_png(buf, w=8, h=4, unit="in", dpi=150)
    return buf.getvalue()


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


def build_facets(text):
    facets = []
    for match in re.finditer(r"#(\w+)", text):
        byte_start = len(text[: match.start()].encode("utf-8"))
        byte_end = len(text[: match.end()].encode("utf-8"))
        facets.append(
            models.AppBskyRichtextFacet.Main(
                features=[models.AppBskyRichtextFacet.Tag(tag=match.group(1))],
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=byte_start,
                    byte_end=byte_end,
                ),
            )
        )
    return facets or None


def post_thread(posts, handle, password, image_bytes=None):
    client = Client()
    client.login(handle, password)

    root_ref = None
    parent_ref = None

    for i, text in enumerate(posts):
        facets = build_facets(text)
        embed = None
        if i == 0 and image_bytes is not None:
            upload = client.upload_blob(image_bytes)
            embed = models.AppBskyEmbedImages.Main(
                images=[
                    models.AppBskyEmbedImages.Image(
                        image=upload.blob,
                        alt="Scatterplot of daily Edmonton cyclist counts over the past 12 months with LOESS trendline",
                    )
                ]
            )
        if i == 0:
            response = client.send_post(text=text, facets=facets, embed=embed)
            root_ref = models.create_strong_ref(response)
            parent_ref = root_ref
        else:
            reply_to = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref)
            response = client.send_post(text=text, reply_to=reply_to, facets=facets)
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

    click.echo("Fetching year-over-year historical totals for chart...")
    this_year_data = fetch_daily_totals(
        target_date.replace(month=1, day=1), target_date
    )
    last_year_data = fetch_daily_totals(
        date(target_date.year - 1, 1, 1), date(target_date.year - 1, 12, 31)
    )
    chart_bytes = (
        generate_trend_chart(this_year_data, last_year_data, target_date)
        if (this_year_data or last_year_data)
        else None
    )

    posts = format_posts(target_date, total, counts, weather)

    if dry_run:
        output = "\n\n---\n\n".join(posts)
        with open("output.txt", "w") as f:
            f.write(output)
        click.echo(f"Dry run: wrote {len(posts)} post(s) to output.txt")
        if chart_bytes:
            with open("output_chart.png", "wb") as f:
                f.write(chart_bytes)
            click.echo("Dry run: wrote chart to output_chart.png")
        return

    handle = os.environ["BSKY_HANDLE"]
    password = os.environ["BSKY_APP_PASSWORD"]
    post_thread(posts, handle, password, image_bytes=chart_bytes)
    click.echo(f"Posted {len(posts)} post(s) to BlueSky as @{handle}")


if __name__ == "__main__":
    main()
