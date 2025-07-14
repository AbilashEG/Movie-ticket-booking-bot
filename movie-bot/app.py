import os
import json
import re
import uuid
from datetime import datetime
import boto3
from flask import Flask, request, render_template, jsonify, session
from flask_session import Session
from dotenv import load_dotenv
from boto3.dynamodb.conditions import Key, Attr

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecret")

# Flask-Session config to avoid oversized cookies
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_FILE_DIR"] = "./flask_session_data"
Session(app)

# Load movie data
with open("movie_data.json") as f:
    MOVIE_DATA = json.load(f)

# AWS config
region = os.getenv("AWS_REGION", "us-east-1")
aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
inference_arn = os.getenv("BEDROCK_INFERENCE_ARN")
model_id = inference_arn.split("inference-profile/")[-1]

# AWS clients
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=region,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
)

dynamodb = boto3.resource(
    "dynamodb",
    region_name=region,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
)
table = dynamodb.Table("MovieTicketBookings")

def get_system_prompt():
    return (
        "You are MovieBot, a helpful movie ticket booking assistant.\n"
        "Guide the user through booking a ticket step-by-step:\n"
        "1. Ask for the movie.\n"
        "2. Show valid theaters.\n"
        "3. Show valid showtimes.\n"
        "4. Offer seats seat_1 to seat_10.\n"
        "5. Confirm booking with details.\n\n"
        "Here is the movie database:\n"
        f"{json.dumps(MOVIE_DATA, indent=2)}\n\n"
        "Use only this data. Mention fun movie facts where helpful. Don't invent anything."
    )

def get_booking_state_prompt():
    state = session.get("booking_state", {})
    parts = []
    if state.get("movie"):
        parts.append(f"Movie: {state['movie']}")
    if state.get("theater"):
        parts.append(f"Theater: {state['theater']}")
    if state.get("showtime"):
        parts.append(f"Showtime: {state['showtime']}")
    if state.get("seat"):
        parts.append(f"Seat: {state['seat']}")
    if state.get("confirmed"):
        parts.append("User has confirmed these details.")
    return "Current booking details: " + ", ".join(parts) if parts else ""

def markdown_to_html(md_text):
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\\1</b>", md_text)
    html = html.replace("\n", "<br>")
    return html

