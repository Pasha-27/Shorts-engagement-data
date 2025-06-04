import streamlit as st
import math
import json
import isodate
from googleapiclient.discovery import build

#working commit at 5:19 AM

# Set page configuration for dark mode
st.set_page_config(
    page_title="YouTube God's eye Dashboard",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply custom CSS for dark mode
st.markdown("""
<style>
    /* Dark mode colors */
    .stApp {
        background-color: #0e1117;
        color: #fafafa;
    }
    
    /* Card styling */
    div.stButton > button {
        background-color: #4c6ef5;
        color: white;
        border: none;
        border-radius: 5px;
        padding: 10px 24px;
        font-weight: bold;
        width: 100%;
    }
    
    div.stButton > button:hover {
        background-color: #364fc7;
    }
    
    /* Fix background for sidebar */
    [data-testid=stSidebar] {
        background-color: #1e2538;
    }
    
    /* Metric container */
    .metric-container {
        background-color: #1e2538;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    
    /* Remove padding from containers */
    div.block-container {
        padding-top: 1rem;
    }

    /* Card container style */
    .video-card {
        background-color: #1e2538;
        border-radius: 10px;
        padding: 1rem;
        margin-bottom: 0.3rem;
    }
    .video-thumbnail {
        width: 100%;
        border-radius: 5px;
        margin-bottom: 0.5rem;
    }
    .video-title {
        color: #fafafa;
        margin-bottom: 0.5rem;
        font-weight: bold;
        font-size: 1.1rem;
    }
    .video-meta {
        color: #fafafa;
        margin-bottom: 0.2rem;
        font-size: 0.9rem;
    }
    .outlier-pill {
        color: white;
        border-radius: 12px;
        padding: 5px 10px;
        font-weight: bold;
    }
    .video-link {
        color: #4c6ef5;
        text-decoration: none;
        font-weight: bold;
    }
    .comment-card {
        background-color: #13192a;
        border-radius: 8px;
        padding: 0.7rem;
        margin-bottom: 0.5rem;
    }
    .comment-author {
        color: #cbd5e0;
        font-weight: bold;
        margin-bottom: 0.2rem;
        font-size: 0.9rem;
    }
    .comment-text {
        color: #e2e8f0;
        font-size: 0.85rem;
        margin-bottom: 0.3rem;
    }
    .comment-likes {
        color: #a0aec0;
        font-size: 0.8rem;
    }
    
    /* Style Streamlit's expander to match our theme */
    .streamlit-expanderHeader {
        background-color: #1e2538 !important;
        color: #4c6ef5 !important;
        font-weight: bold !important;
    }
    .streamlit-expanderContent {
        background-color: #1e2538 !important;
        border: none !important;
    }
</style>
""", unsafe_allow_html=True)

def parse_duration(duration_str):
    """Parse ISO 8601 duration to seconds."""
    try:
        duration = isodate.parse_duration(duration_str)
        return duration.total_seconds()
    except Exception:
        return 0

def format_number(num):
    """Format large numbers for display."""
    if num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num/1_000:.1f}K"
    else:
        return str(num)

def format_duration(seconds):
    """Format duration in seconds to a more readable H/M/S string."""
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    elif minutes > 0:
        return f"{int(minutes)}m {int(seconds)}s"
    else:
        return f"{int(seconds)}s"

def get_outlier_color(multiplier):
    """Determine the color for the outlier multiplier based on VidIQ's brackets."""
    if multiplier < 2:
        return "black"
    elif multiplier < 5:
        return "#4c6ef5"  # blue
    elif multiplier < 10:
        return "purple"
    else:
        return "red"

def load_channels(file_path="channels.json"):
    """
    Loads channel data from a JSON file.
    Expected JSON format:
    {
      "channels": [
        {"id": "CHANNEL_ID", "name": "Channel Name"},
        ...
      ]
    }
    """
    with open(file_path, "r") as f:
        data = json.load(f)
    return data.get("channels", [])

def fetch_top_comments(youtube, video_id, video_title, max_comments=5):
    """
    Fetches the top comments for a video by like count.
    Returns a list of the top comments with author, text, and like count.
    Logs the number of comments fetched with video title for identification.
    """
    try:
        # Fetch comments
        comment_response = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            order="relevance",  # Relevance includes like count in sorting
            maxResults=20  # Fetch more and then sort ourselves to ensure we get most liked
        ).execute()
        
        comments = []
        for item in comment_response.get("items", []):
            comment = item["snippet"]["topLevelComment"]["snippet"]
            
            # Get the plain text version of the comment (remove HTML)
            text_display = comment["textDisplay"]
            
            # Create a safe version of the text by escaping HTML
            import html
            text_safe = html.escape(text_display)
            
            # Clean up any remaining HTML or entities
            import re
            text_safe = re.sub(r'<[^>]*>', '', text_safe)
            text_safe = text_safe.replace('&nbsp;', ' ')
            
            comments.append({
                "author": html.escape(comment["authorDisplayName"]),
                "text": text_display,
                "text_safe": text_safe,
                "like_count": comment.get("likeCount", 0),
                "published_at": comment["publishedAt"][:10]
            })
        
        # Sort by like count and get top max_comments
        comments = sorted(comments, key=lambda x: x["like_count"], reverse=True)[:max_comments]
        
        # Log the number of comments fetched with video title for identification
        truncated_title = video_title[:50] + "..." if len(video_title) > 50 else video_title
        st.write(f"📊 Fetched {len(comments)} comments for video: '{truncated_title}'")
        
        return comments
    except Exception as e:
        # Comments might be disabled for the video
        st.error(f"Error fetching comments for '{video_title[:30]}...': {str(e)}")
        return []

