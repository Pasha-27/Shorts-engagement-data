import streamlit as st
import requests
import re
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------
# 1. Page Configuration & Dark Theme CSS Injection
# --------------------------------------------------
st.set_page_config(
    page_title="YouTube Channel: Recent Shorts",
    layout="wide",
)

# Inject custom CSS for dark, modern styling
st.markdown(
    """
    <style>
    /* Main page background and text */
    .stApp {
        background-color: #0f1115;
        color: #e0e0e0;
    }
    /* Input containers (text_input, button) */
    .stTextInput, .stButton {
        background-color: #1e2228;
        color: #e0e0e0;
        border: 1px solid #333740;
        border-radius: 5px;
    }
    /* Table styling */
    .dataframe tbody tr th:only-of-type {
        vertical-align: middle;
    }
    .dataframe tbody tr th {
        vertical-align: top;
    }
    .dataframe thead th {
        text-align: left;
    }
    /* Markdown table styling */
    .markdown-table {
        width: 100%;
        border-collapse: collapse;
    }
    .markdown-table th,
    .markdown-table td {
        border: 1px solid #333740;
        padding: 8px;
    }
    .markdown-table th {
        background-color: #1e2228;
        color: #ffffff;
    }
    .markdown-table td {
        background-color: #1b1f23;
        color: #e0e0e0;
    }
    .markdown-table a {
        color: #1e90ff;
        text-decoration: none;
    }
    .markdown-table a:hover {
        text-decoration: underline;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🎬 YouTube Channel: Recent Shorts (< 2 Minutes)")
st.write(
    """
    Enter a **YouTube Channel URL**, **Channel ID**, or **Username** on the right, then click **Fetch**.  
    The app will return up to 40 of the most recent videos under 2 minutes, displayed in a table with:
    - Video Title (clickable)  
    - Views  
    - Likes  
    - Comments  
    - Engagement Rate (Likes + Comments) / Views  
    - Link to Video  
    """
)

# --------------------------------------------------
# 2. Helper Functions
# --------------------------------------------------

def extract_channel_identifier(url_or_id: str):
    """
    Given a channel URL or raw identifier, determine:
      - "id"       → Literal channel ID (starts with "UC")
      - "username" → YouTube username (for /user/…)
      - "custom"   → Custom URL handle (for /c/…)
      - "raw"      → Neither; will attempt search
    Returns (mode, identifier).
    """
    url_or_id = url_or_id.strip()
    # If it matches channel ID pattern (starts with UC + 21 chars), treat as ID
    if re.match(r"^UC[\w-]{21}$", url_or_id):
        return ("id", url_or_id)

    # If full URL, parse path segments
    if url_or_id.startswith("http://") or url_or_id.startswith("https://"):
        parsed = urlparse(url_or_id)
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            prefix, ident = parts[0].lower(), parts[1]
            if prefix == "channel":
                return ("id", ident)
            elif prefix == "user":
                return ("username", ident)
            elif prefix == "c":
                return ("custom", ident)
        # Fallback: last segment as custom/username
        fallback = parts[-1]
        if fallback:
            if fallback.startswith("UC") and re.match(r"^UC[\w-]{21}$", fallback):
                return ("id", fallback)
            return ("custom", fallback)

    # If raw string: if matches channel ID pattern, treat as ID
    if url_or_id.startswith("UC") and re.match(r"^UC[\w-]{21}$", url_or_id):
        return ("id", url_or_id)

    # Otherwise treat as raw (will attempt both username & search)
    return ("raw", url_or_id)


def parse_iso_duration_to_seconds(duration_iso: str) -> int:
    """
    Convert ISO 8601 duration (e.g. "PT1M23S", "PT45S") into total seconds.
    """
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_iso)
    if not match:
        return 0
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return hours * 3600 + minutes * 60 + seconds


def resolve_channel_id(api_key: str, mode: str, identifier: str) -> str:
    """
    Resolve a literal channel ID ("UC…") from:
      - mode == "id"       → return as-is
      - mode == "username" → call channels?forUsername=…
      - mode == "custom"   → search channels?q=…
      - mode == "raw"      → try username, else search
    Returns channel ID or None.
    """
    base = "https://www.googleapis.com/youtube/v3"

    if mode == "id":
        return identifier

    if mode == "username":
        url = f"{base}/channels?part=id&forUsername={identifier}&key={api_key}"
        r = requests.get(url)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0]["id"]
        mode = "custom"  # Fall back to search

    if mode in ("custom", "raw"):
        url = f"{base}/search?part=snippet&type=channel&q={identifier}&maxResults=1&key={api_key}"
        r = requests.get(url)
        if r.status_code == 200:
            items = r.json().get("items", [])
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
    r = requests.get(url)
    if r.status_code != 200:
        return None
    items = r.json().get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_videos_under_2_min(api_key: str, uploads_playlist_id: str, max_results: int = 40):
    """
    Paginate through the uploads playlist, fetch video details in batches, filter
    by duration < 120 seconds, until we collect up to max_results videos.
    Returns a list of dicts:
      { videoId, title, viewCount, likeCount, commentCount, engagementRate }
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

        r = requests.get(playlist_url, params=params)
        if r.status_code != 200:
            break
        data = r.json()
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
        rv = requests.get(videos_url, params=vid_params)
        if rv.status_code != 200:
            break
        vdata = rv.json().get("items", [])

        for vid in vdata:
            if len(collected) >= max_results:
                break
            dur_sec = parse_iso_duration_to_seconds(vid["contentDetails"]["duration"])
            if dur_sec < 120:
                snippet = vid["snippet"]
                stats = vid.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                like_count = int(stats.get("likeCount", 0))
                comment_count = int(stats.get("commentCount", 0))

                # Compute engagement rate: (likes + comments) / views * 100
                engagement_rate = 0.0
                if view_count > 0:
                    engagement_rate = (like_count + comment_count) / view_count * 100

                collected.append({
                    "videoId": vid["id"],
                    "title": snippet.get("title", "—"),
                    "viewCount": view_count,
                    "likeCount": like_count,
                    "commentCount": comment_count,
                    "engagementRate": engagement_rate,
                })

        next_token = data.get("nextPageToken")
        if not next_token:
            break

    return collected[:max_results]


# --------------------------------------------------
# 3. Input: Channel URL Field & Fetch Button (Top-Right)
# --------------------------------------------------
# Create two columns: left blank, right contains inputs
col_left, col_right = st.columns([3, 1])

with col_right:
    channel_input = st.text_input(
        label="Channel URL / ID / Username",
        placeholder="e.g. https://www.youtube.com/c/ChannelName"
    )
    fetch_button = st.button("Fetch Videos")

# Retrieve API key from Streamlit secrets
try:
    api_key = st.secrets["youtube_api_key"].strip()
except KeyError:
    api_key = None

if not api_key:
    st.error("🔒 Add `youtube_api_key` to Streamlit secrets and rerun.")
    st.stop()

# --------------------------------------------------
# 4. On Fetch: Resolve + Fetch + Display Table
# --------------------------------------------------
if fetch_button:
    if not channel_input.strip():
        st.error("Please enter a valid channel URL, ID, or username.")
    else:
        with st.spinner("Resolving Channel ID…"):
            mode, identifier = extract_channel_identifier(channel_input)
            channel_id = resolve_channel_id(api_key, mode, identifier)

        if not channel_id:
            st.error("❌ Could not resolve a valid Channel ID. Check your input.")
        else:
            with st.spinner("Fetching Uploads Playlist…"):
                uploads_pl_id = fetch_uploads_playlist_id(api_key, channel_id)

            if not uploads_pl_id:
                st.error("❌ Unable to find uploads playlist for this channel.")
            else:
                with st.spinner("Scanning for recent videos under 2 minutes…"):
                    videos = fetch_videos_under_2_min(api_key, uploads_pl_id, max_results=40)

                if not videos:
                    st.warning("No videos under 2 minutes found for this channel.")
                else:
                    st.success(f"Found {len(videos)} videos under 2 minutes.")

                    # Build a Markdown table
                    header = (
                        "| Video Title | Views | Likes | Comments | Engagement Rate | Link |\n"
                        "|:----------- | ----: | ----: | -------: | --------------: | :--- |\n"
                    )
                    rows = []
                    for vid in videos:
                        # Escape any pipe characters in title
                        safe_title = vid["title"].replace("|", "\\|")
                        views = f"{vid['viewCount']:,}"
                        likes = f"{vid['likeCount']:,}"
                        comments = f"{vid['commentCount']:,}"
                        engagement = f"{vid['engagementRate']:.2f}%"
                        url = f"https://www.youtube.com/watch?v={vid['videoId']}"
                        title_md = f"[{safe_title}]({url})"
                        link_md = f"[Watch]({url})"

                        row = f"| {title_md} | {views} | {likes} | {comments} | {engagement} | {link_md} |"
                        rows.append(row)

                    table_md = header + "\n".join(rows)
                    st.markdown(f'<div class="scrollable-table">{table_md}</div>', unsafe_allow_html=True)
