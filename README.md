# Local Business Leads

Gets a list of local businesses (restaurants, clinics, clothing stores, gyms, ...) from the
Google Places API, scores each one for "website / app opportunity", and saves an Excel
sheet sorted by score — ready for calling and pitching.

## 1. Get a Google API key

1. Go to https://console.cloud.google.com/ and create a project.
2. Turn on billing for the project (Google gives a free monthly usage allowance; check current pricing).
3. Go to **APIs & Services → Library**, search **Places API (New)** and click **Enable**.
4. Go to **APIs & Services → Credentials → Create credentials → API key**.
5. Restrict the key to **Places API (New)** only.
6. Optional but recommended: set a budget alert in **Billing → Budgets & alerts**.

Never commit your API key to GitHub.

## 2. Install

```bash
pip install -r requirements.txt
```

## 3. Run

Find your area's latitude/longitude by right-clicking the spot on Google Maps.

```bash
# Mac / Linux
export GOOGLE_MAPS_API_KEY="your-key"
# Windows (PowerShell)
$env:GOOGLE_MAPS_API_KEY="your-key"

python places_leads.py --location "Your City" --lat 26.76 --lng 83.37 --radius 8000
```

Options:

| Option | What it does |
| --- | --- |
| `--location` | Area name added to each search (required) |
| `--lat`, `--lng` | Centre of your search area |
| `--radius` | Radius in metres (default 5000) |
| `--grid 3` | Splits the area into 3×3 smaller searches — finds many more businesses, but makes 9× the API calls |
| `--categories "restaurants,gyms"` | Only search these (default: 18 categories) |
| `--output my_leads` | Output file name |
| `--include-closed` | Keep closed businesses |

Google returns at most 60 results per search, so use `--grid` for busy categories.

## 4. Output

- `business_leads.xlsx` — **Leads** sheet sorted by score (Hot / Warm / Later), with
  tracking columns (lead status dropdown, follow-up dates, notes, deal value) and a
  **Summary** sheet per category.
- `business_leads.csv` — same data as CSV.

## Scoring

| Signal | Points |
| --- | --- |
| No website (or only Instagram/Facebook link) | +40 |
| 100+ reviews and no website | +30 |
| Only social media as website | +15 |
| Rating 4.0+ | +10 |
| High-value category (restaurant, clinic, hotel, gym, salon, coaching, school) | +10 |
| Under 10 reviews | −10 |

70+ = Hot, 40–69 = Warm, below 40 = Later. Businesses that already have a website are
flagged to check by hand — if it's old or broken, they're a redesign lead.

## Cost tip

Each page of results is one billable request. 18 categories × 3 pages ≈ 54 requests per
run; `--grid 3` makes it ~9× more. Start small with `--categories` to test.
