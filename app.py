import requests
import gradio as gr
import os
import time
from datetime import datetime
import json
import traceback
from functools import lru_cache
from typing import Dict, List, Tuple, Any, Optional, Set
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
USERNAME = os.getenv("GITHUB_USERNAME")
TOKEN = os.getenv("GITHUB_TOKEN")

# Debug startup without exposing token
print(f"🚀 Starting GitHub Unfollower Pro at {datetime.now()}")
print(f"📝 USERNAME configured: {'✅' if USERNAME else '❌'}")
print(f"🔑 TOKEN configured: {'✅' if TOKEN and len(TOKEN) > 10 else '❌'}")

# API Configuration
BASE_URL = "https://api.github.com"
headers = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

# Cache configuration
CACHE_TIMEOUT = 300  # Cache timeout in seconds
cache_timestamp = {
    "following": 0,
    "followers": 0
}
cache_data = {
    "following": [],
    "followers": []
}

# Rate limiting configuration
rate_limit_remaining = 5000
rate_limit_reset = 0
MIN_RATE_LIMIT_THRESHOLD = 100  # Minimum remaining requests before implementing longer delays

class GitHubAPIError(Exception):
    """Custom exception for GitHub API errors"""
    def __init__(self, status_code, message, url):
        self.status_code = status_code
        self.message = message
        self.url = url
        super().__init__(f"GitHub API Error: {status_code} - {message} (URL: {url})")

class RateLimitExceededError(GitHubAPIError):
    """Exception for rate limit exceeded errors"""
    def __init__(self, reset_time, url):
        self.reset_time = reset_time
        super().__init__(429, "Rate limit exceeded", url)

