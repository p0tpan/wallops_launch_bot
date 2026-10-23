import requests
from bs4 import BeautifulSoup
import datetime
import re
import time
import logging
from atproto import Client
from dateutil import parser as date_parser
import unicodedata
import os


# Set up logging
def setup_logging():
    log_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(log_dir, "wallops_launch_bot.log")

    # Configure logging
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Add console handler for errors
    console = logging.StreamHandler()
    console.setLevel(logging.ERROR)
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)

    logging.info("=== Wallops Launch Bot Started ===")


def count_graphemes(text):
    """Count graphemes in text (what users perceive as characters, including emoji)"""
    # This is a simplified approach - full grapheme segmentation is complex
    # For most Western text, this will be accurate enough
    graphemes = 0
    i = 0
    while i < len(text):
        # Check if this is an emoji (surrogate pairs or certain ranges)
        if ord(text[i]) >= 0x1F000:  # Emoji and other special chars
            graphemes += 1
            i += 1
            # Skip any combining characters that might follow
            while i < len(text) and unicodedata.combining(text[i]):
                i += 1
        else:
            # Regular character
            graphemes += 1
            i += 1
    return graphemes


def clean_time_info(time_info):
    """Clean time information to remove location data"""
    if time_info:
        # Remove "Location:" and everything after it
        if "Location:" in time_info:
            time_info = time_info.split("Location:")[0].strip()

        # Also look for location patterns like "Wallops Island, Virginia"
        location_patterns = [
            r',\s*[A-Z][a-z]+(\s+[A-Z][a-z]+)?,\s*[A-Z][a-z]+',  # City, State
            r'at\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?',  # at Location
            r'from\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?'  # from Location
        ]

        for pattern in location_patterns:
            time_info = re.sub(pattern, '', time_info)

        # Clean up any trailing commas or multiple spaces
        time_info = re.sub(r',\s*$', '', time_info)
        time_info = re.sub(r'\s+', ' ', time_info).strip()

    return time_info

