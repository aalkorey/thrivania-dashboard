"""
One-time cleanup script. Finds posts still tagged with the old generic
persona = 'job-seekers', asks Claude to decide whether each one is really
about early-talent (new grad/entry-level/internship) or non-early-talent
(experienced/senior) job-seeking, updates the persona field, then resets
sentiment/is_relevant/theme_id/is_feature_request to null so the next run
of sentiment_tagging.py rebuilds fresh, correctly-grouped themes for them.

Run once:
    python reclassify_job_seeker_persona.py

Then run:
    python sentiment_tagging.py
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List

from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
claude = Anthropic(api_key=ANTHROPIC_API_KEY)

RECLASSIFY_PROMPT = """Read this social media post and decide whether the author sounds like an early-talent job-seeker or a non-early-talent (experienced) job-seeker.

Post:
---
{content}
---

early-talent = new grad, student, intern, first job, entry-level, no/little experience.
non-early-talent = has prior work experience, career changer, mid-career, senior, experienced professional.

If there's no clear signal either way, default to non-early-talent (it's the more common case).

Respond with ONLY one of these two exact strings, nothing else:
early-talent-job-seekers
non-early-talent-job-seekers
"""


def get_old_job_seeker_posts() -> List[Dict]:
    result = (
        supabase.table("posts")
        .select("id,content")
        .eq("persona", "job-seekers")
        .execute()
    )
    return result.data


def classify_persona(content: str) -> str:
    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=20,
        messages=[{"role": "user", "content": RECLASSIFY_PROMPT.format(content=content)}],
    )
    result = response.content[0].text.strip()

    if result not in ("early-talent-job-seekers", "non-early-talent-job-seekers"):
        print(f"Unexpected response '{result}', defaulting to non-early-talent-job-seekers")
        return "non-early-talent-job-seekers"

    return result


def main():
    posts = get_old_job_seeker_posts()
    print(f"Found {len(posts)} posts tagged 'job-seekers' to reclassify.")

    for post in posts:
        new_persona = classify_persona(post["content"])

        supabase.table("posts").update({
            "persona": new_persona,
            # Reset tagging so sentiment_tagging.py rebuilds themes fresh
            # under the correct new persona.
            "sentiment": None,
            "is_relevant": None,
            "theme_id": None,
            "is_feature_request": False,
        }).eq("id", post["id"]).execute()

        print(f"Post {post['id']}: job-seekers -> {new_persona}")
        time.sleep(0.5)

    print("Done reclassifying. Now run sentiment_tagging.py to re-tag these posts.")


if __name__ == "__main__":
    main()