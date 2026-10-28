"""
db.py - MongoDB storage for business leads.

Collections:
  leads    one document per business (keyed by Google place_id)
  searches history of searches run from the web app (with progress log)

Google data (name, phone, rating, score ...) is refreshed on every search, but your own
work (lead_status, notes, follow-up date, deal value) is never overwritten.
"""

import os
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient

# Fields that come from Google / scoring and are refreshed on each search
GOOGLE_FIELDS = [
    "name", "category", "address", "phone", "website", "rating", "reviews",
    "status", "maps_link", "lat", "lng", "score", "group", "score_reasons",
]

# Fields you edit yourself in the app
LEAD_STATUSES = ["New", "Contacted", "Demo shown", "Interested", "Won", "Lost", "Later"]

# Disposition = one logged contact attempt (kept as a list on the lead)
DISPOSITION_TYPES = ["Call", "WhatsApp", "Visit", "Email", "Meeting"]
DISPOSITION_OUTCOMES = ["Connected", "No answer", "Busy", "Interested", "Not interested",
                        "Call back later", "Wrong number"]

_client = None


def get_db():
    global _client
    if _client is None:
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = _client[os.environ.get("MONGO_DB", "business_leads")]
    return db


def set_client(client):
    """Used by tests to plug in a fake client."""
    global _client
    _client = client


def init_indexes():
    db = get_db()
    db.leads.create_index("place_id", unique=True)
    db.leads.create_index([("score", DESCENDING), ("reviews", DESCENDING)])
    db.leads.create_index("group")
    db.leads.create_index("lead_status")
    db.leads.create_index("categories")
    db.leads.create_index("location")
    db.searches.create_index([("started_at", DESCENDING)])


def now():
    return datetime.now(timezone.utc)


def upsert_leads(rows, location=""):
    """Insert new businesses, refresh Google data on existing ones, keep your notes/status."""
    db = get_db()
    new = updated = 0
    for r in rows:
        google = {f: r.get(f) for f in GOOGLE_FIELDS}
        google["updated_at"] = now()
        res = db.leads.update_one(
            {"place_id": r["place_id"]},
            {
                "$set": google,
                "$addToSet": {"categories": r.get("searched_for", ""), "location": location},
                "$setOnInsert": {
                    "place_id": r["place_id"],
                    "lead_status": "New",
                    "notes": "",
                    "last_contact": "",
                    "next_follow_up": "",
                    "deal_value_inr": None,
                    "proposal_sent": False,
                    "created_at": now(),
                },
            },
            upsert=True,
        )
        if res.upserted_id is not None:
            new += 1
        else:
            updated += 1
    return {"new": new, "updated": updated}


def build_query(filters):
    """Turn the filter form (dict of strings) into a MongoDB query."""
    q = {}
    text = (filters.get("q") or "").strip()
    if text:
        import re
        rx = {"$regex": re.escape(text), "$options": "i"}
        q["$or"] = [{"name": rx}, {"address": rx}, {"phone": rx}, {"notes": rx}]
    if filters.get("group"):
        q["group"] = filters["group"]
    if filters.get("lead_status"):
        q["lead_status"] = filters["lead_status"]
    if filters.get("category"):
        q["categories"] = filters["category"]
    if filters.get("location"):
        q["location"] = filters["location"]
    if filters.get("no_website") in ("1", "on", "true"):
        q["score_reasons"] = {"$regex": "no website"}
    if filters.get("min_reviews"):
        try:
            q["reviews"] = {"$gte": int(filters["min_reviews"])}
        except ValueError:
            pass
    if filters.get("follow_up_due") in ("1", "on", "true"):
        today = datetime.now().strftime("%Y-%m-%d")
        q["next_follow_up"] = {"$ne": "", "$lte": today}
    if filters.get("proposal") == "sent":
        q["proposal_sent"] = True
    elif filters.get("proposal") == "pending":
        q.update(PROPOSAL_PENDING)
    return q


# Not sent yet and has a phone to WhatsApp (old leads have no proposal_sent field)
PROPOSAL_PENDING = {"proposal_sent": {"$ne": True}, "phone": {"$nin": ["", None]}}


SORTS = {
    "score": [("score", DESCENDING), ("reviews", DESCENDING)],
    "reviews": [("reviews", DESCENDING)],
    "rating": [("rating", DESCENDING), ("reviews", DESCENDING)],
    "name": [("name", ASCENDING)],
    "newest": [("created_at", DESCENDING)],
    "follow_up": [("next_follow_up", ASCENDING)],
}


