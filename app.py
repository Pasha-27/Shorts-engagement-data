import streamlit as st
import requests
import re
import pandas as pd
from datetime import datetime
from urllib.parse import urlparse

# --------------------------------------------------
# 1. Page Configuration & Dark Theme CSS Injection
# --------------------------------------------------
st.set_page_config(
    page_title="YouTube Channel: Recent Shorts",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* Overall background and text color */
    .stApp {
        background-color: #0f1115;
        color: #e0e0e0;
    }
    /* Left panel (static info) styling */
    .static-panel {
        background-color: #1b1f23;
        padding: 20px;
        border-radius: 10px;
        height: 100%;
    }
    /* Input area styling */
    .input-panel {
        background-color: #1e2228;
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    /* Text input and button styling */
    .stTextInput > div > input {
        background-color: #0f1115 !important;
        color: #e0e0e0 !important;
        border: 1px solid #333740 !important;
        border-radius: 5px !important;
    }
    .stButton > button {
        background-color: #e63946 !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 5px !important;
        padding: 8px 16px !important;
    }
    .stButton > button:hover {
        background-color: #d62839 !important;
    }
    /* DataFrame/table styling */
    .stDataFrame {
        background-color: #1b1f23 !important;
        color: #e0e0e0 !important;
    }
    .stDataFrame th {
        color: #ffffff !important;
        background-color: #1e2228 !important;
    }
    .stDataFrame td {
        color: #e0e0e0 !important;
    }
    /* HTML table rendered via to_html */
    .clickable-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 10px;
    }
    .clickable-table th, .clickable-table td {
        border: 1px solid #333740;
        padding: 8px;
    }
    .clickable-table th {
        background-color: #1e2228;
        color: #ffffff;
        text-align: left;
    }
    .clickable-table td {
        background-color: #1b1f23;
        color: #e0e0e0;
    }
    .clickable-table a {
        color: #1e90ff;
        text-decoration: none;
    }
    .clickable-table a:hover {
        text-decoration: underline;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# --------------------------------------------------
# 2. Helper Functions
# --------------------------------------------------

def extract_channel_identifier(url_or_id: str):
    """
    Determine channel identifier mode:
      - "id"       → literal channel ID (starts with "UC")
      - "username" → YouTube username (for /user/…)
      - "custom"   → custom URL handle (for /c/…)
      - "raw"      → try as username then search
    Returns (mode, identifier).
    """
    text = url_or_id.strip()
    if re.match(r"^UC[\w-]{21}$", text):
        return ("id", text)

    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            prefix, ident = parts[0].lower(), parts[1]
            if prefix == "channel":
                return ("id", ident)
            if prefix == "user":
                return ("username", ident)
            if prefix == "c":
                return ("custom", ident)
        fallback = parts[-1]
        if fallback:
            if fallback.startswith("UC") and re.match(r"^UC[\w-]{21}$", fallback):
                return ("id", fallback)
            return ("custom", fallback)

    if text.startswith("UC") and re.match(r"^UC[\w-]{21}$", text):
        return ("id", text)
    return ("raw", text)


def parse_iso_duration_to_seconds(duration_iso: str) -> int:
    """
    Convert ISO 8601 duration (e.g. "PT1M23S", "PT45S") into total seconds.
    """
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_iso)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def resolve_channel_id(api_key: str, mode: str, identifier: str) -> str:
    """
    Resolve to a literal channel ID ("UC…") using:
      - mode == "id"       → return as-is
      - mode == "username" → channels?forUsername=…
      - mode == "custom"   → search?q=…
      - mode == "raw"      → try username, else search
    """
    base = "https://www.googleapis.com/youtube/v3"

    if mode == "id":
        return identifier

    if mode == "username":
        url = f"{base}/channels?part=id&forUsername={identifier}&key={api_key}"
        resp = requests.get(url)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                return items[0]["id"]
        mode = "custom"

    if mode in ("custom", "raw"):
        url = f"{base}/search?part=snippet&type=channel&q={identifier}&maxResults=1&key={api_key}"
        resp = requests.get(url)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                return items[0]["snippet"]["channelId"]

    return None


def fetch_uploads_playlist_id(api_key: str, channel_id: str) -> str:
    """
    Given a channel ID, fetch the "uploads" playlist ID from contentDetails.
    """
    url = (
        f"https://www.googleapis.com/youtube/v3/channels"
        f"?part=contentDetails&id={channel_id}&key={api_key}"
    )
    resp = requests.get(url)
    if resp.status_code != 200:
        return None
    items = resp.json().get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_videos_under_2_min(api_key: str, uploads_playlist_id: str, max_results: int = 40):
    """
    Paginate through the uploads playlist, fetch video details in batches,
    filter by duration < 120 seconds, and collect up to max_results videos.
    Returns a list of dicts:
      { "Video Title", "Views", "Likes", "Comments", "Engagement Rate", "Upload Date", "Video Link" }
    """
    collected = []
    playlist_url = "https://www.googleapis.com/youtube/v3/playlistItems"
    videos_url = "https://www.googleapis.com/youtube/v3/videos"
    next_token = None

    while len(collected) < max_results:
        params = {
            "part": "contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": 50,
            "key": api_key
        }
        if next_token:
            params["pageToken"] = next_token

        resp = requests.get(playlist_url, params=params)
        if resp.status_code != 200:
            break
        data = resp.json()
        items = data.get("items", [])
        if not items:
            break

        batch_ids = [it["contentDetails"]["videoId"] for it in items]
        if not batch_ids:
            break

        vid_params = {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch_ids),
            "key": api_key
        }
        vid_resp = requests.get(videos_url, params=vid_params)
        if vid_resp.status_code != 200:
            break
        vdata = vid_resp.json().get("items", [])

        for vid in vdata:
            if len(collected) >= max_results:
                break

            duration = parse_iso_duration_to_seconds(vid["contentDetails"]["duration"])
            if duration < 120:
                snippet = vid["snippet"]
                title = snippet.get("title", "—")
                published_at = snippet.get("publishedAt", "")
                # Convert ISO timestamp to YYYY-MM-DD
                try:
                    upload_date = datetime.fromisoformat(published_at.replace("Z", "+00:00")).date().isoformat()
                except:
                    upload_date = published_at[:10] if published_at else ""

                stats = vid.get("statistics", {})
                views = int(stats.get("viewCount", 0))
                likes = int(stats.get("likeCount", 0))
                comments = int(stats.get("commentCount", 0))
                engagement = 0.0
                if views > 0:
                    engagement = (likes + comments) / views * 100

                link = f"https://www.youtube.com/watch?v={vid['id']}"
                collected.append({
                    "Video Title": title,
                    "Views": f"{views:,}",
                    "Likes": f"{likes:,}",
                    "Comments": f"{comments:,}",
                    "Engagement Rate": f"{engagement:.2f}%",
                    "Upload Date": upload_date,
                    "Video Link": f'<a href="{link}" target="_blank">Watch</a>'
                })

        next_token = data.get("nextPageToken")
        if not next_token:
            break

    return collected[:max_results]


