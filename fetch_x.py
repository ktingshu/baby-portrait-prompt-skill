#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X 博主推文批量抓取脚本（免费 · 无需 API Key）

用你 X 账号的登录 Cookie（auth_token + ct0）抓取 followed.txt 中所有博主的
【最新推文】，合并输出 data.json，供聚合页 index.html 展示。

登录凭据从环境变量读取（不在代码/仓库中明文保存）：
    X_AUTH_TOKEN  —— X 账号 auth_token Cookie 值
    X_CT0         —— X 账号 ct0 Cookie 值
在 GitHub Actions 中通过 Secrets 注入这两个环境变量。

用法：
    python fetch_x.py [--top N] [--delay SEC]
      --top    每个博主保留的推文条数（按时间最新在前），默认 20
      --delay  每个博主之间的等待秒数，默认 2

输出：
    data.json —— 供 index.html 渲染的聚合数据
"""

import argparse
import datetime
import json
import os
import sys
import time

import httpx

# ── 路径 ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLLOWED_FILE = os.path.join(BASE_DIR, "followed.txt")
OUTPUT_FILE = os.path.join(BASE_DIR, "data.json")
CONFIG_FILE = os.path.join(BASE_DIR, ".x_config.json")

# ── 登录凭据（环境变量）───────────────────────────────────────────────────────
AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("X_CT0", "").strip()
HAS_AUTH = bool(AUTH_TOKEN and CT0)


# ── 运行时配置（从 .x_config.json 或 twikit 提取）──────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("bearer") and cfg.get("gql"):
            return cfg
    try:
        from twikit.constants import TOKEN
        from twikit.client.gql import Endpoint
        cfg = {
            "bearer": TOKEN,
            "proxy": os.environ.get("X_PROXY", ""),
            "gql": {
                "UserByScreenName": Endpoint.USER_BY_SCREEN_NAME.rsplit("/i/api/graphql/", 1)[-1],
                "UserTweets": Endpoint.USER_TWEETS.rsplit("/i/api/graphql/", 1)[-1],
            },
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return cfg
    except Exception as e:
        print(f"错误: 无法获取运行时配置（{e}）", file=sys.stderr)
        sys.exit(1)


CFG = load_config()
BEARER = CFG["bearer"]
GQL = CFG["gql"]
PROXY = CFG.get("proxy", "")

ANDROID_UA = (
    "TwitterAndroid/10.21.0-release.0 (310210000-r-0) ONEPLUS+A3010/9 "
    "(OnePlus;ONEPLUS+A3010;OnePlus;OnePlus3;0;;1;2016)"
)

USER_FEATURES = {
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

TWEET_FEATURES = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


# ── HTTP 客户端 ────────────────────────────────────────────────────────────────
def make_client():
    if PROXY:
        transport = httpx.HTTPTransport(proxy=PROXY)
        return httpx.Client(transport=transport, timeout=25, follow_redirects=True)
    return httpx.Client(timeout=25, follow_redirects=True)


def request_headers(guest_token=None):
    headers = {
        "Authorization": f"Bearer {BEARER}",
        "User-Agent": ANDROID_UA,
        "x-twitter-client-language": "zh-cn",
        "x-twitter-active-user": "yes",
        "Content-Type": "application/json",
    }
    if guest_token:
        headers["x-guest-token"] = guest_token
    if HAS_AUTH:
        headers["Cookie"] = f"auth_token={AUTH_TOKEN}; ct0={CT0}"
        headers["x-csrf-token"] = CT0
    return headers


def get_guest_token(client):
    r = client.post(
        "https://api.twitter.com/1.1/guest/activate.json",
        headers={"Authorization": f"Bearer {BEARER}", "User-Agent": ANDROID_UA},
    )
    r.raise_for_status()
    return r.json()["guest_token"]


def gql_get(client, endpoint_key, variables, features, guest_token=None):
    path = GQL[endpoint_key]
    r = client.get(
        f"https://x.com/i/api/graphql/{path}",
        params={"variables": json.dumps(variables), "features": json.dumps(features)},
        headers=request_headers(guest_token),
    )
    r.raise_for_status()
    return r.json()


# ── 解析 ───────────────────────────────────────────────────────────────────────
def extract_tweet_text(tweet_result):
    legacy = tweet_result.get("legacy", {})
    note = tweet_result.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
    if note:
        return note.get("text", legacy.get("full_text", ""))
    return legacy.get("full_text", "")


def parse_tweet(entry):
    try:
        content = entry.get("content", {})
        # 兼容 TimelineTimelineModule 分组：推文嵌套在 item.itemContent 里
        item_wrapper = entry.get("item") if isinstance(entry, dict) else None
        if isinstance(item_wrapper, dict) and "itemContent" in item_wrapper:
            item_content = item_wrapper.get("itemContent", {})
        else:
            item_content = content.get("itemContent", content)
        tweet_result = item_content.get("tweet_results", {}).get("result", {})
        if not tweet_result or tweet_result.get("__typename") == "TweetUnavailable":
            return None
        legacy = tweet_result.get("legacy", {})
        if not legacy:
            return None
        return {
            "id": legacy.get("id_str"),
            "created_at": legacy.get("created_at"),
            "text": extract_tweet_text(tweet_result),
            "likes": legacy.get("favorite_count", 0),
            "retweets": legacy.get("retweet_count", 0),
            "replies": legacy.get("reply_count", 0),
            "views": tweet_result.get("views", {}).get("count", "?"),
            "is_retweet": "retweeted_status_result" in legacy,
        }
    except Exception:
        return None


def flatten_timeline(data):
    tweets = []
    try:
        instructions = data["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"]
        for inst in instructions:
            for entry in inst.get("entries", []):
                t = parse_tweet(entry)
                if t:
                    tweets.append(t)
                for item in entry.get("content", {}).get("items", []):
                    t = parse_tweet(item)
                    if t:
                        tweets.append(t)
    except (KeyError, TypeError):
        pass
    return tweets


def tweet_time_key(t):
    try:
        # "Wed Nov 13 03:55:15 +0000 2024"
        return datetime.datetime.strptime(t.get("created_at", ""), "%a %b %d %H:%M:%S +0000 %Y").timestamp()
    except Exception:
        return 0


# ── 抓取单个博主 ───────────────────────────────────────────────────────────────
def fetch_account(client, handle, guest_token, top_n):
    """抓取一个博主的资料 + 最新推文。成功返回 dict，失败返回 None。"""
    try:
        user_data = gql_get(
            client, "UserByScreenName",
            {"screen_name": handle, "withSafetyModeUserFields": True},
            USER_FEATURES, guest_token,
        )
        user_result = user_data.get("data", {}).get("user", {}).get("result", {})
        if not user_result:
            print(f"  ⚠️ @{handle}: 未找到该用户", file=sys.stderr)
            return None
        legacy = user_result.get("legacy", {})
        user_id = user_result.get("rest_id", "")

        data = gql_get(
            client, "UserTweets", {
                "userId": user_id,
                "count": 40,
                "includePromotedContent": False,
                "withQuickPromoteEligibilityTweetFields": True,
                "withVoice": True,
                "withV2Timeline": True,
            }, TWEET_FEATURES, guest_token,
        )
        tweets = flatten_timeline(data)

        # 去重 + 按时间最新在前 + 取前 N 条
        seen, unique = set(), []
        for t in tweets:
            if t and t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                unique.append(t)
        unique.sort(key=tweet_time_key, reverse=True)
        tweets_top = unique[:top_n]
        for t in tweets_top:
            t["url"] = f"https://x.com/{handle}/status/{t['id']}"

        return {
            "handle": handle,
            "name": legacy.get("name", handle),
            "bio": legacy.get("description", ""),
            "followers": legacy.get("followers_count", 0),
            "following": legacy.get("friends_count", 0),
            "profile_url": f"https://x.com/{handle}",
            "tweets": tweets_top,
        }
    except httpx.HTTPStatusError as e:
        print(f"  ⚠️ @{handle}: HTTP {e.response.status_code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ⚠️ @{handle}: {e}", file=sys.stderr)
        return None


# ── 读取名单 ───────────────────────────────────────────────────────────────────
def read_followed():
    accounts = []
    with open(FOLLOWED_FILE, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            handle = parts[0].lstrip("@")
            name = parts[1] if len(parts) > 1 else handle
            accounts.append((handle, name))
    return accounts


# ── 主流程 ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="X 博主推文批量抓取")
    parser.add_argument("--top", type=int, default=20, help="每博主保留推文数（默认20）")
    parser.add_argument("--delay", type=float, default=2.0, help="博主间等待秒数（默认2）")
    args = parser.parse_args()

    if not HAS_AUTH:
        print("⚠️ 未设置 X_AUTH_TOKEN / X_CT0 环境变量，将使用未登录模式（仅能抓取部分博主）", file=sys.stderr)
    else:
        print("✅ 已启用登录模式（auth_token + ct0）")

    accounts = read_followed()
    print(f"共 {len(accounts)} 个博主，开始抓取（每博主取最新 {args.top} 条）...")

    results = []
    with make_client() as client:
        gt = None
        if not HAS_AUTH:
            gt = get_guest_token(client)
            print("✅ 已获取 guest token（未登录模式）")
        for i, (handle, disp_name) in enumerate(accounts, 1):
            print(f"[{i}/{len(accounts)}] 抓取 @{handle} ({disp_name}) ...")
            acc = fetch_account(client, handle, gt, args.top)
            if acc:
                acc["disp_name"] = disp_name
                results.append(acc)
            if i < len(accounts):
                time.sleep(args.delay)

    results.sort(key=lambda a: a.get("followers", 0), reverse=True)

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "total_accounts": len(results),
        "accounts": results,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    total_tweets = sum(len(a["tweets"]) for a in results)
    print(f"\n✅ 完成：{len(results)}/{len(accounts)} 个博主 / {total_tweets} 条推文 → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
