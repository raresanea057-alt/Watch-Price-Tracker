# Kamasu Watch

Tracks Orient Kamasu prices across shops that deliver to Romania, and
publishes the result as a page. No server: GitHub Actions runs the scraper
twice a day, commits the history, and GitHub Pages serves the site.

```
kamasu_tracker.py           scraper + alerts
index.html                  the page (reads price_history.json)
price_history.json          created on first run, committed by the workflow
.github/workflows/track.yml the cron job
```

## Run it locally first

```bash
pip install requests beautifulsoup4
python3 kamasu_tracker.py
```

You'll get a table and a `price_history.json`. **Check the table before
trusting anything.** The `via` column says how each price was found:

| method    | trust | meaning |
|-----------|-------|---------|
| `json-ld` | high  | the shop published structured price data |
| `meta`    | high  | read from Open Graph meta tags |
| `text`    | low   | guessed by scanning visible text |
| `BROKEN`  | —     | fetch failed or no price found |

Anything on `text` can pick up the wrong number when a shop redesigns.
The page flags those cards too.

Then open the page:

```bash
python3 -m http.server 8000    # then visit localhost:8000
```

Opening `index.html` directly with `file://` will not work — the browser
blocks the `fetch` of the JSON. It needs to be served over http.

## Put it on GitHub

1. Push these four files to a repo.
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Actions → General → Workflow permissions: **Read and write**.
4. Actions tab → *Track Kamasu prices* → **Run workflow** to test it now
   rather than waiting for the cron.

The site lands at `https://<user>.github.io/<repo>/`.

## Tuning

- `TARGET_RON` in the script — the alert threshold and the dotted line.
- `PRODUCTS` — add any shop by pasting a product URL. Run it; if the price
  comes back sane, keep it. If you get `BROKEN`, drop it.
- `EUR_RON` — fixed conversion rate, drifts over time. Euro prices on the
  page are indicative, not exact.
- The cron is twice daily. Don't make it hourly: it won't find more, and
  it puts pointless load on small shops.

## Known limits

- **watchshop.ro blocks scrapers.** It returned 403. Track it via
  istoric-preturi.info instead, which already indexes it.
- **chrono24 listings are individual sellers** — a saved URL goes dead when
  the watch sells. Use Chrono24's own saved-search alerts.
- Scrapers rot. When a shop redesigns, you get `BROKEN` rather than silence,
  which is the point — but you do have to go fix the entry.
- The 3-second delay between requests and the browser User-Agent are
  deliberate. Lowering the delay is how you get your IP banned.
