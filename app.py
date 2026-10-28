"""
app.py - Small web frontend for finding and tracking local business leads.

    python app.py         then open http://127.0.0.1:5000
"""

import csv
import io
import os
import threading
import time

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, url_for

import requests

import db
import places_leads as pl

pl.load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

app = Flask(__name__)


def current_filters():
    keys = ["q", "group", "lead_status", "category", "location", "no_website",
            "min_reviews", "follow_up_due", "proposal", "sort"]
    return {k: request.args.get(k, "") for k in keys}


# ------------------------------------------------------------------- pages

@app.route("/")
def index():
    filters = current_filters()
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = 50
    leads, total = db.find_leads(filters, page=page, per_page=per_page)
    return render_template(
        "index.html",
        leads=leads, total=total, page=page, pages=max((total + per_page - 1) // per_page, 1),
        filters=filters, stats=db.stats(),
        categories=db.distinct_values("categories"),
        locations=db.distinct_values("location"),
        statuses=db.LEAD_STATUSES,
    )


@app.route("/leads/<place_id>")
def lead_detail(place_id):
    lead = db.get_lead(place_id)
    if not lead:
        abort(404)
    from datetime import datetime, timedelta
    now_local = datetime.now()
    return render_template(
        "lead.html",
        lead=lead,
        dispositions=db.list_dispositions(place_id),
        statuses=db.LEAD_STATUSES,
        outcomes=db.OUTCOMES,
        channels=db.CHANNELS,
        now_local=now_local.strftime("%Y-%m-%dT%H:%M"),
        tomorrow_11=(now_local + timedelta(days=1)).strftime("%Y-%m-%dT11:00"),
        back=request.args.get("back") or url_for("index"),
    )


@app.route("/follow-ups")
def follow_ups_page():
    return render_template("follow_ups.html", groups=db.follow_ups(days_ahead=7),
                           today=__import__("datetime").datetime.now().strftime("%Y-%m-%d"))


@app.route("/search", methods=["GET"])
def search_page():
    return render_template(
        "search.html",
        defaults={
            "location": os.environ.get("LOCATION", ""),
            "lat": os.environ.get("LAT", ""),
            "lng": os.environ.get("LNG", ""),
            "radius": os.environ.get("RADIUS", "5000"),
            "grid": os.environ.get("GRID", "1"),
        },
        all_categories=pl.DEFAULT_CATEGORIES,
        selected=[c.strip() for c in os.environ.get("CATEGORIES", "").split(",") if c.strip()]
        or pl.DEFAULT_CATEGORIES,
        has_key=bool(os.environ.get("GOOGLE_MAPS_API_KEY")),
        searches=db.recent_searches(),
    )


@app.route("/search", methods=["POST"])
def start_search():
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "GOOGLE_MAPS_API_KEY is missing in .env", 400

    f = request.form
    location = f.get("location", "").strip()
    if not location:
        return "Location is required", 400

    def num(name, cast=float):
        v = f.get(name, "").strip()
        return cast(v) if v else None

    categories = f.getlist("categories")
    extra = [c.strip() for c in f.get("extra_categories", "").split(",") if c.strip()]
    categories = categories + extra
    if not categories:
        return "Pick at least one category", 400

    params = {
        "location": location,
        "lat": num("lat"),
        "lng": num("lng"),
        "radius": num("radius") or 5000,
        "grid": num("grid", int) or 1,
        "categories": categories,
        "include_closed": f.get("include_closed") == "on",
    }
    if params["grid"] > 1 and (params["lat"] is None or params["lng"] is None):
        return "Grid search needs latitude and longitude", 400

    search_id = db.create_search(params)
    threading.Thread(target=run_search, args=(search_id, api_key, params), daemon=True).start()
    return redirect(url_for("search_status_page", search_id=search_id))


def run_search(search_id, api_key, params):
    try:
        rows = pl.collect_leads(
            api_key, params["location"], params["lat"], params["lng"], params["radius"],
            params["grid"], params["categories"], params["include_closed"],
            progress=lambda line: db.log_search(search_id, line),
        )
        result = db.upsert_leads(rows, location=params["location"])
        result["found"] = len(rows)
        db.finish_search(search_id, result=result)
    except Exception as e:  # show the error in the UI instead of crashing the thread
        db.finish_search(search_id, error=str(e))


@app.route("/search/<search_id>")
def search_status_page(search_id):
    s = db.get_search(search_id)
    if not s:
        abort(404)
    return render_template("search_status.html", s=s)


# --------------------------------------------------------------------- API

@app.route("/api/search/<search_id>")
def search_status(search_id):
    s = db.get_search(search_id)
    if not s:
        abort(404)
    return jsonify({k: s[k] for k in ("status", "log", "result", "error")})


@app.route("/api/leads/<place_id>", methods=["PATCH", "POST"])
def edit_lead(place_id):
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        ok = db.update_lead(place_id, data)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": ok}), (200 if ok else 404)


PROPOSAL_LANGUAGES = {
    "english": "English",
    "hindi": "Hindi (Devanagari script)",
    "hinglish": "Hinglish (Hindi written in English/Roman letters, mixed with English words)",
}


def proposal_prompt(lead, language):
    website = lead.get("website") or ""
    reasons = lead.get("score_reasons") or ""
    if "no website" not in reasons:
        pitch = f"They have a website ({website}); offer a modern redesign / better online presence."
    elif website:
        pitch = f"They only have a social media page ({website}), no real website; offer a proper website."
    else:
        pitch = "They have no website; offer a website so customers can find them on Google."
    sender = os.environ.get("PROPOSAL_FROM", "").strip()
    return f"""Write a short WhatsApp message pitching website / app development to a local business owner.

Business: {lead.get("name")}
Category: {lead.get("category") or ", ".join(lead.get("categories", []))}
Address: {lead.get("address")}
Google rating: {lead.get("rating") or "n/a"} from {lead.get("reviews") or 0} reviews
Situation: {pitch}
Sender: {sender or "not given - do not sign with a name"}

Rules:
- Language: {PROPOSAL_LANGUAGES[language]}.
- Format for WhatsApp (use *bold*, no markdown headings), under 120 words:
  1. One opening line: greet and praise something specific (e.g. their rating and reviews).
  2. One line on what they are missing right now without a proper website.
  3. A checklist of 4-5 short lines, each starting with ✅, of what a website gets them
     (found on Google, more calls/WhatsApp enquiries, trust, menu/services/prices online,
     bookings or orders) - pick what fits this business.
  4. A little urgency / FOMO: customers search online first and nearby competitors are
     already getting those customers. Keep it believable - no invented numbers, fake
     deadlines, "limited slots" or claims about specific competitors.
  5. Call to action: ask for a short meeting or call this week to discuss their website.
     Do NOT offer or mention a demo.
- Confident and respectful, not pushy. Emojis only for the ✅ checklist plus at most one more.
- Don't guess the owner's name - greet the business by a short, clean form of its name
  (drop extra words like "Pro.", "and Trainer", owner names or locations from the listing).
- No placeholders like [Name]; no subject line. Output only the message text."""


def ask_gemini(api_key, prompt):
    """Try each model in GEMINI_MODEL (comma-separated); retry once when Gemini is busy."""
    models = [m.strip() for m in os.environ.get("GEMINI_MODEL", "").split(",") if m.strip()]
    error = "no model set"
    for model in models or ["gemini-3.8-flash", "gemini-flash-lite-latest"]:
        for attempt in range(2):
            try:
                resp = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=60,
                )
            except requests.RequestException as e:
                error = f"{model}: {e}"
                break
            if resp.ok:
                try:
                    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError, ValueError):
                    error = f"{model}: empty response"
                    break
            try:
                error = f"{model}: {resp.json()['error']['message']}"
            except (KeyError, ValueError):
                error = f"{model}: HTTP {resp.status_code}"
            if resp.status_code not in (429, 503):
                break  # bad key / unknown model: retrying won't help, try the next model
            if attempt == 0:
                time.sleep(2)
    raise RuntimeError("Gemini request failed - " + error)


