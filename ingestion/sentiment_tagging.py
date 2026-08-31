"""
Reads posts from Supabase that haven't been tagged yet (sentiment is null),
classifies each one with Claude, and writes back:
  - is_relevant   (is this a genuine personal experience/opinion about our
                    topic, as opposed to an ad, career-advice content, or a
                    coincidental keyword match?)
  - sentiment     (positive / neutral / negative — only meaningful if relevant)
  - is_feature_request
  - theme_id      (matched to an existing theme, or a new one is created)

Run:
    pip install -r requirements.txt
    python sentiment_tagging.py
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Cheaper model, fine for a straightforward classification task like this.
# Bump to a stronger model later if you notice misclassifications.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
claude = Anthropic(api_key=ANTHROPIC_API_KEY)

CLASSIFY_PROMPT = """You are helping tag social media posts for a product research dashboard. The product matches startup cofounders, and separately matches job-seekers with employers.

Post to classify:
---
{content}
---

This post was pulled by a keyword search for persona "{persona}" / competitor "{competitor}", but keyword search often matches posts that aren't actually relevant (ads, career-advice content marketing, or coincidental keyword overlap).

Existing themes you can match this post to, if it clearly fits one:
{existing_themes}

Respond with ONLY a JSON object, no other text, in this exact shape:
{{
  "is_relevant": true or false,
  "sentiment": "positive" | "neutral" | "negative" | null,
  "is_feature_request": true or false,
  "theme_title": "short existing or new theme title, or null if not relevant",
  "category": "pain_point" | "feature_request" | "competitor_gap" | null
}}

Rules:
- is_relevant is false for: ads, career-coaching/content-marketing tips, generic listicles, posts that only coincidentally contain the keyword, OR neutral matchmaking/listing posts (e.g. "looking for a cofounder, DM me" or "we're hiring, apply here") that don't express any opinion, frustration, or experience about the process itself.
- is_relevant is true only for genuine personal experiences, opinions, or complaints from the post's author about what it's actually like to search for a cofounder, search for a job, or hire — not just posts that happen to be doing that activity.
- If is_relevant is false, set sentiment, theme_title, and category to null.
- If an existing theme clearly fits, reuse its exact title. Otherwise write a short new one (under 10 words).
"""


def get_existing_themes() -> List[Dict]:
    result = supabase.table("themes").select("id,title,category,persona").execute()
    return result.data


def get_untagged_posts() -> List[Dict]:
    result = (
        supabase.table("posts")
        .select("id,content,persona,competitor_mentioned")
        .is_("sentiment", "null")
        .execute()
    )
    return result.data


def classify_post(post: Dict, existing_themes: List[Dict]) -> Optional[Dict]:
    theme_list = "\n".join(f"- {t['title']}" for t in existing_themes) or "(none yet)"

    prompt = CLASSIFY_PROMPT.format(
        content=post["content"],
        persona=post.get("persona") or "unspecified",
        competitor=post.get("competitor_mentioned") or "none",
        existing_themes=theme_list,
    )

    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text.strip()
    raw_text = raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        print(f"Could not parse response for post {post['id']}: {raw_text[:200]}")
        return None


def resolve_theme_id(theme_title: str, category: str, persona: Optional[str], existing_themes: List[Dict]) -> str:
    for theme in existing_themes:
        if theme["title"].strip().lower() == theme_title.strip().lower():
            return theme["id"]

    # No match — create a new theme.
    inserted = (
        supabase.table("themes")
        .insert({"title": theme_title, "category": category, "persona": persona})
        .execute()
    )
    new_theme = inserted.data[0]
    existing_themes.append(new_theme)
    return new_theme["id"]


def main():
    existing_themes = get_existing_themes()
    posts = get_untagged_posts()
    print(f"Found {len(posts)} untagged posts.")

    for post in posts:
        classification = classify_post(post, existing_themes)
        if classification is None:
            continue

        update = {"is_relevant": classification["is_relevant"]}

        if classification["is_relevant"]:
            update["sentiment"] = classification["sentiment"]
            update["is_feature_request"] = classification["is_feature_request"]
            update["theme_id"] = resolve_theme_id(
                classification["theme_title"],
                classification["category"],
                post.get("persona"),
                existing_themes,
            )
        else:
            update["sentiment"] = None
            update["is_feature_request"] = False
            update["theme_id"] = None

        supabase.table("posts").update(update).eq("id", post["id"]).execute()
        print(f"Tagged post {post['id']}: relevant={update['is_relevant']}")

        # Small delay to stay well within rate limits.
        time.sleep(0.5)

    print("Done tagging.")


if __name__ == "__main__":
    main()