def log_api_call(method: str, url: str, status_code: Optional[int] = None, error: Optional[str] = None) -> None:
    """Log API calls for debugging without exposing sensitive information"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    # Remove token from URL for logging
    safe_url = url.replace(TOKEN, "***TOKEN***") if TOKEN else url
    
    if error:
        print(f"❌ [{timestamp}] {method} {safe_url} - ERROR: {error}")
    else:
        print(f"✅ [{timestamp}] {method} {safe_url} - Status: {status_code}")

def update_rate_limit_info(response: requests.Response) -> None:
    """Update rate limit information from response headers"""
    global rate_limit_remaining, rate_limit_reset
    
    if 'X-RateLimit-Remaining' in response.headers:
        rate_limit_remaining = int(response.headers['X-RateLimit-Remaining'])
    
    if 'X-RateLimit-Reset' in response.headers:
        rate_limit_reset = int(response.headers['X-RateLimit-Reset'])
    
    # Log rate limit status
    print(f"📊 Rate limit status: {rate_limit_remaining} requests remaining, resets at {datetime.fromtimestamp(rate_limit_reset).strftime('%H:%M:%S')}")

def calculate_adaptive_delay() -> float:
    """Calculate adaptive delay based on rate limit status"""
    if rate_limit_remaining <= MIN_RATE_LIMIT_THRESHOLD:
        # If we're close to the limit, use a longer delay
        return 2.0
    elif rate_limit_remaining <= 1000:
        return 1.0
    else:
        return 0.2  # Default delay for normal operation

def make_api_request(method: str, url: str, params: Dict = None) -> requests.Response:
    """Make an API request with rate limiting and error handling"""
    print(f"[DEBUG] make_api_request called: method={method}, url={url}")
    # Apply adaptive delay based on rate limit status
    delay = calculate_adaptive_delay()
    time.sleep(delay)
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == 'DELETE':
            response = requests.delete(url, headers=headers)
        elif method.upper() == 'PUT':
            response = requests.put(url, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        # Log the API call
        log_api_call(method, url, response.status_code)
        
        # Update rate limit information
        update_rate_limit_info(response)
        
        # Handle rate limiting
        if response.status_code == 429:
            reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
            raise RateLimitExceededError(reset_time, url)
        
        # Handle other errors
        if response.status_code >= 400:
            # For DELETE unfollow, let the caller handle 404
            if method.upper() == 'DELETE' and response.status_code == 404:
                return response
            error_message = response.json().get('message', 'Unknown error') if response.text else 'No response body'
            raise GitHubAPIError(response.status_code, error_message, url)
        
        return response
        
    except requests.exceptions.RequestException as e:
        log_api_call(method, url, error=str(e))
        raise GitHubAPIError(0, str(e), url)

def get_paginated(url: str, params: Dict = None) -> List[Dict]:
    """Enhanced pagination with rate limiting and caching"""
    print(f"📄 Starting paginated request for: {url}")
    results = []
    page_count = 0
    
    # Initialize params if None
    if params is None:
        params = {}
    
    # Add per_page parameter for efficiency
    params['per_page'] = 100
    
    current_url = url
    
    while current_url:
        try:
            page_count += 1
            print(f"📖 Fetching page {page_count}: {current_url}")
            
            response = make_api_request('GET', current_url, params if page_count == 1 else None)
            
            if response.status_code != 200:
                print(f"⚠️ Unexpected status code: {response.status_code}")
                print(f"📄 Response headers: {dict(response.headers)}")
                # Log only part of the response body to avoid exposing sensitive data
                print(f"📄 Response body preview: {response.text[:200]}...")
                response.raise_for_status()
            
            data = response.json()
            results.extend(data)
            
            # Check for next page
            next_url = response.links.get('next', {}).get('url')
            print(f"🔗 Next page URL: {next_url if next_url else 'None (last page)'}")
            current_url = next_url
            
            print(f"📊 Page {page_count}: Got {len(data)} items, Total so far: {len(results)}")
            
        except RateLimitExceededError as e:
            wait_time = e.reset_time - time.time() + 5  # Add 5 seconds buffer
            print(f"⚠️ Rate limit exceeded. Waiting for {wait_time:.1f} seconds until reset...")
            time.sleep(max(1, wait_time))  # Ensure we wait at least 1 second
            continue  # Retry the same request
            
        except GitHubAPIError as e:
            print(f"❌ GitHub API error on page {page_count}: {e}")
            raise
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error on page {page_count}: {str(e)}")
            print(f"📄 Raw response preview: {response.text[:200]}...")
            raise
            
        except Exception as e:
            print(f"❌ Unexpected error on page {page_count}: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            raise
    
    print(f"✅ Pagination complete: {page_count} pages, {len(results)} total items")
    return results

def get_following(force_refresh: bool = False) -> List[str]:
    """Get following list with caching"""
    global cache_data, cache_timestamp
    
    current_time = time.time()
    
    # Return cached data if available and not expired
    if not force_refresh and cache_data["following"] and (current_time - cache_timestamp["following"] < CACHE_TIMEOUT):
        print(f"📋 Using cached following list ({len(cache_data['following'])} users, cached {int(current_time - cache_timestamp['following'])}s ago)")
        return cache_data["following"]
    
    print(f"👥 Getting following list for user: {USERNAME}")
    try:
        url = f"{BASE_URL}/user/following"
        following_data = get_paginated(url)
        following_list = [user["login"].strip() for user in following_data]  # Preserve case
        
        # Update cache
        cache_data["following"] = following_list
        cache_timestamp["following"] = current_time
        
        print(f"✅ Following list retrieved: {len(following_list)} users")
        return following_list
    except Exception as e:
        print(f"❌ Error getting following list: {str(e)}")
        print(f"🔍 Full traceback: {traceback.format_exc()}")
        # Return cached data if available, even if expired
        if cache_data["following"]:
            print(f"⚠️ Using expired cached data due to error")
            return cache_data["following"]
        raise

def get_followers(force_refresh: bool = False) -> List[str]:
    """Get followers list with caching"""
    global cache_data, cache_timestamp
    
    current_time = time.time()
    
    # Return cached data if available and not expired
    if not force_refresh and cache_data["followers"] and (current_time - cache_timestamp["followers"] < CACHE_TIMEOUT):
        print(f"📋 Using cached followers list ({len(cache_data['followers'])} users, cached {int(current_time - cache_timestamp['followers'])}s ago)")
        return cache_data["followers"]
    
    print(f"👥 Getting followers list for user: {USERNAME}")
    try:
        url = f"{BASE_URL}/user/followers"
        followers_data = get_paginated(url)
        followers_list = [user["login"].strip() for user in followers_data]  # Preserve case
        
        # Update cache
        cache_data["followers"] = followers_list
        cache_timestamp["followers"] = current_time
        
        print(f"✅ Followers list retrieved: {len(followers_list)} users")
        return followers_list
    except Exception as e:
        print(f"❌ Error getting followers list: {str(e)}")
        print(f"🔍 Full traceback: {traceback.format_exc()}")
        # Return cached data if available, even if expired
        if cache_data["followers"]:
            print(f"⚠️ Using expired cached data due to error")
            return cache_data["followers"]
        raise

def get_user_info(username: str) -> Optional[Dict]:
    """Get detailed user information with improved error handling"""
    print(f"👤 Getting user info for: {username}")
    try:
        url = f"{BASE_URL}/users/{username}"
        response = make_api_request('GET', url)
        
        if response.status_code == 200:
            user_data = response.json()
            print(f"✅ User info retrieved for {username}: {user_data.get('name', 'No name')}")
            return user_data
        else:
            print(f"⚠️ User info not found for {username}: Status {response.status_code}")
            return None
    except GitHubAPIError as e:
        print(f"❌ GitHub API error getting user info for {username}: {e}")
        return None
    except Exception as e:
        print(f"❌ Error getting user info for {username}: {str(e)}")
        return None

def unfollow_user(username: str) -> bool:
    """Unfollow a user, always attempt the API call, and treat 204/404 as success."""
    print(f"[DEBUG] unfollow_user called: username={username}")
    username = username.strip()  # Do not lowercase
    if not username:
        print("⚠️ Skipping empty username")
        return False

    unfollow_url = f"{BASE_URL}/user/following/{username}"
    try:
        response = make_api_request('DELETE', unfollow_url)
        print(f"DEBUG: DELETE {unfollow_url} -> {response.status_code} {response.text}")

        if response.status_code == 204:
            print(f"✅ Successfully unfollowed {username}")
            return True
        elif response.status_code == 404:
            print(f"⚠️ {username} was already unfollowed or does not exist (404). Treating as success.")
            return True
        else:
            print(f"❌ Failed to unfollow {username}: {response.status_code} {response.text}")
            return False
    except GitHubAPIError as e:
        # Only log as error if not 404
        if e.status_code == 404:
            print(f"⚠️ {username} was already unfollowed or does not exist (404). Treating as success.")
            return True
        print(f"❌ GitHub API error unfollowing {username}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error unfollowing {username}: {str(e)}")
        return False

def follow_user(username: str) -> bool:
    """Follow a user with improved error handling"""
    username = username.lower().strip()
    print(f"👋 Attempting to follow: {username}")
    
    try:
        # Check if already following without fetching the entire list
        if username in get_following():
            print(f"✅ Already following {username}. Skipping follow.")
            return True

        url = f"{BASE_URL}/user/following/{username}"
        response = make_api_request('PUT', url)

        if response.status_code == 204:
            print(f"✅ Successfully followed: {username}")
            # Invalidate following cache
            cache_timestamp["following"] = 0
            return True
        else:
            print(f"❌ Failed to follow {username}: Status {response.status_code}")
            return False
    except GitHubAPIError as e:
        print(f"❌ GitHub API error following {username}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error following {username}: {str(e)}")
        return False

def get_account_stats(force_refresh: bool = False) -> Tuple[Dict, List[str], List[str], List[str]]:
    """Get comprehensive account statistics with caching and improved error handling"""
    print(f"📊 Starting account statistics calculation...")
    try:
        print("🔄 Step 1: Getting following list...")
        following = get_following(force_refresh)

        print("🔄 Step 2: Getting followers list...")
        followers = get_followers(force_refresh)

        print("🔄 Step 3: Calculating relationships...")
        following_set = set(following)
        followers_set = set(followers)

        mutuals = following_set & followers_set
        non_mutuals = following_set - followers_set
        not_following_back = followers_set - following_set

        print(f"📈 Relationship stats:")
        print(f"   - Following: {len(following)}")
        print(f"   - Followers: {len(followers)}")
        print(f"   - Mutuals: {len(mutuals)}")
        print(f"   - Non-mutuals: {len(non_mutuals)}")
        print(f"   - Not following back: {len(not_following_back)}")

        print("🔄 Step 4: Getting user profile info...")
        user_info = get_user_info(USERNAME)

        stats = {
            "total_following": len(following),
            "total_followers": len(followers),
            "mutuals": len(mutuals),
            "non_mutuals": len(non_mutuals),
            "not_following_back": len(not_following_back),
            "profile_info": user_info,
            "rate_limit": {
                "remaining": rate_limit_remaining,
                "reset_time": datetime.fromtimestamp(rate_limit_reset).strftime('%H:%M:%S')
            }
        }

        print("✅ Account statistics calculation complete!")
        return stats, list(non_mutuals), list(not_following_back), list(mutuals)

    except Exception as e:
        print(f"❌ Error calculating account stats: {str(e)}")
        print(f"🔍 Full traceback: {traceback.format_exc()}")
        return {"error": str(e)}, [], [], []

def format_stats_display(stats: Dict) -> str:
    """Format statistics for display with error handling"""
    print("🎨 Formatting stats for display...")
    try:
        if "error" in stats:
            error_msg = f"❌ Error loading stats: {stats['error']}"
            print(f"⚠️ Returning error message: {error_msg}")
            return error_msg
        
        profile = stats.get("profile_info", {})
        name = profile.get("name", USERNAME) if profile else USERNAME
        bio = profile.get("bio", "No bio available") if profile else "No bio available"
        public_repos = profile.get("public_repos", 0) if profile else 0
        
        print(f"📝 Profile data: name={name}, bio_length={len(bio)}, repos={public_repos}")
        
        # Calculate follow-back ratio safely
        follow_back_ratio = (stats['total_followers']/max(stats['total_following'], 1)*100)
        
        result = f"""
