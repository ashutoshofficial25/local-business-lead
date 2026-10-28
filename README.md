# Local Business Leads

Find local businesses (restaurants, clinics, clothing stores, gyms, ...) with the Google
Places API, score each one for "website / app opportunity", store them in a local MongoDB,
and track your pitching from a small web app.

![Leads page](docs/screenshot-leads.png)

**What you get**

- **Find businesses page** — pick an area, radius and categories, start a search, watch progress live.
- **Leads page** — every business with score (Hot / Warm / Later), phone, one-click WhatsApp,
  website status and rating. Filter by group, status, category, "no website", min reviews,
  or follow-ups due. Change status, follow-up date, notes and deal value inline (auto-saved).
- **MongoDB storage** — re-running a search refreshes Google data but never overwrites your
  status, notes or follow-ups.
- **Export** the current filtered list to Excel or CSV.

## 1. Get a Google API key

1. Go to https://console.cloud.google.com/ and create a project.
2. Turn on billing for the project (Google gives a free monthly usage allowance; check current pricing).
3. Go to **APIs & Services → Library**, search **Places API (New)** and click **Enable**.
4. Go to **APIs & Services → Credentials → Create credentials → API key**.
5. Restrict the key to **Places API (New)** only.
6. Optional but recommended: set a budget alert in **Billing → Budgets & alerts**.

Never commit your API key to GitHub.

## 2. Start MongoDB locally

Pick one:

- **Docker (easiest):** `docker compose up -d` — starts MongoDB on `localhost:27017`, data is kept in a Docker volume.
- **Install directly:** [MongoDB Community Server](https://www.mongodb.com/try/download/community)
  (Windows/Mac/Linux). It runs on `localhost:27017` by default.

## 3. Install

Python 3.9+:

```bash
pip install -r requirements.txt
```

## 4. Add your settings to `.env`

```bash
cp .env.example .env       # Windows: copy .env.example .env
```

Open `.env` and fill in at least:

```
GOOGLE_MAPS_API_KEY=your-key
LOCATION=Your City
LAT=26.76
LNG=83.37
RADIUS=8000
MONGO_URI=mongodb://localhost:27017
```

Find your area's latitude/longitude by right-clicking the spot on Google Maps.
`.env` is in `.gitignore`, so your key is never pushed to GitHub.

## 5. Run the web app

```bash
python app.py
```

Or do everything (start MongoDB in Docker, create `.venv`, install, run) in one go: `./run.sh`

Open http://127.0.0.1:5000

1. Go to **Find businesses**, check the area and categories, click **Start search**.
2. When it finishes, click **View leads**.
3. Work through Hot leads first: call or WhatsApp, then update **Status**, **Next follow-up** and **Notes**.
4. Tick **Follow-up due** each morning to see who to call today.

## Command-line option (no web app)

The original script still works and saves Excel/CSV files:

```bash
python places_leads.py                       # uses .env settings
python places_leads.py --categories "restaurants,gyms" --grid 2
python places_leads.py --save-db             # also save the results into MongoDB
```

| Option | What it does |
| --- | --- |
| `--location` | Area name added to each search |
| `--lat`, `--lng` | Centre of your search area |
| `--radius` | Radius in metres (default 5000) |
| `--grid 3` | Splits the area into 3×3 smaller searches — finds many more businesses, but makes 9× the API calls |
| `--categories "restaurants,gyms"` | Only search these (default: 18 categories) |
| `--output my_leads` | Output file name |
| `--include-closed` | Keep closed businesses |
| `--save-db` | Save results to MongoDB too |

Google returns at most 60 results per search, so use a bigger grid for busy categories.

## Scoring

| Signal | Points |
| --- | --- |
| No website (or only Instagram/Facebook link) | +40 |
| 100+ reviews and no website | +30 |
| Only social media as website | +15 |
| Rating 4.0+ | +10 |
| High-value category (restaurant, clinic, hotel, gym, salon, coaching, school) | +10 |
| Under 10 reviews | −10 |

70+ = Hot, 40–69 = Warm, below 40 = Later. Hover the score badge in the app to see why.
Businesses that already have a website are flagged to check by hand — if it's old or
broken, they're a redesign lead.

## Project files

| File | Purpose |
| --- | --- |
| `app.py` | Flask web app (pages, API, export) |
| `db.py` | MongoDB storage (`leads` and `searches` collections) |
| `places_leads.py` | Google Places search, scoring, Excel/CSV, CLI |
| `templates/`, `static/` | Frontend HTML and CSS |
| `docker-compose.yml` | Local MongoDB |
| `.env.example` | Settings template |

## Cost tip

Each page of results is one billable request. 18 categories × 3 pages ≈ 54 requests per
run; grid 3 makes it ~9× more. The search page shows an estimate before you start.
Start small with a few categories to test.