def find_leads(filters, page=1, per_page=50):
    db = get_db()
    q = build_query(filters)
    sort = SORTS.get(filters.get("sort") or "score", SORTS["score"])
    total = db.leads.count_documents(q)
    cursor = db.leads.find(q, {"_id": 0}).sort(sort)
    if per_page:
        cursor = cursor.skip((page - 1) * per_page).limit(per_page)
    return list(cursor), total


def update_lead(place_id, changes):
    allowed = {"lead_status", "notes", "last_contact", "next_follow_up", "deal_value_inr",
               "proposal_sent"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if "lead_status" in changes and changes["lead_status"] not in LEAD_STATUSES:
        raise ValueError("Unknown lead status")
    if "deal_value_inr" in changes:
        v = str(changes["deal_value_inr"]).strip()
        changes["deal_value_inr"] = float(v) if v else None
    if "proposal_sent" in changes:
        changes["proposal_sent"] = changes["proposal_sent"] in (True, "true", "1", "on")
        changes["proposal_sent_at"] = now() if changes["proposal_sent"] else None
    if not changes:
        return False
    changes["edited_at"] = now()
    res = get_db().leads.update_one({"place_id": place_id}, {"$set": changes})
    return res.matched_count == 1


def get_lead(place_id):
    return get_db().leads.find_one({"place_id": place_id}, {"_id": 0})


def save_proposal(place_id, language, message):
    get_db().leads.update_one(
        {"place_id": place_id},
        {"$push": {"proposals": {"language": language, "message": message, "created_at": now()}},
         "$set": {"proposal_language": language}},
    )


def add_disposition(place_id, form):
    """Log a contact attempt; also bumps last_contact and optionally status / follow-up."""
    d = {
        "type": form.get("type", ""),
        "outcome": form.get("outcome", ""),
        "note": (form.get("note") or "").strip(),
        "created_at": now(),
    }
    if d["type"] not in DISPOSITION_TYPES or d["outcome"] not in DISPOSITION_OUTCOMES:
        raise ValueError("Pick a type and an outcome")
    changes = {"last_contact": datetime.now().strftime("%Y-%m-%d"), "edited_at": now()}
    if form.get("lead_status"):
        if form["lead_status"] not in LEAD_STATUSES:
            raise ValueError("Unknown lead status")
        changes["lead_status"] = d["lead_status"] = form["lead_status"]
    if form.get("next_follow_up"):
        changes["next_follow_up"] = d["next_follow_up"] = form["next_follow_up"]
    res = get_db().leads.update_one({"place_id": place_id},
                                    {"$push": {"dispositions": d}, "$set": changes})
    return res.matched_count == 1


def delete_lead(place_id):
    return get_db().leads.delete_one({"place_id": place_id}).deleted_count == 1


def stats():
    db = get_db()
    leads = db.leads
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "total": leads.count_documents({}),
        "hot": leads.count_documents({"group": "Hot"}),
        "warm": leads.count_documents({"group": "Warm"}),
        "no_website": leads.count_documents({"score_reasons": {"$regex": "no website"}}),
        "contacted": leads.count_documents({"lead_status": {"$nin": ["New"]}}),
        "won": leads.count_documents({"lead_status": "Won"}),
        "follow_up_due": leads.count_documents({"next_follow_up": {"$ne": "", "$lte": today}}),
        "proposal_pending": leads.count_documents(PROPOSAL_PENDING),
    }


def distinct_values(field):
    return sorted(v for v in get_db().leads.distinct(field) if v)


# ------------------------------------------------------------------ searches

def create_search(params):
    doc = {
        "params": params,
        "status": "running",
        "log": [],
        "started_at": now(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    return str(get_db().searches.insert_one(doc).inserted_id)


def log_search(search_id, line):
    from bson import ObjectId
    get_db().searches.update_one({"_id": ObjectId(search_id)}, {"$push": {"log": line}})


def finish_search(search_id, result=None, error=None):
    from bson import ObjectId
    get_db().searches.update_one(
        {"_id": ObjectId(search_id)},
        {"$set": {"status": "failed" if error else "done", "result": result,
                  "error": error, "finished_at": now()}},
    )


def get_search(search_id):
    from bson import ObjectId
    doc = get_db().searches.find_one({"_id": ObjectId(search_id)})
    if doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def recent_searches(limit=10):
    docs = list(get_db().searches.find().sort("started_at", DESCENDING).limit(limit))
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs
