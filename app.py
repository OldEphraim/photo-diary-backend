from flask import Flask, request, jsonify
from flask_cors import CORS
import boto3
import os
import uuid
import json
from werkzeug.utils import secure_filename
from datetime import datetime
from dotenv import load_dotenv
import jwt
from jwt import PyJWKClient

load_dotenv()

app = Flask(__name__)
CORS(app)

# S3 config from .env
S3_BUCKET = os.getenv("S3_BUCKET")
S3_REGION = os.getenv("S3_REGION")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# Clerk config
CLERK_BASE_URL = os.getenv("CLERK_BASE_URL")

# S3 client
s3 = boto3.client(
    "s3",
    region_name=S3_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)

# Entry persistence
ENTRIES_FILE = "entries.json"
ENTRIES = {}  # in-memory store

def load_entries():
    global ENTRIES
    try:
        with open(ENTRIES_FILE, "r") as f:
            ENTRIES.update(json.load(f))
            print("📂 Loaded entries from disk.")
    except FileNotFoundError:
        print("📁 No saved entries found; starting fresh.")
        ENTRIES = {}
    except Exception as e:
        print("❌ Error loading entries.json:", e)
        ENTRIES = {}

def save_entries():
    try:
        with open(ENTRIES_FILE, "w") as f:
            json.dump(ENTRIES, f, indent=2)
            print("💾 Saved entries to disk.")
    except Exception as e:
        print("❌ Failed to save entries:", e)

# Load entries at startup
load_entries()

# Auth
def verify_token(headers):
    token = headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        print("❌ No token provided")
        return None

    jwks_url = f"{CLERK_BASE_URL}/.well-known/jwks.json"
    try:
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        decoded_token = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=None,
            issuer=CLERK_BASE_URL,
        )
        user_id = decoded_token.get("sub")
        print("✅ Token verified for user_id:", user_id)
        return user_id
    except Exception as e:
        print("❌ JWT verification failed:", str(e))
        return None

# Routes
@app.route("/api/upload", methods=["POST"])
def upload():
    print("📥 Received POST /api/upload")
    user_id = verify_token(request.headers)
    print("🔑 Clerk user_id:", user_id)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get("file")
    caption = request.form.get("caption", "")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    ext = secure_filename(file.filename).split('.')[-1]
    key = f"user_uploads/{user_id}/{uuid.uuid4()}.{ext}"

    s3.upload_fileobj(file, S3_BUCKET, key, ExtraArgs={"ContentType": file.content_type})
    file_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"

    entry_id = str(uuid.uuid4())
    entry = {
        "id": entry_id,
        "media_url": file_url,
        "caption": caption,
        "created_at": datetime.utcnow().isoformat()
    }

    ENTRIES.setdefault(user_id, []).append(entry)
    save_entries()

    return jsonify(entry)

@app.route("/api/entries", methods=["GET"])
def get_entries():
    user_id = verify_token(request.headers)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(ENTRIES.get(user_id, []))

@app.route("/api/entry/<entry_id>", methods=["DELETE"])
def delete_entry(entry_id):
    user_id = verify_token(request.headers)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    user_entries = ENTRIES.get(user_id, [])
    updated_entries = []
    deleted_entry = None

    for entry in user_entries:
        if entry["id"] == entry_id:
            deleted_entry = entry
        else:
            updated_entries.append(entry)

    if not deleted_entry:
        return jsonify({"error": "Entry not found"}), 404

    try:
        media_url = deleted_entry["media_url"]
        s3_key = media_url.split(f".amazonaws.com/")[-1]
        s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        print(f"🗑️ Deleted from S3: {s3_key}")
    except Exception as e:
        print("❌ Failed to delete from S3:", e)

    ENTRIES[user_id] = updated_entries
    save_entries()

    return jsonify({"success": True})

@app.route("/ping")
def ping():
    return "pong"

if __name__ == "__main__":
    app.run(debug=True)
