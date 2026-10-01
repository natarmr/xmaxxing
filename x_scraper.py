import os
import re
import time
from playwright.sync_api import sync_playwright

# Negative patterns to discard irrelevant posts immediately
NEGATIVE_PATTERNS = [
    # Job seekers / Resumes / "Open to work"
    r"\bi\s+am\s+open\s+for\s+new\s+roles\b",
    r"\bi\s+am\s+open\s+to\b",
    r"\bopen\s+for\s+new\s+tech\s+roles\b",
    r"\bopen\s+to\s+(?:work|roles|opportunities)\b",
    r"\blooking\s+for\s+(?:a\s+)?(?:job|internship|role|opportunity)\b",
    r"\bactively\s+(?:seeking|looking)\b",
    r"\bmy\s+last\s+ppo\b",
    r"\blet\s+me\s+know\s+if\s+you\s+are\s+hiring\b",
    
    # False positives for "apply" / "applies"
    r"\bt&c\s+apply\b",
    r"\bterms\s+(?:and\s+conditions\s+)?apply\b",
    r"\bfees?\s+apply\b",
    r"\bcharges?\s+apply\b",
    r"\bapply\s+to\s+all\b",
    r"\bdoes\s+not\s+apply\b",
    r"\brule\s+.*?\s+apply\b",
    r"\brules\s+.*?\s+apply\b",
    
    # Engagement bait / DM scams / Comment to get guide
    r"\bcomment\s+['\"].*?['\"]\s+and\s+i(?:'ll|'m|’ll|’m|\s+will)\s+dm\b",
    r"\bcomment\s+['\"].*?['\"]\s+to\s+get\b",
    r"\breply\s+['\"].*?['\"]\s+and\s+i(?:'ll|'m|’ll|’m|\s+will)\s+dm\b",
    r"\bdm\s+me\s+the\s+word\b",
    r"\bdm\s+['\"]\w+['\"]\b",
    r"\breply\s+[\"'].*?[\"']\s+and\s+i\s+will\b",
    r"\bcomment\s+[\"'].*?[\"']\s+and\s+i\s+will\b",
    
    # Paid internships (scams/noise)
    r"\bpay\s+[\d,]+\s*(?:rs|inr|usd)?\s+to\s+do\s+the\s+internship\b",
    
    # Dialogue memes / joke formats
    r"\bintern:\s",
    r"\bjuniors?:\s",
    r"\bseniors?:\s",
    r"\bboss:\s",
    r"\bmanager:\s",
    r"\bdeveloper:\s",
    
    # Advice / Commentary / Tips (not actual job postings)
    r"\bhot\s+take\b",
    r"\bmy\s+first\s+remote\s+job\b",
    r"\bmy\s+first\s+job\b",
    r"\bhow\s+to\s+get\s+a\s+job\b",
    r"\badvice\s+to\b",
    r"\bproductivity\s+advice\b",
    r"\bmental\s+clutter\b",
    
    # News / Market / Lawsuits / Legal / Geopolitical
    r"\bclass\s+action\b",
    r"\bclass-action\b",
    r"\bsec\s+fines\b",
    r"\bgeopolitical\b",
    r"\bkremlin\b",
    r"\bputin\b",
    r"\braised\s+(?:around|nearly|over|\$)?\s*\d+\s*(?:million|m|billion|b)\b",
    r"\bvaluation\b",
    r"\bseed\s+round\b",
    r"\bseries\s+[a-f]\b",
    r"\bexit\s+liquidity\b",
    
    # Financial / Trading / Crypto / Stock market noise
    r"\boption\s+sweep\b",
    r"\bcalls?\s+at\s+the\s+ask\b",
    r"\bintraday\b",
    r"\btrading\s+competition\b",
    
    # Product promotion / pre-orders / features / giveaways / free trials
    r"\bpre-order\b",
    r"\bpre\s+order\b",
    r"\bhardware\s+wallet\b",
    r"\bgiveaway\b",
    r"\bgive-away\b",
    r"\bfree\s+trial\b",
    
    # Fellowship / Academic / UG admissions / Degree programs
    r"\bfellowship\b",
    r"\bug\s+program\b",
    r"\badmission\s+through\b",
    r"\bdegree\s+cutoff\b",
    r"\bcgpa\b",
]

