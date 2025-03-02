import os
import time
import random
import json
import csv
import argparse
from datetime import datetime
from tqdm import tqdm
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# Constants
CLIENT_SECRETS_FILE = "client_secrets.json"  # Path to your client secrets file
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
DEFAULT_WATCH_PERCENTAGE = 0.8  # Watch 80% of each video by default
CONFIG_FILE = "config.json"
HISTORY_FILE = "watch_history.csv"
BLACKLIST_FILE = "blacklist.csv"

# Set up command line arguments
parser = argparse.ArgumentParser(description="YouTube Focus App - Control your recommendations")
parser.add_argument('--videos', type=str, help='Comma-separated list of video IDs to watch')
parser.add_argument('--file', type=str, help='Path to file containing video IDs (one per line)')
parser.add_argument('--percentage', type=float, default=DEFAULT_WATCH_PERCENTAGE, 
                    help='Percentage of each video to watch (0.0 to 1.0)')
parser.add_argument('--headless', action='store_true', help='Run browser in headless mode')
parser.add_argument('--blacklist', type=str, help='Comma-separated list of video IDs to blacklist')
parser.add_argument('--blacklist-file', type=str, help='Path to file containing video IDs to blacklist')
parser.add_argument('--dont-recommend-channels', action='store_true', 
                    help='Click "Don\'t recommend channel" for blacklisted videos')
parser.add_argument('--randomize', action='store_true', 
                    help='Randomize watch percentage (±10%)')
args = parser.parse_args()

# Load or create configuration
def load_config():
    """Load configuration from file or create default config."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        default_config = {
            "default_watch_percentage": DEFAULT_WATCH_PERCENTAGE,
            "headless": False,
            "random_percentage": False,
            "productive_channels": [],
            "click_dont_recommend": True
        }
        save_config(default_config)
        return default_config

def save_config(config):
    """Save configuration to file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

# Load or create watch history
def load_history():
    """Load watch history from CSV file."""
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                history.append(row)
    return history

def save_to_history(video_id, title, percentage, status):
    """Append a video to the watch history CSV."""
    fieldnames = ['video_id', 'title', 'percentage', 'watched_at', 'status']
    file_exists = os.path.exists(HISTORY_FILE)
    
    with open(HISTORY_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'video_id': video_id,
            'title': title,
            'percentage': percentage,
            'watched_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'status': status
        })

# Load or create blacklist
def load_blacklist():
    """Load blacklist from CSV file."""
    blacklist = []
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                blacklist.append(row['video_id'])
    return blacklist

