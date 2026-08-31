"""
Pulls recent posts from X's search API for a fixed set of queries covering
the three personas (cofounder-seekers, job-seekers, employers) and the
three competitors (LinkedIn, Indeed, Glassdoor), then writes the raw
results into Supabase's `posts` table.

This script only PULLS AND STORES raw posts. Sentiment tagging and theme
assignment happen in a separate step (sentiment_tagging.py, not yet
built), so this script leaves `sentiment` and `theme_id` null on insert.

Run:
    pip install -r requirements.txt
    python x_pull.py
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv
from supabase import create_client

# .env lives at the project root, one level up from this ingestion/ folder.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

X_BEARER_TOKEN = os.environ["X_BEARER_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]

# Keep this small while testing — each result costs money. Raise it once
# you've confirmed the pipeline works end to end.
MAX_RESULTS_PER_QUERY = 10

# Each entry: the X search query, which persona it targets, and (if any)
# which competitor it's about. lang:en and -is:retweet keep results clean.
QUERIES = [
    {
        "query": '("looking for a cofounder" OR "finding a co-founder") lang:en -is:retweet -is:nullcast',
        "persona": "cofounder-seekers",
        "competitor": None,
    },
    {
        "query": '(cofounder OR "co-founder") (ghosted OR flaked OR "didn\'t commit") lang:en -is:retweet -is:nullcast',
        "persona": "cofounder-seekers",
        "competitor": None,
    },
    {
        "query": '("job search" OR "looking for a job") (frustrating OR ghosted OR ATS) lang:en -is:retweet -is:nullcast -toolkit -"#Ad" -"get yours"',
        "persona": "job-seekers",
        "competitor": None,
    },
    {
        "query": '(hiring OR recruiting) ("too many applicants" OR "unqualified applicants") lang:en -is:retweet -is:nullcast',
        "persona": "employers",
        "competitor": None,
    },
    {
        "query": 'linkedin (networking OR "job search" OR recruiter OR hiring OR connections) (spammy OR annoying OR "AI slop" OR fake OR broken OR frustrating) lang:en -is:retweet -is:nullcast -suspended -banned',
        "persona": None,
        "competitor": "linkedin",
    },
    {
        "query": "indeed.com (spam OR scam OR \"fake job\") lang:en -is:retweet -is:nullcast",
        "persona": None,
        "competitor": "indeed",
    },
    {
        "query": 'glassdoor ("fake review" OR "reviews are" OR biased OR manipulated OR "can\'t trust" OR untrustworthy) lang:en -is:retweet -is:nullcast -"space x" -tesla',
        "persona": None,
        "competitor": "glassdoor",
    },
]

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


def fetch_tweets(query: str, max_results: int) -> List[Dict]:
    response = requests.get(
        SEARCH_URL,
        headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
        params={
            "query": query,
            "max_results": max_results,
            "tweet.fields": "created_at,author_id",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def to_post_row(tweet: Dict, persona: Optional[str], competitor: Optional[str]) -> Dict:
    return {
        "source": "x",
        "external_id": tweet["id"],
        "url": f"https://x.com/i/web/status/{tweet['id']}",
        "author": tweet.get("author_id"),
        "content": tweet["text"],
        "posted_at": tweet["created_at"],
        "persona": persona,
        "competitor_mentioned": competitor,
        # Left null on purpose — filled in by the sentiment tagging step.
        "sentiment": None,
        "theme_id": None,
        "is_feature_request": False,
    }


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

    total_inserted = 0
    for entry in QUERIES:
        tweets = fetch_tweets(entry["query"], MAX_RESULTS_PER_QUERY)
        rows = [to_post_row(t, entry["persona"], entry["competitor"]) for t in tweets]

        if not rows:
            print(f"No results for query: {entry['query']}")
            continue

        # Upsert on (source, external_id) so re-running this script doesn't
        # create duplicate rows for posts you've already pulled.
        supabase.table("posts").upsert(rows, on_conflict="source,external_id").execute()
        total_inserted += len(rows)
        print(f"Inserted {len(rows)} posts for query: {entry['query']}")

    print(f"Done. {total_inserted} posts processed.")


if __name__ == "__main__":
    main()