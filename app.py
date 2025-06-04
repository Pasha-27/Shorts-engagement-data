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
    initial_sidebar_state="expanded"
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
    /* Sidebar background */
    .stSidebar {
        background-color: #121416;
        color: #e0e0e0;
    }
    /* Sidebar inputs styling */
    .stSidebar input, .stSidebar button {
        background-color: #1e2228;
        color: #e0e0e0;
        border: 1px solid #333740;
        border-radius: 5px;
    }
    /* Video card container */
    .video-card {
        background-color: #1e2228;
        border-radius: 10px;
        padding: 15px;
        margin-bottom: 20px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    }
    /* Headers inside video cards */
    .video-card h3 {
        margin-bottom: 10px;
        color: #ffffff;
    }
    /* Metrics labels */
    .stMetric-label {
        color: #ccd6f6;
    }
    /* Metrics values */
    .stMetric-value {
        color: #00f2c3;
    }
    /* Thumbnail images */
    .video-thumb {
        border-radius: 8px;
    }
    /* Expander styling */
    .stExpanderHeader {
        background-color: #1e2228 !important;
        color: #ffffff !important;
        border-radius: 8px;
        padding: 10px;
    }
    .stExpanderContent {
        background-color: #1b1f23 !important;
        padding: 10px;
        border-radius: 0 0 8px 8px;
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
    Given a channel URL or raw identifier, determine:
      - "id"       → Literal channel ID (starts with "UC✔")
      - "username" → YouTube username (for /user/…)
      - "custom"   → Custom URL handle (for /c/…)
      - "raw"      → Neither; treat as possible username/custom (will attempt search)
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
        # Fall back to custom search if username not found
        mode = "custom"

    if mode in ("custom", "raw"):
        # Try searching for a channel whose title or custom handle matches identifier
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
      { videoId, title, thumbnail_url, viewCount, likeCount, commentCount, engagementRate }
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

                thumbs = snippet.get("thumbnails", {})
                thumb_url = (
                    thumbs.get("high", {}).get("url")
                    or thumbs.get("medium", {}).get("url")
                    or thumbs.get("default", {}).get("url")
                    or ""
                )

                collected.append({
                    "videoId": vid["id"],
                    "title": snippet.get("title", "—"),
                    "thumbnail_url": thumb_url,
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
# 3. Sidebar: Inputs & Fetch Button
# --------------------------------------------------
st.sidebar.title("🔍 Channel Inputs")
st.sidebar.markdown(
    """
    Enter a **YouTube Channel URL** (or Channel ID or Username).  
    The app will fetch up to 40 of the most recent videos **under 2 minutes**,
    then display:
    - Title  
    - Thumbnail  
    - View count  
    - Like count  
    - Comment count  
    - Engagement rate (Likes + Comments) / Views  
    """
)

# Attempt to retrieve API key securely from secrets
try:
    api_key = st.secrets["youtube_api_key"].strip()
except Exception:
    api_key = None

if not api_key:
    st.sidebar.error("🔒 Add `youtube_api_key` to Streamlit secrets.")
    st.stop()

channel_input = st.sidebar.text_input("Channel URL or ID/Username")

fetch_button = st.sidebar.button("Fetch Recent <2 Min Videos", use_container_width=True)

# --------------------------------------------------
# 4. Main: Fetch & Display Video Cards
# --------------------------------------------------
if fetch_button:
    if not channel_input.strip():
        st.sidebar.error("Please enter a channel URL or identifier.")
    else:
        # Resolve channel ID
        with st.spinner("Resolving Channel ID…"):
            mode, identifier = extract_channel_identifier(channel_input)
            channel_id = resolve_channel_id(api_key, mode, identifier)

        if not channel_id:
            st.error("❌ Unable to resolve a valid Channel ID. Check your input.")
        else:
            # Fetch uploads playlist ID
            with st.spinner("Fetching Uploads Playlist…"):
                uploads_pl_id = fetch_uploads_playlist_id(api_key, channel_id)

            if not uploads_pl_id:
                st.error("❌ Could not find an uploads playlist for that channel.")
            else:
                # Fetch and filter videos
                with st.spinner("Scanning for recent videos < 2 minutes…"):
                    videos = fetch_videos_under_2_min(api_key, uploads_pl_id, max_results=40)

                if not videos:
                    st.warning("No videos under 2 minutes found for this channel.")
                else:
                    st.success(f"Found {len(videos)} videos under 2 minutes.")

                    # Display each video as a styled "card"
                    for vid in videos:
                        # Start of card container
                        st.markdown('<div class="video-card">', unsafe_allow_html=True)

                        # Video title as a header
                        st.markdown(f'<h3>🎥 {vid["title"]}</h3>', unsafe_allow_html=True)

                        # Layout: thumbnail + metrics side by side
                        cols = st.columns([1, 1, 1, 1, 1, 1], gap="small")

                        # Thumbnail
                        with cols[0]:
                            if vid["thumbnail_url"]:
                                st.image(vid["thumbnail_url"], use_column_width=True, caption="")
                            else:
                                st.write("No thumbnail available")

                        # Views
                        with cols[1]:
                            st.metric(label="👁️ Views", value=f"{vid['viewCount']:,}")

                        # Likes
                        with cols[2]:
                            st.metric(label="👍 Likes", value=f"{vid['likeCount']:,}")

                        # Comments
                        with cols[3]:
                            st.metric(label="💬 Comments", value=f"{vid['commentCount']:,}")

                        # Engagement Rate (formatted to 2 decimals + “%”)
                        with cols[4]:
                            er_str = f"{vid['engagementRate']:.2f}%"
                            st.metric(label="📈 Engagement Rate", value=er_str)

                        # Watch Link
                        with cols[5]:
                            watch_url = f"https://www.youtube.com/watch?v={vid['videoId']}"
                            st.markdown(f'<a href="{watch_url}" target="_blank">▶️ Watch Video</a>', unsafe_allow_html=True)

                        # End of card container
                        st.markdown('</div>', unsafe_allow_html=True)
