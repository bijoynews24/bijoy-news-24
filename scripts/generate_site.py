def collect_all_items():
    all_items = []
    
    # প্রতিটি সোর্স থেকে সর্বোচ্চ কয়টি নিউজ নেওয়া যাবে তার লিমিট (এখানে সর্বোচ্চ ২ দেওয়া হলো)
    MAX_NEWS_PER_SOURCE = 2 
    
    for feed_url in FEEDS:
        try:
            print(f"ফিড ফেচ করা হচ্ছে: {feed_url}")
            parsed_feed = feedparser.parse(feed_url)
            src_name = source_name_from_url(feed_url)
            
            source_count = 0  # এই সোর্স থেকে কয়টি নেওয়া হলো তা ট্র্যাক করার জন্য
            
            for entry in parsed_feed.entries:
                if source_count >= MAX_NEWS_PER_SOURCE:
                    break  # এই পত্রিকার কোটা শেষ, পরবর্তী পত্রিকায় চলে যাবে
                
                title = entry.get("title", "শিরোনামহীন")
                link = entry.get("link", "#")
                
                # সামারি বা বিবরণ সংগ্রহ
                summary = ""
                if hasattr(entry, "summary"):
                    summary = entry.summary
                elif hasattr(entry, "description"):
                    summary = entry.description
                
                # ছবি খোঁজা (যদি RSS ফিডে থাকে)
                image_url = ""
                if hasattr(entry, "media_content") and entry.media_content:
                    image_url = entry.media_content[0].get("url", "")
                elif hasattr(entry, "enclosures") and entry.enclosures:
                    image_url = entry.enclosures[0].get("href", "")

                item = {
                    "title": title,
                    "link": link,
                    "summary": summary[:150] + "...",
                    "image": image_url,
                    "source": src_name,
                    "published": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                all_items.append(item)
                source_count += 1  # সোর্স কাউন্ট এক বাড়ল
                
        except Exception as exc:
            print(f"✘ ফিড পড়তে সমস্যা হয়েছে ({feed_url}): {exc}", file=sys.stderr)

    # ডুপ্লিকেট লিংক বাদ দেওয়া
    seen = set()
    unique_items = []
    for it in all_items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        unique_items.append(it)
        
    return unique_items
