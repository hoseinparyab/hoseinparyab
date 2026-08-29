#!/usr/bin/env python3
"""Fetch GitHub commit stats and update README (public; private when GH_PAT is set)."""

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
TOKEN = os.environ.get("GH_PAT", "")
README_PATH = os.environ.get("README_PATH", "README.md")
START_MARKER = "<!--START_SECTION:commit_stats-->"
END_MARKER = "<!--END_SECTION:commit_stats-->"
MONTHS_LOOKBACK = int(os.environ.get("MONTHS_LOOKBACK", "6"))


def http_get(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "hoseinparyab-commit-stats"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8")


def graphql_request(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "hoseinparyab-commit-stats",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))
    if "errors" in result:
        raise RuntimeError(json.dumps(result["errors"], indent=2))
    return result["data"]


def fetch_user_created_at() -> datetime:
    payload = json.loads(http_get(f"https://api.github.com/users/{USERNAME}"))
    return datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))


def fetch_public_total_commits() -> int:
    svg = http_get(
        f"https://github-readme-stats.shion.dev/api?username={USERNAME}"
        "&include_all_commits=true&hide=stars,prs,issues,contribs"
    )
    match = re.search(r"Total Commits\s*:\s*([\d,]+)", svg)
    if not match:
        raise RuntimeError("Could not parse total commits from stats API")
    return int(match.group(1).replace(",", ""))


def fetch_public_contribution_days(start_year: int, end_year: int) -> list[tuple[datetime, int]]:
    days: list[tuple[datetime, int]] = []
    for year in range(start_year, end_year + 1):
        payload = json.loads(
            http_get(f"https://github-contributions-api.jogruber.de/v4/{USERNAME}?y={year}")
        )
        for item in payload.get("contributions", []):
            date = datetime.strptime(item["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days.append((date, int(item["count"])))
    return days


def fetch_private_stats(from_date: datetime, to_date: datetime) -> dict[str, Any]:
    data = graphql_request(
        """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          viewer { login }
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
        """,
        {
            "login": USERNAME,
            "from": from_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    viewer_login = data.get("viewer", {}).get("login")
    if viewer_login != USERNAME:
        raise RuntimeError(
            f"GH_PAT must belong to '{USERNAME}', but token user is '{viewer_login}'."
        )

    user = data.get("user")
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
    scope_label: str,
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
      <sub>{since_label} - Present · {scope_label}</sub>
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
    created_at = fetch_user_created_at()
    now = datetime.now(timezone.utc)

    if TOKEN:
        print("Using GH_PAT: counting public + private stats")
        stats = fetch_private_stats(created_at, now)
        calendar = stats["contributionCalendar"]
        days = parse_calendar_days(calendar)
        total_commits = int(stats.get("totalCommitContributions", 0))
        scope_label = "Public + Private"
    else:
        print("GH_PAT not set: counting public stats only")
        lookback_start_year = max(created_at.year, now.year - 1)
        days = fetch_public_contribution_days(lookback_start_year, now.year)
        total_commits = fetch_public_total_commits()
        scope_label = "Public only"

    best_month, best_week = compute_peaks(days, MONTHS_LOOKBACK)
    update_readme(build_section(total_commits, best_month, best_week, created_at, scope_label))

    print(
        json.dumps(
            {
                "scope": scope_label,
                "total_commits": total_commits,
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