def build_video_card(col, video, channel_avg_views, show_comments=True):
    """Builds a video card with Streamlit components instead of HTML."""
    color = get_outlier_color(video['outlier_multiplier'])
    avg_views = channel_avg_views.get(video["channel_id"], 0)
    
    with col:
        st.markdown(f"""
        <div class="video-card">
            <img src="{video['thumbnail']}" class="video-thumbnail" />
            <div class="video-title">{video['title']}</div>
            <div class="video-meta">{video['channel']} • {video['published_at']}</div>
            <div class="video-meta">
                <strong>Views:</strong> {format_number(video['view_count'])} |
                <strong>Duration:</strong> {format_duration(video['duration'])}
            </div>
            <div class="video-meta">
                <strong>Outlier Score:</strong> <span class='outlier-pill' style='background-color:{color};'>{video['outlier_multiplier']:.1f}x</span> |
                <strong>Channel Avg:</strong> {format_number(int(avg_views))}
            </div>
            <div class="video-meta">
                <strong>Engagement Rate:</strong> {video['engagement_rate']:.1f}%
            </div>
            <div class="video-meta">
                <a href="{video['url']}" class="video-link" target="_blank">Watch Video</a>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Add comments section using Streamlit native components
        if show_comments:
            if video.get('top_comments'):
                with st.expander(f"📝 Top Comments ({len(video['top_comments'])})"):
                    for comment in video['top_comments']:
                        st.markdown(f"""
                        <div class="comment-card">
                            <div class="comment-author">{comment['author']}</div>
                            <div class="comment-text">{comment['text_safe']}</div>
                            <div class="comment-likes">❤️ {format_number(comment['like_count'])} likes • {comment['published_at']}</div>
                        </div>
                        """, unsafe_allow_html=True)
            else:
                st.markdown('<div class="comments-meta">No comments available</div>', unsafe_allow_html=True)

def main():
    # We can't use custom JavaScript in Streamlit
    # So instead we'll use Streamlit's built-in components for toggling
    
    # Sidebar for inputs
    with st.sidebar:
        st.title("YouTube Search")
        st.write("")
        st.markdown("SEARCH KEYWORD")
        keyword = st.text_input("", placeholder="Enter keyword...", label_visibility="collapsed")
        st.write("")
        st.markdown("VIDEO TYPE")
        video_type = st.selectbox("", options=["All", "Short (< 3 mins)", "Long (>= 3 mins)"], label_visibility="collapsed")
        st.write("")
        st.markdown("SORT BY")
        sort_option = st.selectbox("", options=["Outlier Score", "View Count"], label_visibility="collapsed")
        st.write("")
        st.markdown("MINIMUM OUTLIER MULTIPLIER")
        min_outlier_multiplier = st.slider("", min_value=0.0, max_value=20.0, value=2.0, step=0.1, label_visibility="collapsed")
        st.write("")
        
        # Load channels from channels.json to let user select
        channels_list = load_channels("channels.json")
        channel_names = [ch["name"] for ch in channels_list]
        select_all = st.checkbox("Select All Channels", value=True)
        if select_all:
            selected_channel_names = channel_names
        else:
            selected_channel_names = st.multiselect("Select Channels", options=channel_names)
        
        show_comments = st.checkbox("Show Top Comments", value=True)
        
        search_button = st.button("Search Videos")
    
    st.title("YouTube God's eye Dashboard")
    
    if search_button:
        if not keyword:
            st.sidebar.error("Please enter a search keyword.")
            return
        
        if not selected_channel_names:
            st.sidebar.error("Please select at least one channel.")
            return
        
        search_info = st.empty()
        search_info.info("Searching for videos in selected Finance channels...")
        
        try:
            API_KEY = st.secrets["YOUTUBE_API_KEY"]
            youtube = build("youtube", "v3", developerKey=API_KEY)
            
            # Filter channels based on user selection
            selected_channels = [ch for ch in channels_list if ch["name"] in selected_channel_names]
            if not selected_channels:
                search_info.error("No channels selected. Please update your selection.")
                return
            
            # Compute each selected channel's average views (total_views / total_videos)
            channel_avg_views = {}
            for ch in selected_channels:
                channel_id = ch["id"]
                channel_response = youtube.channels().list(
                    part="statistics",
                    id=channel_id
                ).execute()
                if channel_response.get("items"):
                    stats = channel_response["items"][0]["statistics"]
                    total_views = int(stats.get("viewCount", 0))
                    total_videos = int(stats.get("videoCount", 0))
                    avg_views = total_views / total_videos if total_videos > 0 else 0
                    channel_avg_views[channel_id] = avg_views
                else:
                    channel_avg_views[channel_id] = 0
            
            all_video_ids = []
            # Search for videos in each selected channel
            for ch in selected_channels:
                channel_id = ch["id"]
                search_response = youtube.search().list(
                    part="snippet",
                    q=keyword,
                    channelId=channel_id,
                    type="video",
                    maxResults=50
                ).execute()
                items = search_response.get("items", [])
                all_video_ids.extend([item["id"]["videoId"] for item in items])
            
            if not all_video_ids:
                search_info.error("No videos found across the selected channels.")
                return
            
            # Retrieve video details in chunks of 50
            details_responses = []
            def chunk_list(lst, size=50):
                for i in range(0, len(lst), size):
                    yield lst[i: i + size]
            for chunk in chunk_list(all_video_ids, 50):
                details_response = youtube.videos().list(
                    part="contentDetails,statistics,snippet",
                    id=",".join(chunk)
                ).execute()
                details_responses.append(details_response)
            
            detail_items = []
            for resp in details_responses:
                detail_items.extend(resp.get("items", []))
            
            results = []
            keyword_lower = keyword.lower()
            for item in detail_items:
                video_id = item["id"]
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                contentDetails = item.get("contentDetails", {})
                
                title = snippet.get("title", "")
                description = snippet.get("description", "")
                tags = snippet.get("tags", [])
                channel_title = snippet.get("channelTitle", "")
                channel_id = snippet.get("channelId", "")
                thumbnail = snippet.get("thumbnails", {}).get("high", {}).get("url", "")
                published_at = snippet.get("publishedAt", "")
                
                # Check for keyword in title, description, or tags (case-insensitive)
                if (keyword_lower not in title.lower() and
                    keyword_lower not in description.lower() and
                    not any(keyword_lower in tag.lower() for tag in tags)):
                    continue
                
                view_count = int(statistics.get("viewCount", 0))
                like_count = int(statistics.get("likeCount", 0)) if "likeCount" in statistics else 0
                comment_count = int(statistics.get("commentCount", 0)) if "commentCount" in statistics else 0
                
                # Calculate engagement rate (percentage)
                if view_count > 0:
                    engagement_rate = ((like_count + comment_count) / view_count) * 100
                else:
                    engagement_rate = 0.0
                
                # New outlier score calculation: 
                # (Video view count * (1 + (engagement_rate/100))) / Channel average views
                if channel_id in channel_avg_views and channel_avg_views[channel_id] > 0:
                    multiplier = (view_count * (1 + (engagement_rate / 100))) / channel_avg_views[channel_id]
                else:
                    multiplier = 0
                
                if multiplier <= min_outlier_multiplier:
                    continue
                
                duration_str = contentDetails.get("duration", "PT0S")
                duration_seconds = parse_duration(duration_str)
                if video_type == "Short (< 3 mins)" and duration_seconds >= 180:
                    continue
                if video_type == "Long (>= 3 mins)" and duration_seconds < 180:
                    continue
                
                results.append({
                    "video_id": video_id,
                    "title": title,
                    "channel": channel_title,
                    "channel_id": channel_id,
                    "description": description,  # Not displayed in the card
                    "view_count": view_count,
                    "duration": duration_seconds,
                    "outlier_multiplier": multiplier,
                    "thumbnail": thumbnail,
                    "published_at": published_at[:10],
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "engagement_rate": engagement_rate
                })
            
            if sort_option == "View Count":
                results = sorted(results, key=lambda x: x["view_count"], reverse=True)
            else:
                results = sorted(results, key=lambda x: x["outlier_multiplier"], reverse=True)
            
            results = results[:10]
            
            # Fetch top comments for videos if requested
            if show_comments:
                st.write("### Fetching comments for search results")
                
                # Create a container for logs
                comment_log_container = st.empty()
                with comment_log_container.container():
                    progress_bar = st.progress(0)
                    for i, video in enumerate(results):
                        search_info.info(f"Fetching comments for video {i+1} of {len(results)}...")
                        # Now correctly passing the video title as the third argument
                        top_comments = fetch_top_comments(youtube, video["video_id"], video["title"])
                        video["top_comments"] = top_comments
                        progress_bar.progress((i+1)/len(results))
                    
                # Option to hide logs after loading
                if st.button("Clear Comment Logs"):
                    comment_log_container.empty()
                
                progress_bar.empty()
            
            search_info.empty()
            
            if not results:
                st.error("No videos match the criteria.")
            else:
                st.header("Search Results")
                st.markdown('<div class="metric-container">', unsafe_allow_html=True)
                st.markdown("VIDEOS FOUND")
                st.markdown(f"<h2>{len(results)}</h2>", unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
                
                # Display results in a 3-column layout with a small gap
                for i in range(0, len(results), 3):
                    columns = st.columns(3, gap="small")
                    for j in range(3):
                        if i + j < len(results):
                            video = results[i + j]
                            build_video_card(columns[j], video, channel_avg_views, show_comments)
        
        except Exception as e:
            st.error(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()