def matches_keywords(text, has_links=False):
    text_lower = text.lower()
    
    # 1. Reject immediately if it matches negative patterns
    for neg in NEGATIVE_PATTERNS:
        if re.search(neg, text_lower):
            return False, f"negative: {neg}"
            
    # 2. Hackathon/Competition Matching
    IS_HACKATHON = False
    if re.search(r"\bhackathons?\b", text_lower):
        # Search for active hackathon verbs/nouns with tenses supported
        hack_triggers = [
            r"\bregister(?:ed|s|ing)?\b", 
            r"\bapply(?:ing|ied)?\b", 
            r"\blive\b", 
            r"\bjoin(?:ed|s|ing)?\b",
            r"\bparticipat(?:e|es|ed|ing|ion)\b", 
            r"\bcompet(?:e|es|ed|ing)\b", 
            r"\bsubmission(?:s)?\b",
            r"\bprize(?:s)?\b", 
            r"\bwinner(?:s)?\b", 
            r"\blaunch(?:es|ed|ing)?\b", 
            r"\bbuild(?:s|ing)?\b"
        ]
        is_discussion = re.search(r"\bdurations?\b|\bhistory\b|\bshrunk\b|\bdecade\b|\byears?\s+ago\b|\bcommentary\b", text_lower)
        if any(re.search(trig, text_lower) for trig in hack_triggers) and not is_discussion:
            IS_HACKATHON = True

    # 3. Job / Internship Matching
    IS_JOB_OR_INTERN = False
    
    # Check for direct hiring calls
    hiring_triggers = [
        r"\bwe're\s+hiring\b",
        r"\bwe\s+are\s+hiring\b",
        r"\bi'm\s+hiring\b",
        r"\bi\s+am\s+hiring\b",
        r"\bnow\s+hiring\b",
        r"\bhiring\s+(?:across|software|interns?|developers?|engineers?|designers?|roles|positions)\b",
        r"\bhiring\s+for\b",
        r"\bexpand\s+the\s+team\b",
        r"\blooking\s+for\s+(?:cracked|rockstar|senior|junior|fullstack|frontend|backend|devops|ml|ai)\s+(?:engineers?|developers?)\b",
        r"\b[a-za-z0-9_]+\s+is\s+hiring\b",
    ]
    
    # General discussion exceptions for hiring keyword matches
    is_general_hiring_discussion = re.search(
        r"\b(?:who|that|which|anyone|someone|if\s+you\s+are)\s+(?:is|are)\s+hiring\b", 
        text_lower
    )
    
    # Strong hiring triggers don't strictly require CTA if they aren't general discussions
    if any(re.search(trig, text_lower) for trig in hiring_triggers) and not is_general_hiring_discussion:
        # Avoid obvious joke roles
        if not re.search(r"\bslop\s+cannon\b|\brecursive\s+agent\s+builder\b", text_lower):
            IS_JOB_OR_INTERN = True
            
    # Regular job/internship posts (require CTA)
    if not IS_JOB_OR_INTERN:
        has_cta = has_links or re.search(r"\b(?:dm|pm|comment)\b", text_lower)
        if re.search(r"\binternships?\b|\bintern\b", text_lower) and not re.search(r"\bmemes?\b", text_lower):
            intern_triggers = [
                r"\bhiring\b", r"\bstipend\b", r"\bapply\b", r"\blooking\s+for\b",
                r"\bpositions?\b", r"\broles?\b"
            ]
            if any(re.search(trig, text_lower) for trig in intern_triggers) and has_cta:
                IS_JOB_OR_INTERN = True
                
    if IS_HACKATHON:
        return True, "hackathon_match"
    if IS_JOB_OR_INTERN:
        return True, "job_or_intern_match"
        
    return False, "no_strong_match"

def load_seen_tweets(filepath):
    seen = set()
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            # Extract status URLs like https://x.com/username/status/1234567890
            urls = re.findall(r'https://(?:x|twitter)\.com/\w+/status/\d+', content)
            seen.update(urls)
        print(f"Loaded {len(seen)} previously scraped tweets from {os.path.basename(filepath)}")
    return seen

