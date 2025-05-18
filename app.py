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
from botocore.exceptions import ClientError

load_dotenv()

app = Flask(__name__)
allowed_origins = os.getenv("ALLOWED_ORIGINS")
CORS(app, origins=allowed_origins.split(",") if allowed_origins else "*")

# S3 config
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

# Load per-user entries from S3
def load_user_entries(user_id):
    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key=f"entries/{user_id}.json")
        return json.load(response["Body"])
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            print(f"📁 No saved entries for {user_id}; starting fresh.")
        else:
            print("❌ Failed to load entries:", e)
        return []

# Save per-user entries to S3
def save_user_entries(user_id, entries):
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"entries/{user_id}.json",
            Body=json.dumps(entries),
            ContentType="application/json"
        )
        print(f"💾 Saved entries for {user_id} to S3.")
    except Exception as e:
        print("❌ Failed to save entries:", e)

# Verify Clerk token
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

# Upload endpoint
@app.route("/api/upload", methods=["POST"])
def upload():
    print("📥 Received POST /api/upload")
    user_id = verify_token(request.headers)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    caption = request.form.get("caption", "")
    file = request.files.get("file")

    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    ext = secure_filename(file.filename).split('.')[-1]
    file_key = f"user_uploads/{user_id}/{uuid.uuid4()}.{ext}"

    s3.upload_fileobj(file, S3_BUCKET, file_key, ExtraArgs={"ContentType": file.content_type})
    print(f"⬆️ Uploaded file: {file_key}")
    media_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{file_key}"

    entry = {
        "id": str(uuid.uuid4()),
        "media_url": media_url,
        "caption": caption,
        "created_at": datetime.utcnow().isoformat()
    }

    entries = load_user_entries(user_id)
    entries.append(entry)
    save_user_entries(user_id, entries)

    return jsonify(entry)

# Get entries
@app.route("/api/entries", methods=["GET"])
def get_entries():
    user_id = verify_token(request.headers)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    entries = load_user_entries(user_id)
    return jsonify(entries)

# Delete entry
@app.route("/api/entry/<entry_id>", methods=["DELETE"])
def delete_entry(entry_id):
    user_id = verify_token(request.headers)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    entries = load_user_entries(user_id)
    updated_entries = []
    deleted_entry = None

    for entry in entries:
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

    save_user_entries(user_id, updated_entries)
    return jsonify({"success": True})

# Health check
@app.route("/api/ping")
def ping():
    return "pong"

if __name__ == "__main__":
    app.run()
