"""Pull recent US App Store reviews for top-grossing subscription apps per genre via Apple's public RSS feeds."""
import json, time, urllib.request, sys

GENRES = {6008: "Photo & Video", 6013: "Health & Fitness", 6017: "Education", 6007: "Productivity",
          6012: "Lifestyle", 6016: "Entertainment", 6011: "Music", 6002: "Utilities"}
# Platforms whose revenue is not in-app subscriptions to their own features (not Adapty's market)
EXCLUDE = ["YouTube", "Snapchat", "Instagram", "Twitch", "TikTok", "Google", "Facebook", "WhatsApp", "Netflix",
           "Disney", "Amazon", "Prime Video", "Max", "Hulu", "Peacock", "Paramount", "Roku", "Microsoft",
           "Spotify", "Apple", "Pandora", "SiriusXM", "iHeart", "Audible", "Kindle", "Fitbit", "Tubi", "Pluto",
           "Crunchyroll", "Fubo", "Sling", "DIRECTV", "Telegram", "Discord", "Uber", "Pinterest", "BIGO", "LiveMe",
           "Tango", "Bumble for Friends", "Cash App", "PayPal", "Venmo", "Bilibili"]
PAGES = 6  # 50 reviews per page -> up to 300 most recent reviews per app

def get(url):
    for i in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except Exception as e:
            time.sleep(2 * (i + 1))
    return None

apps = []
for gid, gname in GENRES.items():
    d = get(f"https://itunes.apple.com/us/rss/topgrossingapplications/limit=100/genre={gid}/json")
    entries = d["feed"]["entry"]
    ranked = [(i + 1, e) for i, e in enumerate(entries)
              if not any(x.lower() in (e["im:name"]["label"] + " " + e["im:artist"]["label"]).lower() for x in EXCLUDE)]
    top = ranked[:5]
    mid = [(r, e) for r, e in ranked if 41 <= r <= 60][:5]
    for tier, sel in (("top", top), ("mid", mid)):
        for rank, e in sel:
            apps.append({"genre": gname, "tier": tier, "rank": rank, "id": e["id"]["attributes"]["im:id"],
                         "name": e["im:name"]["label"], "publisher": e["im:artist"]["label"]})
    time.sleep(0.5)

json.dump(apps, open("apps.json", "w"), indent=1, ensure_ascii=False)
print(len(apps), "apps", file=sys.stderr)

out = open("reviews.jsonl", "w")
n = 0
for a in apps:
    for p in range(1, PAGES + 1):
        d = get(f"https://itunes.apple.com/us/rss/customerreviews/page={p}/id={a['id']}/sortby=mostrecent/json")
        ent = (d or {}).get("feed", {}).get("entry", [])
        if isinstance(ent, dict):
            ent = [ent]
        ent = [e for e in ent if "im:rating" in e]
        for e in ent:
            out.write(json.dumps({"rid": e["id"]["label"], "app_id": a["id"], "app": a["name"], "genre": a["genre"],
                                  "tier": a["tier"], "rating": int(e["im:rating"]["label"]),
                                  "date": e["updated"]["label"][:10], "title": e["title"]["label"],
                                  "text": e["content"]["label"]}, ensure_ascii=False) + "\n")
            n += 1
        time.sleep(0.4)
        if len(ent) < 50:
            break
    print(a["genre"], a["tier"], a["name"][:30], n, file=sys.stderr)
print("reviews", n, file=sys.stderr)