def append_tweet_to_markdown(filepath, tweet_info):
    file_exists = os.path.exists(filepath)
    with open(filepath, "a", encoding="utf-8") as f:
        if not file_exists:
            f.write("# Scraped Jobs & Hackathons from X.com\n\n")
            f.write("This file is automatically updated by the scraper script.\n\n")
        
        f.write(f"## Post by {tweet_info['user']} ({tweet_info['scraped_at']})\n")
        f.write(f"- **Tweet Link:** {tweet_info['url']}\n")
        f.write("- **Content:**\n")
        # Format tweet text as a blockquote
        indented_text = "\n".join(f"  > {line}" for line in tweet_info['text'].split("\n"))
        f.write(f"{indented_text}\n")
        
        if tweet_info['links']:
            f.write("- **Extracted Links:**\n")
            for href, text in tweet_info['links']:
                # Clean up display text if it's identical to href or empty
                display_text = text if text and text != href else "Link"
                f.write(f"  - [{display_text}]({href})\n")
        else:
            f.write("- **Extracted Links:** None\n")
        
        f.write("\n---\n\n")
        f.flush()

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, "jobs_and_hackathons.md")
    user_data_dir = os.path.join(script_dir, "x_user_data")
    
    seen_tweets = load_seen_tweets(output_file)
    
    print("Starting Playwright...")
    with sync_playwright() as p:
        # Launch Chromium with a persistent context to store login sessions
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"] # Reduce bot detection
        )
        
        page = context.pages[0] if context.pages else context.new_page()
        
        print("Navigating to x.com/home...")
        page.goto("https://x.com/home")
        
        # Check login status
        try:
            # Look for the home navigation button as proof of login
            page.wait_for_selector('a[data-testid="AppTabBar_Home_Link"]', timeout=7000)
            print("Logged in successfully!")
        except Exception:
            print("\n" + "="*60)
            print("NOT LOGGED IN. Please log in manually in the browser window.")
            print("Waiting for you to log in...")
            print("="*60 + "\n")
            # Wait indefinitely until the home feed is loaded
            page.wait_for_selector('a[data-testid="AppTabBar_Home_Link"]', timeout=0)
            print("Login detected! Resuming...")

        print("Starting feed scroll and scraper. Press Ctrl+C in the terminal to stop.")
        
        consecutive_no_new_tweets = 0
        
        try:
            while True:
                # Find all tweet articles currently loaded in the DOM
                articles = page.locator('article[data-testid="tweet"]').all()
                new_tweets_found_this_scroll = False
                
                for article in articles:
                    try:
                        # 1. Get Tweet URL to use as unique identifier
                        tweet_url = None
                        time_el = article.locator('time')
                        if time_el.count() > 0:
                            parent_a = article.locator('a:has(time)')
                            if parent_a.count() > 0:
                                href = parent_a.first.get_attribute('href')
                                if href:
                                    tweet_url = f"https://x.com{href}" if href.startswith('/') else href
                        
                        if not tweet_url:
                            # Fallback: scan all links for status path
                            for a in article.locator('a').all():
                                href = a.get_attribute('href')
                                if href and '/status/' in href:
                                    tweet_url = f"https://x.com{href}" if href.startswith('/') else href
                                    break
                        
                        # Skip if we couldn't resolve a URL or if we already processed it
                        if not tweet_url or tweet_url in seen_tweets:
                            continue
                        
                        seen_tweets.add(tweet_url)
                        new_tweets_found_this_scroll = True
                        
                        # 2. Extract User Info
                        user_info = "Unknown User"
                        user_name_el = article.locator('[data-testid="User-Name"]')
                        if user_name_el.count() > 0:
                            user_info = user_name_el.first.inner_text().replace('\n', ' ')
                        
                        # 3. Extract Tweet Text
                        tweet_text = ""
                        tweet_text_el = article.locator('[data-testid="tweetText"]')
                        if tweet_text_el.count() > 0:
                            tweet_text = tweet_text_el.first.inner_text()
                        
                        # 4. Extract links first to assist in keyword scoring
                        links = []
                        # Look inside the text content for links
                        if tweet_text_el.count() > 0:
                            for a in tweet_text_el.first.locator('a').all():
                                href = a.get_attribute('href')
                                text = a.inner_text().strip()
                                if href:
                                    # Ignore hashtags and mentions (internal twitter links)
                                    if not (href.startswith('/hashtag/') or href.startswith('/search') or (href.startswith('/') and not '/status/' in href)):
                                        links.append((href, text))
                        
                        # Look for links in preview cards
                        card = article.locator('[data-testid="card.wrapper"]')
                        if card.count() > 0:
                            for a in card.locator('a').all():
                                href = a.get_attribute('href')
                                text = a.inner_text().strip()
                                if href:
                                    links.append((href, text or "Link Preview"))
                        
                        # Normalize and de-duplicate links
                        unique_links = []
                        seen_links = set()
                        for href, text in links:
                            full_href = f"https://x.com{href}" if href.startswith('/') else href
                            if full_href not in seen_links:
                                seen_links.add(full_href)
                                unique_links.append((full_href, text))
                        
                        # 5. Check keywords with scoring logic
                        is_match, reason = matches_keywords(tweet_text, has_links=bool(unique_links))
                        if is_match:
                            # Prepare data
                            tweet_info = {
                                "user": user_info,
                                "url": tweet_url,
                                "text": tweet_text,
                                "links": unique_links,
                                "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S")
                            }
                            
                            # Save
                            append_tweet_to_markdown(output_file, tweet_info)
                            print(f"[{time.strftime('%H:%M:%S')}] Saved post from {user_info.split(' @')[0]}")
                            
                    except Exception as e:
                        # Ignore errors on individual tweets (e.g. element went out of DOM during parsing)
                        continue
                
                # Scroll down to load more tweets
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(2500) # Wait for network requests/renders
                
                # Monitor progress
                if new_tweets_found_this_scroll:
                    consecutive_no_new_tweets = 0
                else:
                    consecutive_no_new_tweets += 1
                    # If we've scrolled several times without seeing any new tweets, do a larger scroll
                    if consecutive_no_new_tweets >= 5:
                        print("Feed scrolling seems slow or stuck. Doing a larger scroll step...")
                        page.evaluate("window.scrollBy(0, 2500)")
                        page.wait_for_timeout(4000)
                        consecutive_no_new_tweets = 0
        except KeyboardInterrupt:
            print("\nScraper stopped by user. Cleaning up...")
        finally:
            try:
                context.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
