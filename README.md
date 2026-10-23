# wallops_launch_bot

A Python bot that checks NASA's [Wallops Flight Facility launch schedule](https://www.nasa.gov/wallops-launch-schedule/) and posts upcoming rocket launches to Bluesky as [@wallopslaunchbot.bsky.social](https://bsky.app/profile/wallopslaunchbot.bsky.social).

## How it works

Each run of `wallops_scraper_v2.py` does the following:

1. **Fetches the schedule page.** It downloads the NASA Wallops launch schedule and saves the raw HTML to `wallops_debug.html` so you can inspect it if parsing breaks.
2. **Finds launch entries.** It looks for page elements (`p`, `div`, `section`, `li`, `article`) that contain both `Rocket Type:` and `Launch Date:`. Elements with more than 2,000 characters of text are skipped, because they are page wrappers rather than individual entries.
3. **Pulls out the fields.** For each entry it reads these labels:
   - `Rocket Type:` → vehicle
   - `Launch Date:` → date, plus a launch time if one is listed (e.g. `5:30 a.m. EDT`)
   - `Launch Location:` → location
   - `Description:` → mission name (falls back to the rocket type)
4. **Parses dates.** Date ranges such as `June 24-25, 2026` are reduced to their first day and parsed with `python-dateutil`.
5. **Picks what to post.**
   - Launches that list a **specific time** are collected (duplicates removed by mission and date).
   - The **closest upcoming launch** is the one with the nearest date on or after today. If no dates can be parsed, the first entry on the page is used.
6. **Posts to Bluesky.**
   - If any launches have specific times, it posts one **Launch Alert** per launch (mission, vehicle, date, time). If the closest upcoming launch is a different mission, it is added to the post. Posts are 2 seconds apart.
   - Otherwise it posts a single dated **Launch Update** that shows the next upcoming launch.
   - If the page has no launches at all, it posts a short "no upcoming launches" message.

Posts are trimmed to Bluesky's 300-character limit, counting emoji as single characters. Long vehicle names are cut at the first dash, or to 50 characters.

### Example post

```
🚀 Wallops Launch Alert 🚀

Mission: <description from the schedule>
Vehicle: <rocket type>
Date: June 24, 2026
Time: 5:30 a.m.-9:30 a.m. EDT
```

## Requirements

- Python 3.9+
- Packages:
  - `requests`
  - `beautifulsoup4`
  - `python-dateutil`
  - `atproto`

```bash
pip install requests beautifulsoup4 python-dateutil atproto
```

## Configuration

The Bluesky login is read from two environment variables:

| Variable | Value |
| --- | --- |
| `BLUESKY_USERNAME` | The bot's handle, e.g. `wallopslaunchbot.bsky.social` |
| `BLUESKY_PASSWORD` | A Bluesky [app password](https://bsky.app/settings/app-passwords), not your main account password |

If either one is missing, the script still scrapes and logs the schedule but posts nothing, and it logs an error.

## Usage

```bash
export BLUESKY_USERNAME="wallopslaunchbot.bsky.social"
export BLUESKY_PASSWORD="your-app-password"
python3 wallops_scraper_v2.py
```

The script runs once and exits. To post on a schedule, run it with cron or a similar scheduler. cron doesn't load your shell profile, so keep the variables in a file that only you can read (for example `~/.wallops_bot_env` with `chmod 600`) and source it in the job:

```
# ~/.wallops_bot_env
export BLUESKY_USERNAME="wallopslaunchbot.bsky.social"
export BLUESKY_PASSWORD="your-app-password"
```

This crontab entry runs the bot every day at 8 a.m.:

```
0 8 * * * . $HOME/.wallops_bot_env && cd /path/to/wallops_launch_bot && /usr/bin/python3 wallops_scraper_v2.py
```

## Output files

| File | Purpose |
| --- | --- |
| `wallops_launch_bot.log` | Log of each run: what was found, what was parsed and the text of each post. It is written next to the script. |
| `wallops_debug.html` | The last schedule page that was downloaded. It is written to the current working directory. |

Errors are also printed to the console.

## Maintenance notes

The scraper depends on the labels NASA uses on the schedule page. The page has changed before: it used `Vehicle:`, `Mission:`, `Date:` and `Location:` and now uses `Rocket Type:`, `Launch Date:`, `Launch Location:` and `Description:`. If the bot stops finding launches, compare `wallops_debug.html` against the regular expressions in `check_for_specific_launch_times()`.