def check_for_specific_launch_times():
    url = "https://www.nasa.gov/wallops-launch-schedule/"

    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        logging.info(f"Successfully retrieved page with status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to retrieve the webpage: {e}")
        return None, None

    soup = BeautifulSoup(response.text, 'html.parser')

    page_title = soup.title.text if soup.title else "No title found"
    logging.info(f"Page title: {page_title}")

    with open("wallops_debug.html", "w", encoding="utf-8") as f:
        f.write(response.text)
    logging.info("Saved HTML to wallops_debug.html for debugging")

    # ── NEW FIELD NAMES ──────────────────────────────────────────────────────
    # Website now uses:
    #   "Rocket Type:"  (was "Vehicle:" / "Mission:")
    #   "Launch Date:"  (was "Date:")
    #   "Launch Location:" (was "Location:")
    #   "Description:"  (was "Mission:" description)
    # ────────────────────────────────────────────────────────────────────────

    time_pattern = re.compile(
        r'\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|EDT|EST|UTC|GMT|PDT|PST)', re.IGNORECASE
    )
    today = datetime.datetime.now().date()

    launches_with_specific_times = []
    all_launches = []

    # Find every container that has at least "Rocket Type:" and "Launch Date:"
    potential_containers = soup.find_all(['p', 'div', 'section', 'li', 'article'])

    # MAX_ENTRY_CHARS: real launch entries are compact. Any container whose
    # text exceeds this is a page-wrapper that happens to contain a launch
    # entry somewhere inside it — skip it and let the inner element match.
    MAX_ENTRY_CHARS = 2000

    launch_entries = []

    for container in potential_containers:
        text = container.get_text(" ", strip=True)
        # Normalise whitespace before length check so we measure real content
        text_norm = re.sub(r'\s+', ' ', text)
        if len(text_norm) > MAX_ENTRY_CHARS:
            continue  # too large — this is a page/section wrapper, not a launch entry
        if (re.search(r'Rocket Type:', text_norm, re.IGNORECASE) and
                re.search(r'Launch Date:', text_norm, re.IGNORECASE)):
            launch_entries.append(container)
            logging.info(f"Found candidate container ({len(text_norm)} chars): {text_norm[:120]}...")

    logging.info(f"Processing {len(launch_entries)} potential launch entries")

    for entry in launch_entries:
        text = entry.get_text(" ", strip=True)
        text = re.sub(r'[\xa0\u200b]+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # ── Extract each field with updated labels ───────────────────────────
        rocket_match = re.search(
            r'Rocket\s*Type\s*:\s*(.+?)(?=Launch\s*Date|Launch\s*Location|Description|$)',
            text, re.IGNORECASE | re.DOTALL
        )
        date_match = re.search(
           r'Launch\s*Date\s*:\s*(.+?)(?=Launch\s*Location|Rocket\s*Type|Description|$)',
           text, re.IGNORECASE | re.DOTALL
        )
        location_match = re.search(
            r'Launch\s*Location\s*:\s*(.+?)(?=Description|Rocket\s*Type|Launch\s*Date|$)',
            text, re.IGNORECASE | re.DOTALL
        )
        desc_match = re.search(
            r'Description\s*:\s*(.+?)(?=Rocket\s*Type|Launch\s*Date|Launch\s*Location|$)',
            text, re.IGNORECASE | re.DOTALL
        )
        logging.info(f"Regex results — rocket:{bool(rocket_match)} date:{bool(date_match)} in text: {text[:150]}")

        if not (rocket_match and date_match):
            logging.info(f"Skipping entry — missing Rocket Type or Launch Date in: {text[:80]}")
            continue

        rocket_type  = rocket_match.group(1).strip()[:200]
        date_text    = date_match.group(1).strip()[:100]
        location     = (location_match.group(1).strip()[:200] if location_match else None)
        description  = (desc_match.group(1).strip()[:300]     if desc_match     else None)

        # ── Pull the time out of the date field ─────────────────────────────
        # e.g. "June 24-25, 2026, 5:30 a.m.-9:30 a.m. EDT"
        time_info = None
        time_in_date = time_pattern.search(date_text)
        if time_in_date:
            # Everything from the first time token onward becomes time_info
            time_info = date_text[time_in_date.start():].strip()
            # Strip the time portion from date_text so parsing works
            date_text = date_text[:time_in_date.start()].rstrip(', ').strip()

        # ── Parse the date ───────────────────────────────────────────────────
        launch_date = None
        try:
            date_clean = re.sub(r'\s*\(.*?\)', '', date_text)   # drop parentheticals
            date_clean = re.sub(r'[-–]\d+', '', date_clean)     # "24-25" → "24"
            launch_date = date_parser.parse(date_clean, fuzzy=True).date()
            logging.info(f"Parsed '{date_clean}' → {launch_date}")
        except Exception as e:
            logging.warning(f"Could not parse date '{date_text}': {e}")

        launch_info = {
            'mission':     description or rocket_type,   # use description as mission label
            'vehicle':     rocket_type,
            'date':        date_text,
            'date_obj':    launch_date,
            'time':        time_info,
            'location':    location,
        }

        logging.info(
            f"Launch found → vehicle={rocket_type!r}  date={date_text!r}  time={time_info!r}"
        )

        all_launches.append(launch_info)
        if time_info and time_pattern.search(time_info):
            launches_with_specific_times.append(launch_info)

    logging.info(f"Processed {len(all_launches)} valid launches")

    # ── Deduplicate launches_with_specific_times ─────────────────────────────
    # The HTML scraper uses text[:200] as a seen-key, which means nested
    # containers (e.g. a <div> wrapping a <p>) can both pass dedup and produce
    # two identical-looking entries for the same launch. Collapse them here by
    # (mission, date) so the posting loop only fires once per real launch.
    seen_launch_keys = set()
    deduped = []
    for lnch in launches_with_specific_times:
        key = (lnch.get('mission', ''), lnch.get('date', ''))
        if key not in seen_launch_keys:
            seen_launch_keys.add(key)
            deduped.append(lnch)
        else:
            logging.info(f"Deduplicating repeat launch entry: {key}")
    launches_with_specific_times = deduped

    # ── Find closest future launch ───────────────────────────────────────────
    closest_launch   = None
    min_days_diff    = float('inf')

    for launch in all_launches:
        if launch['date_obj'] and launch['date_obj'] >= today:
            days_diff = (launch['date_obj'] - today).days
            if days_diff < min_days_diff:
                min_days_diff  = days_diff
                closest_launch = launch

    if closest_launch is None and all_launches:
        logging.info("No future launches with parsed dates; using first entry as fallback")
        closest_launch = all_launches[0]

    if closest_launch:
        logging.info(f"Closest launch: {closest_launch['mission']} on {closest_launch['date']}")
    else:
        logging.info("No closest launch determined")

    return launches_with_specific_times, closest_launch


def create_post_text(launch=None, closest_launch=None, has_specific_times=False, is_update=False):
    """Create post text with proper character limit enforcement"""
    current_date = datetime.datetime.now().strftime('%Y-%m-%d')
    post_text = ""

    # Helper to shorten vehicle line
    def shorten_vehicle(vehicle_text):
        if not vehicle_text:
            return None
        # Option 1: just take the first part before a dash
        short_vehicle = vehicle_text.split("-")[0].strip()
        # Option 2 (fallback): if still too long, truncate
        if count_graphemes(short_vehicle) > 50:
            short_vehicle = short_vehicle[:47] + "..."
        return short_vehicle

    if launch and not is_update:
        # Specific launch post
        post_text = f"🚀 Wallops Launch Alert 🚀\n\n"
        post_text += f"Mission: {launch['mission']}\n"
        if launch.get('vehicle'):
            short_vehicle = shorten_vehicle(launch['vehicle'])
            post_text += f"Vehicle: {short_vehicle}\n"
        post_text += f"Date: {launch['date']}\n"
        if launch.get('time'):
            post_text += f"Time: {launch['time']}\n\n"
    else:
        # General update
        post_text = f"🚀 Wallops Launch Update ({current_date}) 🚀\n\n"
        if not has_specific_times:
            post_text += ""

    # Closest launch info — only append when it genuinely adds new information.
    # In a specific-launch alert (launch is set, is_update is False) we skip this
    # block entirely: the launch IS the closest launch, so repeating it would
    # produce the bare "🚀 Wallops Launch Alert 🚀\n\n..." second post.
    # In an update post (is_update=True) we always show the closest launch.
    show_closest = (
        closest_launch is not None
        and (
            is_update  # general update: always show closest
            or (launch is None)  # no specific launch in context
            or (closest_launch['mission'] != launch.get('mission'))  # truly different mission
        )
    )
    if show_closest:
        post_text += "➡️ Next upcoming launch:\n"
        post_text += f"Mission: {closest_launch['mission']}\n"
        if closest_launch.get('vehicle'):
            short_vehicle = shorten_vehicle(closest_launch['vehicle'])
            post_text += f"Vehicle: {short_vehicle}\n"
        post_text += f"Date: {closest_launch['date']}\n"
        if closest_launch.get('time'):
            post_text += f"Time: {closest_launch['time']}\n"

    # Trim if over limit
    if count_graphemes(post_text) > 300:
        lines = post_text.split('\n')
        trimmed_text = ""
        for line in lines:
            if count_graphemes(trimmed_text + line + '\n') <= 297:
                trimmed_text += line + '\n'
            else:
                break
        post_text = trimmed_text + "..."

    grapheme_count = count_graphemes(post_text)
    logging.info(f"Post has {grapheme_count} graphemes")
    return post_text


def post_to_bluesky(launches=None, closest_launch=None, has_specific_times=False):
    # Bluesky ATP credentials - read from environment variables
    BLUESKY_USERNAME = os.environ.get("BLUESKY_USERNAME")
    BLUESKY_PASSWORD = os.environ.get("BLUESKY_PASSWORD")

    if not BLUESKY_USERNAME or not BLUESKY_PASSWORD:
        logging.error("BLUESKY_USERNAME and BLUESKY_PASSWORD environment variables must be set")
        return False

    try:
        # Create a client and authenticate
        client = Client()
        client.login(BLUESKY_USERNAME, BLUESKY_PASSWORD)
        logging.info("Successfully logged in to Bluesky")

        # Log closest launch info for debugging
        if closest_launch:
            logging.info(f"Ready to post closest launch: {closest_launch['mission']}")
        else:
            logging.info("No closest launch available to post")

        if has_specific_times and launches:
                        # Handle specific launch posts
                        for launch in launches:
                                # Skip launches that are missing any of the three core fields.
                                # A launch without a time has no business being in the
                                # specific-times list, and one without a mission or date
                                # would produce an empty / misleading post.
                                if not launch.get('mission') or not launch.get('date') or not launch.get('time'):
                                        logging.info(
                                            f"Skipping launch entry missing required fields "
                                            f"(mission={bool(launch.get('mission'))}, "
                                            f"date={bool(launch.get('date'))}, "
                                            f"time={bool(launch.get('time'))})"
                                        )
                                        continue

                                # Create post text with character limit in mind
                                post_text = create_post_text(launch=launch, closest_launch=closest_launch)

                                # Log output
                                logging.info(f"Post text: {post_text}")
                                logging.info(f"Character count: {count_graphemes(post_text)}")

                                # Create a post
                                client.send_post(text=post_text)
                                logging.info(f"Posted about launch: {launch['mission']}")

                                # Sleep for a moment to avoid rate limits
                                time.sleep(2)
        else:
            # Handle general update post
            if closest_launch:
                # Create post text with character limit in mind
                post_text = create_post_text(closest_launch=closest_launch, has_specific_times=has_specific_times,
                                             is_update=True)

                # Log output
                logging.info(f"Post text: {post_text}")
                logging.info(f"Character count: {count_graphemes(post_text)}")

                # Create a post
                client.send_post(text=post_text)
                logging.info("Posted update about launches")
            else:
                # Simple post when no launches found
                post_text = "🚀 Wallops Launch Update 🚀\n\nNo upcoming launches found on the schedule at this time."

                # Log output
                logging.info(f"Post text: {post_text}")
                logging.info(f"Character count: {count_graphemes(post_text)}")

                # Create a post
                client.send_post(text=post_text)
                logging.info("Posted update about no launches found")

        return True
    except Exception as e:
        logging.error(f"Failed to post to Bluesky: {e}")
        return False


def main():
    # Set up logging first
    setup_logging()

    logging.info("Checking for NASA Wallops launches with specific times...")
    launches_with_specific_times, closest_launch = check_for_specific_launch_times()

    if closest_launch:
        logging.info(f"Closest upcoming launch: {closest_launch['mission']} on {closest_launch['date']}")
    else:
        logging.info("No upcoming launches found")

    if launches_with_specific_times:
        logging.info(f"Found {len(launches_with_specific_times)} launches with specific times!")
        for launch in launches_with_specific_times:
            logging.info(f"Mission: {launch['mission']}")
            logging.info(f"Time: {launch['time']}")
            logging.info("---")

        # Post the launches to Bluesky
        success = post_to_bluesky(launches_with_specific_times, closest_launch, has_specific_times=True)
    else:
        logging.info("No launches with specific times found on the schedule.")

        # Post update to Bluesky that no specific times were found
        success = post_to_bluesky(closest_launch=closest_launch, has_specific_times=False)

    if success:
        logging.info("Successfully posted to Bluesky!")
    else:
        logging.error("Failed to post to Bluesky. Check your credentials and try again.")

    logging.info("=== Wallops Launch Bot Finished ===")


if __name__ == "__main__":
    main()