def add_to_blacklist(video_id, reason="User blacklisted"):
    """Add a video to the blacklist CSV."""
    fieldnames = ['video_id', 'reason', 'added_at']
    file_exists = os.path.exists(BLACKLIST_FILE)
    
    with open(BLACKLIST_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'video_id': video_id,
            'reason': reason,
            'added_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

# Authenticate and create YouTube API client
def get_authenticated_service():
    """
    Authenticate with the YouTube API using OAuth 2.0.
    Returns a YouTube API service object.
    """
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise FileNotFoundError(
            f"Client secrets file '{CLIENT_SECRETS_FILE}' not found. "
            "Please download it from Google Developers Console."
        )
    
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    credentials = flow.run_local_server(port=0)
    return build("youtube", "v3", credentials=credentials)

# Get video details from YouTube API
def get_video_details(youtube, video_id):
    """
    Get title and other details for a video using the YouTube API.
    Args:
        youtube: The YouTube API client.
        video_id (str): The YouTube video ID.
    Returns:
        dict: Video details including title.
    """
    response = youtube.videos().list(
        part="snippet,contentDetails",
        id=video_id
    ).execute()
    
    if not response.get('items'):
        return None
    
    item = response['items'][0]
    return {
        'title': item['snippet']['title'],
        'channel_title': item['snippet']['channelTitle'],
        'duration': item['contentDetails']['duration']
    }

# Simulate watching a video using Selenium
def simulate_watch(youtube, video_id, watch_percentage, headless=True, randomize=False, blacklist=None):
    """
    Simulate watching a YouTube video for a specified percentage of its duration.
    Args:
        youtube: The YouTube API client.
        video_id (str): The YouTube video ID.
        watch_percentage (float): Percentage of the video to watch (0.0 to 1.0).
        headless (bool): Whether to run in headless mode.
        randomize (bool): Whether to randomize the watch percentage.
        blacklist (list): List of video IDs to blacklist.
    """
    # Get video details first
    video_details = get_video_details(youtube, video_id)
    if not video_details:
        print(f"Could not retrieve details for video {video_id}")
        return
    
    video_title = video_details['title']
    print(f"Processing: {video_title} ({video_id})")
    
    # Apply randomization if enabled
    if randomize:
        variation = random.uniform(-0.1, 0.1)  # ±10%
        watch_percentage = max(0.1, min(0.95, watch_percentage + variation))
        print(f"Randomized watch percentage: {watch_percentage:.2f}")
    
    # Set up Selenium WebDriver
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless")  # Run in headless mode (no GUI)
        options.add_argument("--disable-gpu")  # Disable GPU acceleration
    options.add_argument("--no-sandbox")  # Required for some environments
    options.add_argument("--mute-audio")  # Mute audio
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")
    driver = webdriver.Chrome(service=service, options=options)
    
    try:
        # Open YouTube video page
        url = f"https://www.youtube.com/watch?v={video_id}"
        driver.get(url)
        print(f"Opened video: {url}")
        
        # Wait for the page to load
        time.sleep(random.uniform(3, 5))
        
        try:
            # Accept cookies if the dialog appears
            cookie_button = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Accept all') or contains(., 'I agree')]"))
            )
            cookie_button.click()
            time.sleep(1)
        except (TimeoutException, NoSuchElementException):
            # Cookie dialog may not appear, that's fine
            pass

        # Wait for the video player to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "video"))
        )
        
        # Get video duration using JavaScript
        duration_script = """
        var video = document.querySelector('video');
        return video ? video.duration : null;
        """
        duration = driver.execute_script(duration_script)
        
        if duration is None or duration <= 0:
            print(f"Could not retrieve valid duration for video {video_id}")
            save_to_history(video_id, video_title, watch_percentage, "Failed - Invalid Duration")
            return
        
        # Calculate how long to watch
        watch_time = duration * watch_percentage
        print(f"Video duration: {duration:.2f}s, watching for {watch_time:.2f}s ({watch_percentage*100:.1f}%)")
        
        # Play the video
        play_script = """
        var video = document.querySelector('video');
        if (video) {
            video.play();
            // Skip to 2 seconds in to avoid ads
            video.currentTime = 2;
        }
        return video ? true : false;
        """
        video_played = driver.execute_script(play_script)
        
        if not video_played:
            print(f"Failed to play video {video_id}")
            save_to_history(video_id, video_title, watch_percentage, "Failed - Could Not Play")
            return
        
        # Try skipping any ads
        try:
            skip_ad_button = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.CLASS_NAME, "ytp-ad-skip-button"))
            )
            skip_ad_button.click()
            print("Skipped ad")
        except TimeoutException:
            # No ad or not skippable, that's fine
            pass
        
        # Show progress bar for the wait time
        for _ in tqdm(range(int(watch_time)), desc="Watching", unit="sec"):
            time.sleep(1)
            
            # Occasionally check if the video is still playing
            if random.random() < 0.1:  # 10% chance each second
                is_playing_script = """
                var video = document.querySelector('video');
                return video && !video.paused && !video.ended;
                """
                is_playing = driver.execute_script(is_playing_script)
                if not is_playing:
                    print("Video stopped playing. Resuming...")
                    driver.execute_script(play_script)
        
        # Pause the video
        pause_script = """
        var video = document.querySelector('video');
        if (video) video.pause();
        """
        driver.execute_script(pause_script)
        
        # Click like button (optional)
        try:
            like_button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label, 'like')]"))
            )
            like_button.click()
            print("Liked the video")
        except TimeoutException:
            print("Could not find or click like button")
        
        # Add to blacklist or click "Don't recommend channel" if requested
        if blacklist and video_id in blacklist:
            try:
                # Click the three dots menu
                menu_button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='More actions']"))
                )
                menu_button.click()
                time.sleep(1)
                
                # Click "Don't recommend channel"
                dont_rec_button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Don't recommend channel') or contains(text(), 'Not interested')]"))
                )
                dont_rec_button.click()
                print(f"Clicked 'Don't recommend channel' for {video_id}")
            except TimeoutException:
                print("Could not find or click 'Don't recommend channel' button")
        
        save_to_history(video_id, video_title, watch_percentage, "Success")
        print(f"Successfully watched {watch_percentage*100:.1f}% of video {video_id}")
        
    except Exception as e:
        print(f"Error simulating watch for video {video_id}: {e}")
        save_to_history(video_id, video_title if 'video_title' in locals() else "Unknown", 
                       watch_percentage, f"Error - {str(e)[:50]}")
    
    finally:
        driver.quit()

# Main function
def main():
    """
    Main function to authenticate and simulate watching multiple videos.
    """
    try:
        config = load_config()
        blacklist = load_blacklist()
        
        # Process command line arguments
        watch_percentage = args.percentage if args.percentage else config["default_watch_percentage"]
        headless = args.headless if args.headless is not None else config["headless"]
        randomize = args.randomize if args.randomize is not None else config["random_percentage"]
        
        # Get video IDs from command line args or file
        video_ids = []
        if args.videos:
            video_ids = [vid.strip() for vid in args.videos.split(',')]
        elif args.file and os.path.exists(args.file):
            with open(args.file, 'r') as f:
                video_ids = [line.strip() for line in f if line.strip()]
        
        # Handle blacklist additions
        if args.blacklist:
            for vid in args.blacklist.split(','):
                if vid.strip() not in blacklist:
                    add_to_blacklist(vid.strip())
                    blacklist.append(vid.strip())
        
        if args.blacklist_file and os.path.exists(args.blacklist_file):
            with open(args.blacklist_file, 'r') as f:
                for line in f:
                    vid = line.strip()
                    if vid and vid not in blacklist:
                        add_to_blacklist(vid)
                        blacklist.append(vid)
        
        # Authenticate with YouTube API
        youtube = get_authenticated_service()
        print("Successfully authenticated with YouTube API")
        
        if not video_ids:
            print("No video IDs provided. Please use --videos or --file options.")
            return
        
        print(f"Processing {len(video_ids)} videos...")
        
        # Simulate watching each video
        for video_id in video_ids:
            simulate_watch(
                youtube, 
                video_id, 
                watch_percentage, 
                headless=headless,
                randomize=randomize,
                blacklist=blacklist
            )
            
            # Add a random delay between videos to seem more natural
            if video_ids.index(video_id) < len(video_ids) - 1:
                delay = random.uniform(2, 5)
                print(f"Waiting {delay:.1f} seconds before next video...")
                time.sleep(delay)
        
        print(f"Completed processing {len(video_ids)} videos.")
        print(f"Watch history saved to {HISTORY_FILE}")
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()