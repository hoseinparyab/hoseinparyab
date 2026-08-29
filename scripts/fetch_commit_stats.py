#!/usr/bin/env python3
"""Fetch GitHub commit stats (public + private) and update README section."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

USERNAME = os.environ.get("GITHUB_USERNAME", "hoseinparyab")
TOKEN = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
README_PATH = os.environ.get("README_PATH", "README.md")
START_MARKER = "<!--START_SECTION:commit_stats-->"
END_MARKER = "<!--END_SECTION:commit_stats-->"
MONTHS_LOOKBACK = int(os.environ.get("MONTHS_LOOKBACK", "6"))


def http_get(url: str, auth: bool = False) -> str:
    headers = {
        "User-Agent": "hoseinparyab-commit-stats",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if auth and TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8")


def api_request(url: str, data: dict[str, Any] | None = None) -> Any:
    headers = {
        "User-Agent": "hoseinparyab-commit-stats",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if not TOKEN:
        raise RuntimeError("GH_PAT secret is required to include private repo stats")

    headers["Authorization"] = f"Bearer {TOKEN}"
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_user_created_at() -> datetime:
    payload = json.loads(http_get(f"https://api.github.com/users/{USERNAME}"))
    return datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))


def fetch_authenticated_stats(from_date: datetime, to_date: datetime) -> dict[str, Any]:
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      viewer {
        login
      }
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "login": USERNAME,
        "from": from_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": to_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    result = api_request("https://api.github.com/graphql", {"query": query, "variables": variables})
    if "errors" in result:
        raise RuntimeError(json.dumps(result["errors"], indent=2))

    viewer_login = result.get("data", {}).get("viewer", {}).get("login")
    if viewer_login != USERNAME:
        raise RuntimeError(
            "GH_PAT must belong to the profile owner. "
            f"Token user is '{viewer_login}', expected '{USERNAME}'."
        )

    user = result.get("data", {}).get("user")
    if not user:
        raise RuntimeError("GitHub user not found")

    return user["contributionsCollection"]


def parse_calendar_days(calendar: dict[str, Any]) -> list[tuple[datetime, int]]:
    days: list[tuple[datetime, int]] = []
    for week in calendar.get("weeks", []):
        for day in week.get("contributionDays", []):
            date = datetime.strptime(day["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days.append((date, int(day["contributionCount"])))
    return days


def month_key(date: datetime) -> str:
    return date.strftime("%Y-%m")


def format_month(label: str) -> str:
    year, month = label.split("-")
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{names[int(month) - 1]} {year}"


def format_week(start: datetime, end: datetime) -> str:
    if start.year == end.year:
        return f"{start.strftime('%b %d')} - {end.strftime('%b %d, %Y')}"
    return f"{start.strftime('%b %d, %Y')} - {end.strftime('%b %d, %Y')}"


def compute_peaks(days: list[tuple[datetime, int]], months_back: int) -> tuple[tuple[str, int, str], tuple[str, int, str]]:
    now = datetime.now(timezone.utc)
    month_cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(months_back - 1):
        month_cutoff = (month_cutoff.replace(day=1) - timedelta(days=1)).replace(day=1)

    monthly: dict[str, int] = defaultdict(int)
    weekly: dict[str, int] = defaultdict(int)
    week_start: dict[str, datetime] = {}
    week_end: dict[str, datetime] = {}

    for date, count in days:
        if date >= month_cutoff:
            monthly[month_key(date)] += count
            iso = date.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            weekly[key] += count
            week_start[key] = min(week_start.get(key, date), date)
            week_end[key] = max(week_end.get(key, date), date)

    if not monthly:
        raise RuntimeError("No contribution data found in lookback window")

    best_month_key = max(monthly, key=monthly.get)
    best_month = (format_month(best_month_key), monthly[best_month_key], f"Last {months_back} months")

    best_week_key = max(weekly, key=weekly.get)
    best_week = (
        format_week(week_start[best_week_key], week_end[best_week_key]),
        weekly[best_week_key],
        f"Last {months_back} months",
    )

    return best_month, best_week


def build_section(
    total_commits: int,
    best_month: tuple[str, int, str],
    best_week: tuple[str, int, str],
    since: datetime,
) -> str:
    since_label = since.strftime("%b %d, %Y")
    return f"""{START_MARKER}
<p align="center">
  <img src="https://github-readme-stats.shion.dev/api?username={USERNAME}&include_all_commits=true&show_icons=true&theme=tokyonight&hide_border=true&hide=stars,prs,issues,contribs&custom_title=Commit%20Overview" alt="Commit Overview" />
</p>

<p align="center">
  <img src="https://github-readme-streak-stats.herokuapp.com/?user={USERNAME}&theme=tokyonight&hide_border=true" alt="GitHub Streak" />
</p>

<table align="center">
  <tr>
    <td align="center">
      <b>🔢 Total Commits</b><br/>
      <b>{total_commits:,}</b><br/>
      <sub>{since_label} - Present · Public + Private</sub>
    </td>
    <td align="center">
      <b>📅 Best Month</b><br/>
      <b>{best_month[1]:,}</b><br/>
      <sub>{best_month[0]} · {best_month[2]}</sub>
    </td>
    <td align="center">
      <b>🔥 Best Week</b><br/>
      <b>{best_week[1]:,}</b><br/>
      <sub>{best_week[0]} · {best_week[2]}</sub>
    </td>
  </tr>
</table>

<p align="center"><sub>Auto-updated daily by GitHub Actions</sub></p>
{END_MARKER}"""


def update_readme(section: str) -> None:
    with open(README_PATH, encoding="utf-8") as handle:
        content = handle.read()

    pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL)
    if not pattern.search(content):
        raise RuntimeError(f"Markers not found in {README_PATH}")

    with open(README_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(pattern.sub(section, content, count=1))


def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "Missing GH_PAT secret. Create a Personal Access Token with 'repo' scope "
            "and add it as repository secret named GH_PAT."
        )

    created_at = fetch_user_created_at()
    now = datetime.now(timezone.utc)
    stats = fetch_authenticated_stats(created_at, now)

    calendar = stats["contributionCalendar"]
    days = parse_calendar_days(calendar)
    best_month, best_week = compute_peaks(days, MONTHS_LOOKBACK)
    total_commits = int(stats.get("totalCommitContributions", 0))

    update_readme(build_section(total_commits, best_month, best_week, created_at))

    print(
        json.dumps(
            {
                "total_commits": total_commits,
                "total_contributions": calendar.get("totalContributions", 0),
                "best_month": {"label": best_month[0], "count": best_month[1]},
                "best_week": {"label": best_week[0], "count": best_week[1]},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