## 📊 Account Overview
**Name:** {name}  
**Username:** @{USERNAME}  
**Bio:** {bio}  
**Public Repositories:** {public_repos}

### 📈 Following Statistics
- **Total Following:** {stats['total_following']} users
- **Total Followers:** {stats['total_followers']} users
- **Mutual Connections:** {stats['mutuals']} users
- **Non-Mutual Following:** {stats['non_mutuals']} users (you follow, they don't follow back)
- **Potential New Followers:** {stats['not_following_back']} users (they follow you, you don't follow back)

### 📋 Recommendations
- Consider unfollowing {stats['non_mutuals']} non-mutual connections to clean up your feed
- You could follow back {stats['not_following_back']} users who are following you
- Your follow-back ratio is {follow_back_ratio:.1f}%

### 🔄 API Status
- Remaining requests: {stats.get('rate_limit', {}).get('remaining', 'Unknown')}
- Reset time: {stats.get('rate_limit', {}).get('reset_time', 'Unknown')}
"""
        
        print("✅ Stats formatting complete!")
        return result
        
    except Exception as e:
        print(f"❌ Error formatting stats: {str(e)}")
        print(f"🔍 Full traceback: {traceback.format_exc()}")
        return f"❌ Error formatting display: {str(e)}"

def dry_run_analysis(force_refresh: bool = False) -> str:
    """Comprehensive dry run with detailed analysis and logging"""
    print("🔍 Starting dry run analysis...")
    try:
        stats, non_mutuals, not_following_back, mutuals = get_account_stats(force_refresh)

        if "error" in stats:
            error_msg = f"❌ Error: {stats['error']}"
            print(f"⚠️ Dry run failed: {error_msg}")
            return error_msg

        print("🎨 Formatting analysis display...")
        analysis = format_stats_display(stats)

        if non_mutuals:
            print(f"📝 Adding non-mutuals list ({len(non_mutuals)} users)...")
            analysis += f"\n## 🔍 Users You Follow (But They Don't Follow Back)\n"
            for i, user in enumerate(non_mutuals[:20], 1):  # Show first 20
                analysis += f"{i}. @{user}\n"
            if len(non_mutuals) > 20:
                analysis += f"... and {len(non_mutuals) - 20} more users\n"

        print("✅ Dry run analysis complete!")
        return analysis

    except Exception as e:
        print(f"❌ Error in dry run analysis: {str(e)}")
        print(f"🔍 Full traceback: {traceback.format_exc()}")
        return f"❌ Analysis failed: {str(e)}"

def execute_selective_unfollow(unfollow_count: int) -> str:
    """Unfollow a specific number of non-mutual users with improved efficiency"""
    try:
        unfollow_count = int(unfollow_count)
        stats, non_mutuals, _, _ = get_account_stats()

        if "error" in stats:
            return f"❌ Error: {stats['error']}"

        if not non_mutuals:
            return "✅ Great! Everyone you follow also follows you back!"

        # No need to fetch following list again, we already have non_mutuals
        users_to_unfollow = non_mutuals[:unfollow_count]

        results = []
        successful_unfollows = 0

        for i, user in enumerate(users_to_unfollow, 1):
            print(f"🔄 [{i}/{len(users_to_unfollow)}] Processing user: {user}")
            success = unfollow_user(user)
            if success:
                successful_unfollows += 1
                results.append(f"✅ Unfollowed @{user}")
            else:
                results.append(f"❌ Failed to unfollow @{user}")
            
            # Use adaptive delay
            delay = calculate_adaptive_delay()
            time.sleep(delay)

        # Force refresh following list after bulk operation
        if successful_unfollows > 0:
            new_following = get_following(force_refresh=True)
            print("First 10 users you are now following:", new_following[:10])

        summary = f"🎯 Unfollowed {successful_unfollows}/{len(users_to_unfollow)} users\n\n"
        return summary + "\n".join(results)

    except ValueError:
        return "❌ Please enter a valid number"
    except Exception as e:
        return f"❌ Error: {str(e)}"

def execute_full_unfollow() -> str:
    """Unfollow all non-mutual users with progress and detailed logging"""
    print("\U0001f525 Starting full unfollow operation...")
    try:
        print("\U0001f4c8 Getting account stats for full unfollow...")
        stats, non_mutuals, _, _ = get_account_stats()

        if "error" in stats:
            error_msg = f"\u274c Error: {stats['error']}"
            print(f"\u26a0\ufe0f Full unfollow failed: {error_msg}")
            return error_msg

        if not non_mutuals:
            success_msg = "\u2705 Great! Everyone you follow also follows you back!"
            print(f"\U0001f389 {success_msg}")
            return success_msg

        # No need to fetch following list again
        filtered_non_mutuals = non_mutuals

        print(f"\U0001f4dd Found {len(filtered_non_mutuals)} non-mutual users to unfollow")
        print(f"\U0001f6a8 WARNING: About to unfollow {len(filtered_non_mutuals)} users!")

        results = []
        successful_unfollows = 0
        batch_size = 10  # Process in batches to provide updates

        for i, user in enumerate(filtered_non_mutuals, 1):
            print(f"\U0001f504 [{i}/{len(filtered_non_mutuals)}] Processing user: {user}")
            success = unfollow_user(user)
            if success:
                successful_unfollows += 1
                results.append(f"\u2705 [{i}/{len(filtered_non_mutuals)}] Unfollowed @{user}")
            else:
                results.append(f"\u274c [{i}/{len(filtered_non_mutuals)}] Failed to unfollow @{user}")

            # Progress reporting for batches
            if i % batch_size == 0 or i == len(filtered_non_mutuals):
                print(f"\U0001f4c8 Progress: {i}/{len(filtered_non_mutuals)} processed ({successful_unfollows} successful)")
                
                # Check rate limit status and adjust delay if needed
                if rate_limit_remaining < MIN_RATE_LIMIT_THRESHOLD:
                    wait_time = min(30, rate_limit_reset - time.time())
                    if wait_time > 0:
                        print(f"\u23f3 Rate limit low ({rate_limit_remaining}). Pausing for {wait_time:.1f} seconds...")
                        time.sleep(wait_time)

            # Use adaptive delay
            delay = calculate_adaptive_delay()
            time.sleep(delay)

        # Force refresh following list after bulk operation
        if successful_unfollows > 0:
            new_following = get_following(force_refresh=True)
            print("First 10 users you are now following:", new_following[:10])

        summary = f"\U0001f525 Mass Unfollow Complete!\n"
        summary += f"Successfully unfollowed {successful_unfollows}/{len(filtered_non_mutuals)} users\n\n"
        final_result = summary + "\n".join(results)

        print(f"\u2705 Full unfollow complete: {successful_unfollows}/{len(filtered_non_mutuals)} successful")
        return final_result

    except Exception as e:
        error_msg = f"\u274c Error: {str(e)}"
        print(f"\u274c Unexpected error in full unfollow: {str(e)}")
        print(f"\U0001f50d Full traceback: {traceback.format_exc()}")
        return error_msg

def follow_back_suggestions() -> str:
    """Show users who follow you but you don't follow back with logging"""
    print("\U0001f465 Getting follow-back suggestions...")
    try:
        print("\U0001f4ca Getting account stats for follow-back suggestions...")
        stats, _, not_following_back, _ = get_account_stats()

        if "error" in stats:
            error_msg = f"\u274c Error: {stats['error']}"
            print(f"\u26a0\ufe0f Follow-back suggestions failed: {error_msg}")
            return error_msg

        if not not_following_back:
            success_msg = "\u2705 You're already following everyone who follows you!"
            print(f"\U0001f389 {success_msg}")
            return success_msg

        print(f"\U0001f4dd Found {len(not_following_back)} follow-back opportunities")

        result = f"\U0001f465 {len(not_following_back)} users follow you but you don't follow them back:\n\n"

        display_count = min(30, len(not_following_back))
        for i, user in enumerate(not_following_back[:display_count], 1):
            result += f"{i}. @{user}\n"

        if len(not_following_back) > 30:
            result += f"... and {len(not_following_back) - 30} more users\n"

        result += f"\n💡 Consider following some of these users to build mutual connections!"

        print(f"\u2705 Follow-back suggestions complete: showing {display_count}/{len(not_following_back)} users")
        return result

    except Exception as e:
        error_msg = f"\u274c Error: {str(e)}"
        print(f"\u274c Unexpected error in follow-back suggestions: {str(e)}")
        print(f"\U0001f50d Full traceback: {traceback.format_exc()}")
        return error_msg

def follow_selected_users(usernames: str) -> str:
    """Follow a list of users provided as a comma-separated string"""
    if not usernames or usernames.strip() == "":
        return "❌ No usernames provided"
    
    user_list = [u.strip() for u in usernames.split(',') if u.strip()]
    
    if not user_list:
        return "❌ No valid usernames found"
    
    results = []
    successful_follows = 0
    
    for i, username in enumerate(user_list, 1):
        print(f"🔄 [{i}/{len(user_list)}] Processing user: {username}")
        success = follow_user(username)
        
        if success:
            successful_follows += 1
            results.append(f"✅ Followed @{username}")
        else:
            results.append(f"❌ Failed to follow @{username}")
        
        # Use adaptive delay
        delay = calculate_adaptive_delay()
        time.sleep(delay)
    
    # Force refresh following list after bulk operation
    if successful_follows > 0:
        new_following = get_following(force_refresh=True)
        print("First 10 users you are now following:", new_following[:10])
    
    summary = f"👥 Follow Operation Complete\n"
    summary += f"Successfully followed {successful_follows}/{len(user_list)} users\n\n"
    
    return summary + "\n".join(results)

# Enhanced Gradio Interface
print("🎨 Initializing Gradio interface...")
with gr.Blocks(
    title="GitHub Unfollower Pro",
    theme=gr.themes.Soft(),
    css="""
    @import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap');

    * {
        font-family: 'Press Start 2P', cursive !important;
    }

    .main-header {
        text-align: center;
        margin-bottom: 30px;
    }
    .stats-box {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 10px;
        margin: 10px 0;
    }
    .action-btn {
        margin: 5px;
        font-weight: bold;
    }
    .warning-box {
        background-color: #fffbe6 !important; /* light yellow for both themes */
        color: #222 !important; /* dark text for contrast */
        border: 2px solid #ffb300 !important; /* strong yellow/orange border */
        padding: 15px;
        border-radius: 5px;
        margin: 10px 0;
        font-weight: bold;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    """
) as demo:
    
    with gr.Column():
        gr.HTML("""
        <div class="main-header">
            <h1>🚀 GitHub Unfollower Pro</h1>
            <p>Advanced GitHub following management with smart analytics and bulk operations</p>
        </div>
        """)
        
        # Account Overview Section
        with gr.Tab("📊 Account Overview"):
            gr.Markdown("### Get comprehensive insights about your GitHub following patterns")
            
            with gr.Row():
                stats_btn = gr.Button("📈 Load Account Stats", variant="primary", size="lg")
                refresh_btn = gr.Button("🔄 Force Refresh Data", variant="secondary", size="lg")
            
            stats_output = gr.Markdown(value="Click 'Load Account Stats' to see your GitHub analytics")
            
            with gr.Row():
                rate_limit_info = gr.Markdown(value="API rate limit information will appear here")
            
        # Smart Unfollow Section  
        with gr.Tab("🎯 Smart Unfollow"):
            gr.Markdown("### Intelligently manage your following list")
            
            gr.HTML("""
            <div class="warning-box">
                <strong>⚠️ Important:</strong> Always run a dry run first to review who will be unfollowed.
                This action cannot be easily undone!
            </div>
            """)
            
            with gr.Row():
                dry_run_btn = gr.Button("🔍 Analyze Non-Mutuals", variant="secondary", size="lg")
                
            with gr.Row():
                with gr.Column(scale=2):
                    unfollow_count = gr.Number(
                        label="Number of users to unfollow",
                        value=10,
                        minimum=1,
                        maximum=100,
                        step=1
                    )
                with gr.Column(scale=1):
                    selective_unfollow_btn = gr.Button("🎯 Selective Unfollow", variant="primary")
                    
            with gr.Row():
                full_unfollow_btn = gr.Button("🔥 Unfollow All Non-Mutuals", variant="stop", size="lg")
            
            unfollow_output = gr.Textbox(
                label="Results",
                lines=15,
                max_lines=25,
                placeholder="Results will appear here..."
            )
            
        # Follow Back Suggestions
        with gr.Tab("👥 Follow Back"):
            gr.Markdown("### Discover users who follow you but you don't follow back")
            
            with gr.Row():
                follow_back_btn = gr.Button("🔍 Find Follow-Back Opportunities", variant="primary", size="lg")
                
            follow_back_output = gr.Markdown(value="Click the button above to see follow-back suggestions")
            
            gr.Markdown("### Follow Selected Users")
            with gr.Row():
                usernames_input = gr.Textbox(
                    label="Enter usernames to follow (comma-separated)",
                    placeholder="username1, username2, username3",
                    lines=2
                )
                follow_selected_btn = gr.Button("👥 Follow Selected Users", variant="primary")
                
            follow_selected_output = gr.Textbox(
                label="Follow Results",
                lines=10,
                placeholder="Results will appear here..."
            )
            
        # Advanced Settings
        with gr.Tab("⚙️ Settings & Info"):
            gr.Markdown("""
            ### 🛠️ Tool Information
            
            **Rate Limiting:** This tool automatically adapts to GitHub's API rate limits with intelligent delays.
            
            **Safety Features:**
            - Caching system to reduce API calls (5 minute cache timeout)
            - Adaptive rate limiting based on remaining API quota
            - Dry run mode to preview actions
            - Selective unfollowing with custom counts  
            - Progress tracking for bulk operations
            - Enhanced error handling and status reporting
            
            **Tips for Best Results:**
            1. Always start with account overview to understand your following patterns
            2. Use dry run to review who will be affected
            3. Start with selective unfollowing (10-20 users) rather than mass operations
            4. Consider following back engaged users who follow you
            
            **GitHub API Limits:**
            - 5000 requests per hour for authenticated users
            - This tool uses ~2-4 requests per operation
            - Adaptive delays prevent rate limit errors
            
            ### 🔐 Required Environment Variables:
            - `GITHUB_USERNAME`: Your GitHub username
            - `GITHUB_TOKEN`: Personal access token with 'user:follow' permissions
            
            ### 🐞 Debug Information:
            - Check the server console/logs for detailed error messages
            - All API calls and errors are logged with timestamps
            - Failed operations include specific error details
            """)
            
            with gr.Row():
                cache_status = gr.Markdown(value="Cache status will appear here")
                
            with gr.Row():
                clear_cache_btn = gr.Button("🧹 Clear Cache", variant="secondary")
                cache_clear_result = gr.Markdown(value="")
    
    # Enhanced Event handlers with logging
    def stats_handler():
        print("🔘 Stats button clicked")
        try:
            stats, _, _, _ = get_account_stats()
            result = format_stats_display(stats)
            
            # Update rate limit info
            rate_info = f"""
            ### 🔄 API Rate Limit Status
            - **Remaining Requests:** {rate_limit_remaining} / 5000
            - **Reset Time:** {datetime.fromtimestamp(rate_limit_reset).strftime('%H:%M:%S')}
            - **Adaptive Delay:** {calculate_adaptive_delay():.1f} seconds
            """
            
            print("✅ Stats handler completed successfully")
            return result, rate_info
        except Exception as e:
            print(f"❌ Error in stats handler: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            return f"❌ Error loading stats: {str(e)}", "❌ Rate limit information unavailable"
    
    def refresh_stats_handler():
        print("🔘 Force refresh button clicked")
        try:
            stats, _, _, _ = get_account_stats(force_refresh=True)
            result = format_stats_display(stats)
            
            # Update rate limit info
            rate_info = f"""
            ### 🔄 API Rate Limit Status
            - **Remaining Requests:** {rate_limit_remaining} / 5000
            - **Reset Time:** {datetime.fromtimestamp(rate_limit_reset).strftime('%H:%M:%S')}
            - **Adaptive Delay:** {calculate_adaptive_delay():.1f} seconds
            """
            
            print("✅ Force refresh completed successfully")
            return result, rate_info
        except Exception as e:
            print(f"❌ Error in force refresh: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            return f"❌ Error loading stats: {str(e)}", "❌ Rate limit information unavailable"
    
    def dry_run_handler():
        print("🔘 Dry run button clicked")
        try:
            result = dry_run_analysis()
            print("✅ Dry run handler completed successfully")
            return result
        except Exception as e:
            print(f"❌ Error in dry run handler: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            return f"❌ Error in dry run: {str(e)}"
    
    def selective_unfollow_handler(count):
        print(f"🔘 Selective unfollow button clicked with count: {count}")
        try:
            result = execute_selective_unfollow(count)
            print("✅ Selective unfollow handler completed successfully")
            return result
        except Exception as e:
            print(f"❌ Error in selective unfollow handler: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            return f"❌ Error in selective unfollow: {str(e)}"
    
    def full_unfollow_handler():
        print("🔘 Full unfollow button clicked")
        try:
            result = execute_full_unfollow()
            print("✅ Full unfollow handler completed successfully")
            return result
        except Exception as e:
            print(f"❌ Error in full unfollow handler: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            return f"❌ Error in full unfollow: {str(e)}"
    
    def follow_back_handler():
        print("🔘 Follow back button clicked")
        try:
            result = follow_back_suggestions()
            print("✅ Follow back handler completed successfully")
            return result
        except Exception as e:
            print(f"❌ Error in follow back handler: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            return f"❌ Error in follow back: {str(e)}"
    
    def follow_selected_handler(usernames):
        print("🔘 Follow selected users button clicked")
        try:
            result = follow_selected_users(usernames)
            print("✅ Follow selected handler completed successfully")
            return result
        except Exception as e:
            print(f"❌ Error in follow selected handler: {str(e)}")
            print(f"🔍 Full traceback: {traceback.format_exc()}")
            return f"❌ Error following users: {str(e)}"
    
    def get_cache_status():
        current_time = time.time()
        following_age = current_time - cache_timestamp["following"]
        followers_age = current_time - cache_timestamp["followers"]
        
        following_status = (
            f"✅ Following: {len(cache_data['following'])} users (cached {int(following_age)}s ago)" 
            if cache_data["following"] else "❌ Following: Not cached"
        )
        
        followers_status = (
            f"✅ Followers: {len(cache_data['followers'])} users (cached {int(followers_age)}s ago)" 
            if cache_data["followers"] else "❌ Followers: Not cached"
        )
        
        return f"""
        ### 📋 Cache Status
        
        {following_status}
        
        {followers_status}
        
        Cache timeout: {CACHE_TIMEOUT} seconds
        """
    
    def clear_cache_handler():
        print("🔘 Clear cache button clicked")
        global cache_data, cache_timestamp
        
        # Clear cache
        cache_data = {
            "following": [],
            "followers": []
        }
        cache_timestamp = {
            "following": 0,
            "followers": 0
        }
        
        print("✅ Cache cleared successfully")
        return "✅ Cache cleared successfully", get_cache_status()
    
    # Attach handlers
    stats_btn.click(stats_handler, outputs=[stats_output, rate_limit_info])
    refresh_btn.click(refresh_stats_handler, outputs=[stats_output, rate_limit_info])
    dry_run_btn.click(dry_run_handler, outputs=unfollow_output)
    selective_unfollow_btn.click(selective_unfollow_handler, inputs=unfollow_count, outputs=unfollow_output)
    full_unfollow_btn.click(full_unfollow_handler, outputs=unfollow_output)
    follow_back_btn.click(follow_back_handler, outputs=follow_back_output)
    follow_selected_btn.click(follow_selected_handler, inputs=usernames_input, outputs=follow_selected_output)
    clear_cache_btn.click(clear_cache_handler, outputs=[cache_clear_result, cache_status])
    
    # Initialize cache status
    demo.load(get_cache_status, outputs=cache_status)

print("🎨 Gradio interface setup complete!")

if __name__ == "__main__":
    print("🚀 Launching application...")
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True
    )
