# update_db.py
from pymongo import MongoClient
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

client = MongoClient(os.environ.get('MONGO_URI'))
db = client[os.environ.get('DATABASE_NAME', 'tv_shows')]

# Find all documents that *don't* have a created_at field.
for doc in db.tv_shows.find({'created_at': {'$exists': False}}):
    # Use the message_id as a proxy for age, then convert to datetime.
    # This isn't perfect, but it's better than nothing.
    # The smaller the message_id, the "older" we assume the show is.
    # Create a datetime object.  We'll make them all on Jan 1, 2024,
    # and vary the hour based on the message_id.
    # This makes the update deterministic and consistent.
    created_at = datetime(2024, 1, 1, doc['message_id'] % 24, 0, 0, tzinfo=timezone.utc)
    db.tv_shows.update_one({'_id': doc['_id']}, {'$set': {'created_at': created_at}})
    print(f"Updated document {doc['_id']} with created_at: {created_at}")

print("Finished updating documents.")
client.close()
