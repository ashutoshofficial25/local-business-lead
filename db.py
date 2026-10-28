"""
db.py - MongoDB storage for business leads.

Collections:
  leads         one document per business (keyed by Google place_id)
  dispositions  one document per contact attempt: outcome, note, next follow-up time
  searches      history of searches run from the web app (with progress log)

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
    db.leads.create_index("next_follow_up")
    db.searches.create_index([("started_at", DESCENDING)])
    db.dispositions.create_index([("place_id", ASCENDING), ("contacted_at", DESCENDING)])
    db.dispositions.create_index("next_follow_up_at")


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
                    "next_follow_up_at": "",
                    "contact_count": 0,
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


def get_lead(place_id):
    return get_db().leads.find_one({"place_id": place_id}, {"_id": 0})


def update_lead(place_id, changes):
    allowed = {"lead_status", "notes", "last_contact", "next_follow_up", "next_follow_up_at",
               "deal_value_inr", "contact_person", "proposal_sent"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if "lead_status" in changes and changes["lead_status"] not in LEAD_STATUSES:
        raise ValueError("Unknown lead status")
    if "deal_value_inr" in changes:
        v = str(changes["deal_value_inr"]).strip()
        changes["deal_value_inr"] = float(v) if v else None
    if "proposal_sent" in changes:
        changes["proposal_sent"] = changes["proposal_sent"] in (True, "true", "1", "on")
        changes["proposal_sent_at"] = now() if changes["proposal_sent"] else None

    # Keep next_follow_up (date, used for filters) and next_follow_up_at (date + time) in sync
    if "next_follow_up_at" in changes:
        at = (changes["next_follow_up_at"] or "").strip()
        changes["next_follow_up_at"] = at
        changes["next_follow_up"] = at[:10]
    elif "next_follow_up" in changes:
        day = (changes["next_follow_up"] or "").strip()
        old = (get_lead(place_id) or {}).get("next_follow_up_at") or ""
        time_part = old[10:] if len(old) > 10 else ""
        changes["next_follow_up"] = day
        changes["next_follow_up_at"] = (day + time_part) if day else ""

    if not changes:
        return False
    changes["edited_at"] = now()
    res = get_db().leads.update_one({"place_id": place_id}, {"$set": changes})
    return res.matched_count == 1


def save_proposal(place_id, language, message):
    get_db().leads.update_one(
        {"place_id": place_id},
        {"$push": {"proposals": {"language": language, "message": message, "created_at": now()}},
         "$set": {"proposal_language": language}},
    )


def delete_lead(place_id):
    get_db().dispositions.delete_many({"place_id": place_id})
    return get_db().leads.delete_one({"place_id": place_id}).deleted_count == 1


# -------------------------------------------------------------- dispositions
# One document per contact attempt (call, WhatsApp, visit ...), with what happened,
# a note, and optionally when to contact the lead next.

CHANNELS = ["Call", "WhatsApp", "Visit", "Email", "Other"]

# Outcome -> lead status it moves the lead to (None = keep current, but New becomes Contacted)
OUTCOMES = {
    "No answer": None,
    "Busy / call back": "Contacted",
    "Connected - interested": "Interested",
    "Connected - not interested": "Lost",
    "Demo scheduled": "Interested",
    "Demo shown": "Demo shown",
    "Proposal sent": "Interested",
    "Negotiating": "Interested",
    "Won": "Won",
    "Lost": "Lost",
    "Wrong number": "Lost",
    "Other": None,
}


def local_now_str():
    """Local time as 'YYYY-MM-DDTHH:MM' (same format as <input type=datetime-local>)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def add_disposition(place_id, data):
    """Log a contact attempt and update the lead (status, last contact, next follow-up)."""
    lead = get_lead(place_id)
    if not lead:
        raise ValueError("Lead not found")

    outcome = (data.get("outcome") or "").strip()
    if outcome not in OUTCOMES:
        raise ValueError("Pick an outcome")
    channel = (data.get("channel") or "Call").strip()
    if channel not in CHANNELS:
        channel = "Other"

    needs_follow_up = str(data.get("needs_follow_up", "")).lower() in ("1", "on", "true", "yes")
    follow_up_at = (data.get("next_follow_up_at") or "").strip() if needs_follow_up else ""
    if needs_follow_up and not follow_up_at:
        raise ValueError("Pick the next follow-up date and time")

    status = (data.get("lead_status") or "").strip()
    if status and status not in LEAD_STATUSES:
        raise ValueError("Unknown lead status")
    if not status:
        status = OUTCOMES[outcome] or ("Contacted" if lead.get("lead_status") == "New" else lead.get("lead_status"))

    contacted_at = (data.get("contacted_at") or "").strip() or local_now_str()
    doc = {
        "place_id": place_id,
        "lead_name": lead.get("name", ""),
        "channel": channel,
        "outcome": outcome,
        "note": (data.get("note") or "").strip(),
        "contact_person": (data.get("contact_person") or "").strip(),
        "contacted_at": contacted_at,
        "needs_follow_up": needs_follow_up,
        "next_follow_up_at": follow_up_at,
        "status_after": status,
        "created_at": now(),
    }
    res = get_db().dispositions.insert_one(doc)

    lead_changes = {
        "lead_status": status,
        "last_contact": contacted_at[:10],
        "last_contact_at": contacted_at,
        "last_outcome": outcome,
        "next_follow_up_at": follow_up_at,
        "next_follow_up": follow_up_at[:10],
        "edited_at": now(),
    }
    if doc["contact_person"]:
        lead_changes["contact_person"] = doc["contact_person"]
    get_db().leads.update_one(
        {"place_id": place_id},
        {"$set": lead_changes, "$inc": {"contact_count": 1}},
    )
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


def list_dispositions(place_id):
    docs = list(get_db().dispositions.find({"place_id": place_id})
                .sort([("contacted_at", DESCENDING), ("created_at", DESCENDING)]))
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


def delete_disposition(disposition_id):
    from bson import ObjectId
    res = get_db().dispositions.delete_one({"_id": ObjectId(disposition_id)})
    return res.deleted_count == 1


def follow_ups(days_ahead=7):
    """Leads with a next follow-up set, split into overdue / today / upcoming."""
    from datetime import timedelta
    now_local = datetime.now()
    now_str = now_local.strftime("%Y-%m-%dT%H:%M")
    today = now_local.strftime("%Y-%m-%d")
    until = (now_local + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    closed = ["Won", "Lost"]
    leads = list(get_db().leads.find(
        {"next_follow_up": {"$ne": "", "$exists": True, "$lte": until},
         "lead_status": {"$nin": closed}},
        {"_id": 0},
    ).sort([("next_follow_up_at", ASCENDING), ("next_follow_up", ASCENDING)]))

    groups = {"overdue": [], "today": [], "upcoming": []}
    for l in leads:
        at = l.get("next_follow_up_at") or l.get("next_follow_up")
        if l["next_follow_up"] < today:
            groups["overdue"].append(l)
        elif l["next_follow_up"] == today:
            l["is_late"] = len(at) > 10 and at < now_str
            groups["today"].append(l)
        else:
            groups["upcoming"].append(l)
    return groups


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