@app.route("/api/leads/<place_id>/proposal", methods=["POST"])
def generate_proposal(place_id):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY is missing in .env"}), 400
    data = request.get_json(silent=True) or request.form.to_dict()
    language = (data.get("language") or "").lower()
    if language not in PROPOSAL_LANGUAGES:
        return jsonify({"ok": False, "error": "language must be hindi, english or hinglish"}), 400
    lead = db.get_lead(place_id)
    if not lead:
        return jsonify({"ok": False, "error": "Lead not found"}), 404

    try:
        message = ask_gemini(api_key, proposal_prompt(lead, language))
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    db.save_proposal(place_id, language, message)
    return jsonify({"ok": True, "message": message, "language": language})


@app.route("/api/leads/<place_id>", methods=["DELETE"])
def remove_lead(place_id):
    return jsonify({"ok": db.delete_lead(place_id)})


@app.route("/api/leads/<place_id>/dispositions", methods=["GET"])
def get_dispositions(place_id):
    return jsonify(db.list_dispositions(place_id))


@app.route("/leads/<place_id>/dispositions", methods=["POST"])
def add_disposition(place_id):
    """Form post from the lead page (also accepts JSON)."""
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        doc = db.add_disposition(place_id, data)
    except ValueError as e:
        if request.is_json:
            return jsonify({"ok": False, "error": str(e)}), 400
        return redirect(url_for("lead_detail", place_id=place_id, error=str(e)) + "#log-call")
    if request.is_json:
        doc.pop("created_at", None)
        return jsonify({"ok": True, "disposition": doc})
    return redirect(url_for("lead_detail", place_id=place_id, saved=1) + "#history")


@app.route("/api/dispositions/<disposition_id>", methods=["DELETE"])
def remove_disposition(disposition_id):
    return jsonify({"ok": db.delete_disposition(disposition_id)})


# ------------------------------------------------------------------ export

EXPORT_COLUMNS = pl.COLUMNS + ["contact_person", "contact_count", "last_outcome", "last_contact_at", "next_follow_up_at"]


@app.context_processor
def nav_counts():
    try:
        g = db.follow_ups(days_ahead=0)
        return {"due_count": len(g["overdue"]) + len(g["today"])}
    except Exception:
        return {"due_count": 0}


def export_rows():
    leads, _ = db.find_leads(current_filters(), per_page=0)
    for r in leads:
        r["searched_for"] = ", ".join(r.get("categories", []))
    return leads


@app.route("/export.csv")
def export_csv():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(export_rows())
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=business_leads.csv"})


@app.route("/export.xlsx")
def export_xlsx():
    import tempfile
    rows = export_rows()
    for r in rows:
        r.setdefault("group", "Later")
        r.setdefault("score_reasons", "")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = tmp.name
    pl.save_xlsx(rows, path, EXPORT_COLUMNS)
    return send_file(path, as_attachment=True, download_name="business_leads.xlsx")


@app.template_filter("inr")
def inr(v):
    if v in (None, ""):
        return ""
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return v


@app.template_filter("when")
def when(value):
    """'2026-10-25T14:30' -> 'Sun 25 Oct, 2:30 PM'; '2026-10-25' -> 'Sun 25 Oct'."""
    from datetime import datetime
    if not value:
        return ""
    try:
        if len(value) > 10:
            return datetime.strptime(value[:16], "%Y-%m-%dT%H:%M").strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
        return datetime.strptime(value, "%Y-%m-%d").strftime("%a %d %b")
    except (TypeError, ValueError):
        return value


@app.template_filter("local")
def local_time(dt):
    """UTC datetime from MongoDB -> local time string."""
    if not dt:
        return ""
    from datetime import timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d %b %Y, %I:%M %p")


@app.template_filter("wa")
def whatsapp_number(phone):
    """Turn a phone like '098765 43210' into '919876543210' for a wa.me link (India default)."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("0"):
        digits = digits.lstrip("0")
    if len(digits) == 10:
        digits = os.environ.get("COUNTRY_CODE", "91") + digits
    return digits


if __name__ == "__main__":
    db.init_indexes()
    port = int(os.environ.get("PORT", 5000))
    print(f"Open http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
