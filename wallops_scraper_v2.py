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
    # URL to scrape
    url = "https://www.nasa.gov/wallops-launch-schedule/"

    # Get the webpage content
    try:
        response = requests.get(url)
        response.raise_for_status()
        logging.info(f"Successfully retrieved page with status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to retrieve the webpage: {e}")
        return None, None

    # Parse the HTML content
    soup = BeautifulSoup(response.text, 'html.parser')

    # Log page title to confirm we're getting the right page
    page_title = soup.title.text if soup.title else "No title found"
    logging.info(f"Page title: {page_title}")

    # Find launch information - based on the HTML structure you provided
    # Look for sections with "Mission:", "Vehicle:", "Date:", "Time:" text

    # First, save the HTML for debugging if needed
    with open("wallops_debug.html", "w", encoding="utf-8") as f:
        f.write(response.text)
    logging.info("Saved HTML to wallops_debug.html for debugging")

    # Try multiple approaches to find launch information

    # Approach 1: Look for paragraphs or divs containing the mission info
    launch_entries = []

    # Find all paragraphs or divs that might contain launch info
    potential_containers = soup.find_all(['p', 'div', 'section'])

    for container in potential_containers:
        text = container.get_text()
        # If the container has multiple mission keywords, it likely contains launch info
        if (re.search(r'Mission:', text, re.IGNORECASE) and
                (re.search(r'Vehicle:', text, re.IGNORECASE) or
                 re.search(r'Date:', text, re.IGNORECASE) or
                 re.search(r'Time:', text, re.IGNORECASE))):
            launch_entries.append(container)
            logging.info(f"Found potential launch entry: {text[:100]}...")

    # If we didn't find anything with that approach, try a broader search
    if not launch_entries:
        logging.info("No launch entries found with first approach, trying broader search...")
        # Look for strong tags with "Mission:" text
        mission_tags = soup.find_all('strong', string=lambda s: s and 'Mission:' in s)

        for tag in mission_tags:
            # Get the parent container that should have all the launch info
            parent = tag.find_parent(['p', 'div', 'li'])
            if parent and parent not in launch_entries:
                launch_entries.append(parent)
                logging.info(f"Found mission tag parent: {parent.get_text()[:100]}...")

    # Process the entries to find specific times and all launches
    launches_with_specific_times = []
    all_launches = []

    # Pattern to match specific times (e.g., "10:15 a.m. EDT", "15:30 UTC", "12:45 p.m.")
    time_pattern = re.compile(r'\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|EDT|EST|UTC|GMT|PDT|PST)', re.IGNORECASE)
    today = datetime.datetime.now().date()

    logging.info(f"Processing {len(launch_entries)} potential launch entries")

    for entry in launch_entries:
        entry_text = entry.get_text()

        # Extract mission information
        mission_match = re.search(r'Mission:[\s\xa0]*([^:]+?)(?=Vehicle:|Date:|Time:|$)', entry_text, re.IGNORECASE)
        vehicle_match = re.search(r'Vehicle:[\s\xa0]*([^:]+?)(?=Mission:|Date:|Time:|$)', entry_text, re.IGNORECASE)

        # Modified regex to stop at "Time:"
        date_match = re.search(r'Date:[\s\xa0]*([^:]+?)(?=Time:|$)', entry_text, re.IGNORECASE)

        # Extract time information separately
        time_match = re.search(r'Time:[\s\xa0]*([^:]+?)(?=Location:|$)', entry_text, re.IGNORECASE)

        # Only process if we found at least mission and date
        if not (mission_match and date_match):
            continue

        mission = mission_match.group(1).strip() if mission_match else None
        vehicle = vehicle_match.group(1).strip() if vehicle_match else None
        date_text = date_match.group(1).strip() if date_match else None
        time_info = time_match.group(1).strip() if time_match else None

        # Skip entries with missing critical info
        if not mission or not date_text:
            continue

        # Try to clean up mission name (remove URLs etc.)
        if mission:
            # Remove common HTML artifacts
            mission = re.sub(r'\s+', ' ', mission)  # Normalize whitespace
            mission = re.sub(r'\xa0', ' ', mission)  # Replace non-breaking spaces

        # Clean up vehicle info if needed
        if vehicle:
            vehicle = re.sub(r'\s+', ' ', vehicle)  # Normalize whitespace
            vehicle = re.sub(r'\xa0', ' ', vehicle)  # Replace non-breaking spaces

        # Clean up date info
        if date_text:
            date_text = re.sub(r'\s+', ' ', date_text)  # Normalize whitespace
            date_text = re.sub(r'\xa0', ' ', date_text)  # Replace non-breaking spaces

        # Clean up time info
        if time_info:
            time_info = re.sub(r'\s+', ' ', time_info)  # Normalize whitespace
            time_info = re.sub(r'\xa0', ' ', time_info)  # Replace non-breaking spaces
            # Apply extra cleaning to remove location information
            time_info = clean_time_info(time_info)

        # Try to parse the date to determine if it's in the future
        launch_date = None
        try:
            # Handle various date formats
            date_text_clean = re.sub(r'\s*\(.*?\)', '', date_text)  # Remove parenthetical comments
            date_text_clean = re.sub(r',\s*back-up day.*', '', date_text_clean)  # Remove backup dates
            launch_date = date_parser.parse(date_text_clean, fuzzy=True).date()
            logging.info(f"Successfully parsed date '{date_text_clean}' to {launch_date}")
        except Exception as e:
            logging.warning(f"Could not parse date '{date_text}': {e}")

        # Create the launch info dictionary
        launch_info = {
            'mission': mission,
            'vehicle': vehicle,
            'date': date_text,
            'date_obj': launch_date,
            'time': time_info
        }

        # Log debug info
        logging.info(f"Found launch: {mission} on {date_text} at {time_info}")

        # Only add entries with valid mission and date
        if mission != "Unknown Mission" and date_text != "Unknown Date":
            all_launches.append(launch_info)

            # Check if time information contains a specific time rather than just "Morning" or "Afternoon"
            if time_info and time_pattern.search(time_info):
                launches_with_specific_times.append(launch_info)

    logging.info(f"Processed {len(all_launches)} valid launches")

    # Find the closest future launch
    closest_launch = None
    min_days_diff = float('inf')

    for launch in all_launches:
        if launch['date_obj'] and launch['date_obj'] >= today:
            days_diff = (launch['date_obj'] - today).days
            if days_diff < min_days_diff:
                min_days_diff = days_diff
                closest_launch = launch

    # Only use a fallback if there are valid launches
    if closest_launch is None and all_launches:
        logging.info("No future launches with valid dates found, using first available launch as closest")
        closest_launch = all_launches[0]

    # Log debug info
    if closest_launch:
        logging.info(f"Closest launch determined: {closest_launch['mission']} on {closest_launch['date']}")
    else:
        logging.info("No closest launch could be determined")

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

    # Closest launch info
    if closest_launch and (not launch or closest_launch['mission'] != launch.get('mission')):
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
    # Bluesky ATP credentials - Replace with your details
    BLUESKY_USERNAME = "wallopslaunchbot.bsky.social"
    BLUESKY_PASSWORD = "wallopsnewpassword"

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