def save_booking_to_dynamodb(booking_state):
    if not booking_state.get("movie") or not booking_state.get("seat"):
        return False
    try:
        booking_id = str(uuid.uuid4())
        item = {
            "movie_name": booking_state["movie"],
            "booking_id": booking_id,
            "theater": booking_state.get("theater", "Unknown"),
            "showtime": booking_state.get("showtime", "Unknown"),
            "seat": booking_state.get("seat", "Unknown"),
            "timestamp": datetime.utcnow().isoformat(),
            "user_session_id": session.get("user_id", str(uuid.uuid4()))
        }
        session["user_id"] = item["user_session_id"]
        table.put_item(Item=item)
        return True
    except Exception as e:
        print(f"⚠️ DynamoDB Error: {e}")
        return False

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message", "").strip()

    if "history" not in session:
        session["history"] = []
    if "booking_state" not in session:
        session["booking_state"] = {
            "movie": None,
            "theater": None,
            "showtime": None,
            "seat": None,
            "confirmed": False
        }

    booking_state = session["booking_state"]
    user_lower = user_input.lower()

    if user_input.startswith("SEAT_CLICKED:"):
        seat = user_input.split("SEAT_CLICKED:")[1].strip()
        booking_state["seat"] = seat
        booking_state["confirmed"] = False
        session["booking_state"] = booking_state

    if user_lower in ["yes", "confirm", "confirm booking", "book", "yes book"]:
        booking_state["confirmed"] = True

        if not booking_state.get("movie"):
            for msg in reversed(session["history"]):
                if msg["role"] == "assistant" and "Movie:" in msg["content"]:
                    match = re.search(r"Movie:\s*(.+)", msg["content"])
                    if match:
                        booking_state["movie"] = match.group(1).split("<")[0].strip()
                        break

        # Backfill theater and showtime from assistant messages if missing
        for msg in reversed(session["history"]):
            if msg["role"] == "assistant":
                if not booking_state.get("theater"):
                    theater_match = re.search(r"Theater:\s*(.+)", msg["content"])
                    if theater_match:
                        booking_state["theater"] = theater_match.group(1).split("<")[0].strip()
                if not booking_state.get("showtime"):
                    showtime_match = re.search(r"Showtime:\s*(.+)", msg["content"])
                    if showtime_match:
                        booking_state["showtime"] = showtime_match.group(1).split("<")[0].strip()
                if booking_state.get("theater") and booking_state.get("showtime"):
                    break

        missing = []
        if not booking_state.get("movie"):
            missing.append("movie")
        if not booking_state.get("seat"):
            missing.append("seat")

        if missing:
            return jsonify({"response": f"Booking info incomplete. Missing: {', '.join(missing)}."})

        if save_booking_to_dynamodb(booking_state):
            confirmation_msg = (
                f"<b>Booking Confirmed!</b><br>"
                f"Movie: {booking_state['movie']}<br>"
                f"Theater: {booking_state.get('theater', 'Unknown')}<br>"
                f"Showtime: {booking_state.get('showtime', 'Unknown')}<br>"
                f"Seat: {booking_state.get('seat', 'Unknown')}<br>"
                "Your ticket has been booked successfully! 🎉"
            )
            session["history"].append({"role": "assistant", "content": confirmation_msg})
            session["booking_state"] = {"movie": None, "theater": None, "showtime": None, "seat": None, "confirmed": False}
            return jsonify({"response": confirmation_msg})
        else:
            return jsonify({"response": "Sorry, booking failed. Please try again."})

    if any(w in user_lower for w in ["change", "edit", "no"]):
        booking_state["confirmed"] = False

    for movie in MOVIE_DATA:
        if isinstance(movie, dict) and movie.get("title", "").lower() in user_lower:
            booking_state["movie"] = movie["title"]
            booking_state["confirmed"] = False
            break

    for movie in MOVIE_DATA:
        if isinstance(movie, dict) and movie.get("title") == booking_state.get("movie"):
            for theater in movie.get("theaters", []):
                if theater.lower() in user_lower:
                    booking_state["theater"] = theater
                    break
            for st in movie.get("showtimes", []):
                if st.lower() in user_lower:
                    booking_state["showtime"] = st
                    break
            break

    if not booking_state.get("seat"):
        seat_match = re.search(r"seat[_\s]?(\d+)", user_lower)
        if seat_match:
            booking_state["seat"] = f"seat_{seat_match.group(1)}"
            booking_state["confirmed"] = False

    session["booking_state"] = booking_state
    session["history"].append({"role": "user", "content": user_input})

    system_msgs = [{"text": get_system_prompt()}]
    booking_msg = get_booking_state_prompt()
    if booking_msg:
        system_msgs.append({"text": booking_msg})

    payload = {
        "system": system_msgs,
        "messages": [
            {"role": m["role"], "content": [{"text": m["content"]}]} 
            for m in session["history"][-10:] if m["role"] in ("user", "assistant")
        ]
    }

    if not payload["messages"] or payload["messages"][0]["role"] != "user":
        payload["messages"].insert(0, {
            "role": "user",
            "content": [{"text": user_input or "Hello"}]
        })

    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload)
        )
        result = json.loads(response["body"].read().decode("utf-8"))
        content_list = result.get("output", {}).get("message", {}).get("content", [])
        assistant_message = markdown_to_html(content_list[0]["text"]) if content_list else "⚠️ Unexpected model response."
    except Exception as e:
        assistant_message = f"⚠️ Error from Bedrock: {e}"

    session["history"].append({"role": "assistant", "content": assistant_message})
    return jsonify({"response": assistant_message})

@app.route("/get_booked_seats", methods=["POST"])
def get_booked_seats():
    data = request.json or {}
    movie = data.get("movie")
    theater = data.get("theater")
    showtime = data.get("showtime")

    if not movie or not theater or not showtime:
        return jsonify({"bookedSeats": [], "error": "Missing data"})

    try:
        response = table.scan(
            FilterExpression=(Attr("movie_name").eq(movie) & Attr("theater").eq(theater) & Attr("showtime").eq(showtime))
        )
        items = response.get("Items", [])
        return jsonify({"bookedSeats": [i.get("seat") for i in items if i.get("seat")]})
    except Exception as e:
        return jsonify({"bookedSeats": [], "error": str(e)})

@app.route("/show_bookings", methods=["GET"])
def show_bookings():
    movie_name = session.get("booking_state", {}).get("movie")
    if not movie_name:
        return jsonify({"response": "No movie selected."})

    try:
        response = table.query(KeyConditionExpression=Key("movie_name").eq(movie_name))
        items = response.get("Items", [])
        if not items:
            return jsonify({"response": f"No bookings found for <b>{movie_name}</b>."})
        html = f"<b>Bookings for {movie_name}:</b><br><ul>"
        for item in items:
            html += f"<li>Theater: {item.get('theater')}, Showtime: {item.get('showtime')}, Seat: {item.get('seat')}</li>"
        html += "</ul>"
        return jsonify({"response": html})
    except Exception as e:
        return jsonify({"response": f"⚠️ Error: {e}"})

if __name__ == "__main__":
    app.run(debug=True)
