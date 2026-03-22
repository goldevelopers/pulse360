# pulse360 News Sources
# Manage active sources by editing the `active` column (yes/no).
# Every row with `active: yes` is picked up by researcher.py on each run.
# Supported types: rss, newsapi, gnews

| Name               | Type    | URL / Endpoint                                               | Countries        | Categories               | active |
|--------------------|---------|--------------------------------------------------------------|------------------|--------------------------|--------|
| BBC World          | rss     | http://feeds.bbci.co.uk/news/world/rss.xml                   | global           | Politics,Economy         | yes    |
| BBC Sport          | rss     | http://feeds.bbci.co.uk/sport/rss.xml                        | global           | Sports                   | yes    |
| Reuters Top News   | rss     | https://feeds.reuters.com/reuters/topNews                    | global           | Politics,Economy         | yes    |
| Reuters Biz        | rss     | https://feeds.reuters.com/reuters/businessNews               | global           | Economy                  | yes    |
| AP Top News        | rss     | https://feeds.apnews.com/apnews/topnews                      | global           | Politics                 | yes    |
| AP Sports          | rss     | https://feeds.apnews.com/apnews/sports                       | global           | Sports                   | yes    |
| Al Jazeera         | rss     | https://www.aljazeera.com/xml/rss/all.xml                    | global           | Politics,Economy         | yes    |
| Sky News           | rss     | https://feeds.skynews.com/feeds/rss/world.xml                | global           | Politics                 | yes    |
| ESPN               | rss     | https://www.espn.com/espn/rss/news                           | global           | Sports                   | yes    |
| Variety            | rss     | https://variety.com/feed/                                    | global           | Showbiz                  | yes    |
| Hollywood Reporter | rss     | https://www.hollywoodreporter.com/feed/                      | global           | Showbiz                  | yes    |
| Deadline           | rss     | https://deadline.com/feed/                                   | global           | Showbiz                  | yes    |
| TechCrunch         | rss     | https://techcrunch.com/feed/                                 | global           | Tech                     | yes    |
| Ars Technica       | rss     | https://feeds.arstechnica.com/arstechnica/index              | global           | Tech                     | yes    |
| The Verge          | rss     | https://www.theverge.com/rss/index.xml                       | global           | Tech                     | yes    |
| Wired              | rss     | https://www.wired.com/feed/rss                               | global           | Tech                     | yes    |
| NewsAPI Top        | newsapi | https://newsapi.org/v2/top-headlines                         | configurable     | Politics,Economy,Sports,Tech | yes    |
| GNews              | gnews   | https://gnews.io/api/v4/top-headlines                        | configurable     | Politics,Economy,Sports,Tech | yes    |
| Financial Times    | rss     | https://www.ft.com/?format=rss                                 | global           | Economy                  | yes    |
| The Economist     | rss     | https://www.economist.com/finance-and-economics/rss.xml       | global           | Economy                  | yes    |
| Bloomberg         | rss     | https://www.bloomberg.com/feed/podcast/etf-report.xml         | global           | Economy                  | yes    |
| CNBC              | rss     | https://www.cnbc.com/id/10001147/device/rss/rss.html           | global           | Economy                  | yes    |
| MarketWatch       | rss     | https://feeds.marketwatch.com/marketwatch/topstories/          | global           | Economy                  | yes    |

## Adding a new source

1. Add a new row to the table above.
2. Set `active` to `yes`.
3. Supported `type` values: `rss`, `newsapi`, `gnews`.
4. For `rss` sources, `URL / Endpoint` is the feed URL.
5. For `newsapi`/`gnews`, the URL is the endpoint — the agent supplies query params (country, category, apiKey) automatically.
6. Commit and push — the next scheduled run will pick up the new source.