# --------------------------------------------------
# 3. Layout: Left Panel (20%) & Right Panel (80%)
# --------------------------------------------------

# Create a two-column layout with 20% width for left, 80% for right
left_col, right_col = st.columns([1, 4])

with left_col:
    st.markdown('<div class="static-panel">', unsafe_allow_html=True)
    st.header("📋 App Description")
    st.write("""
    • Enter a **YouTube Channel URL**, **Channel ID**, or **Username** on the right panel.  
    • Click **Fetch Videos** to retrieve up to 40 of the most recent videos under 2 minutes.  
    • Results will be displayed in a table with columns:  
      – Video Title  
      – Views  
      – Likes  
      – Comments  
      – Engagement Rate (Likes + Comments) / Views  
      – Upload Date  
      – Video Link  
    """)
    st.markdown('</div>', unsafe_allow_html=True)

with right_col:
    st.markdown('<div class="input-panel">', unsafe_allow_html=True)
    st.header("🔍 Find Shorts")
    channel_input = st.text_input(
        label="Channel URL / ID / Username",
        placeholder="e.g. https://www.youtube.com/c/ChannelName"
    )
    fetch_button = st.button("Fetch Videos", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

# --------------------------------------------------
# 4. On Fetch: Resolve → Fetch → Display Table
# --------------------------------------------------

# Retrieve API key from secrets
try:
    api_key = st.secrets["youtube_api_key"].strip()
except KeyError:
    api_key = None

if not api_key:
    st.error("🔒 Missing `youtube_api_key` in Streamlit secrets. Add it and rerun.")
    st.stop()

if fetch_button:
    if not channel_input.strip():
        st.error("Please enter a channel URL, ID, or username.")
    else:
        with st.spinner("Resolving Channel ID…"):
            mode, identifier = extract_channel_identifier(channel_input)
            channel_id = resolve_channel_id(api_key, mode, identifier)

        if not channel_id:
            st.error("❌ Could not resolve a valid Channel ID. Check your input.")
        else:
            with st.spinner("Fetching Uploads Playlist…"):
                uploads_playlist_id = fetch_uploads_playlist_id(api_key, channel_id)

            if not uploads_playlist_id:
                st.error("❌ Unable to find uploads playlist for this channel.")
            else:
                with st.spinner("Scanning for recent videos under 2 minutes…"):
                    videos_data = fetch_videos_under_2_min(api_key, uploads_playlist_id, max_results=40)

                if not videos_data:
                    st.warning("No videos under 2 minutes found for this channel.")
                else:
                    st.success(f"Found {len(videos_data)} videos under 2 minutes.")
                    df = pd.DataFrame(videos_data)

                    # Calculate and display average engagement rate
                    eng_rates = [float(item["Engagement Rate"].strip("%")) for item in videos_data]
                    avg_eng = sum(eng_rates) / len(eng_rates) if eng_rates else 0.0
                    st.markdown(f"## **Average Engagement Rate: {avg_eng:.2f}%**")

                    # Calculate and display average views
                    view_counts = [int(item["Views"].replace(",", "")) for item in videos_data]
                    avg_views = sum(view_counts) / len(view_counts) if view_counts else 0.0
                    st.markdown(f"## **Average Views: {avg_views:,.0f}**")

                    # Convert DataFrame to HTML with clickable links and display
                    html_table = df.to_html(escape=False, index=False, classes="clickable-table")
                    st.write(html_table, unsafe_allow_html=True)
