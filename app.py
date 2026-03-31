"""
============================================================
Fingerprint-Based Voting System — Flask Application
============================================================
Routes:
  GET  /                  → Home / dashboard
  GET  /register          → Registration page
  POST /api/register      → Save voter + fingerprint
  POST /api/scan/enroll   → Trigger Arduino enrollment
  GET  /vote              → Voting panel page
  POST /api/verify-aadhar → Check aadhar exists
  POST /api/scan/verify   → Trigger Arduino verification
  POST /api/cast-vote     → Record vote
  GET  /api/results       → Live vote tally (JSON)
  GET  /api/status        → Arduino serial status (JSON)
============================================================
"""

import os
import re
import time
import threading
import serial
import serial.tools.list_ports
import mysql.connector
from mysql.connector import Error as MySQLError
from flask import Flask, render_template, request, jsonify, session
from dotenv import load_dotenv

load_dotenv()

# ── Flask setup ──────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fp_voting_secret_key_change_me")

# ── Database configuration ───────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     int(os.getenv("DB_PORT", "3306")),
    "user":     os.getenv("DB_USER",     "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME",     "fingerprint_voting"),
}

# ── Serial / Arduino state ───────────────────────────────────
arduino:       serial.Serial | None = None
serial_lock    = threading.Lock()
pending_result: str | None = None   # Latest response from Arduino
arduino_status = "disconnected"     # "connected" | "disconnected" | "error"


# ============================================================
# Database helpers
# ============================================================
def get_db():
    """Return a fresh MySQL connection."""
    return mysql.connector.connect(**DB_CONFIG)


def db_query(sql: str, params: tuple = (), fetch: bool = True):
    """
    Execute a query and optionally return all rows.
    Raises RuntimeError on DB error.
    """
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        if fetch:
            result = cur.fetchall()
            return result
        conn.commit()
        return cur.lastrowid
    except MySQLError as exc:
        raise RuntimeError(f"Database error: {exc}") from exc
    finally:
        conn.close()


# ============================================================
# Arduino / Serial helpers
# ============================================================
def find_arduino_port() -> str | None:
    """Auto-detect the Arduino serial port."""
    for port in serial.tools.list_ports.comports():
        desc = port.description.lower()
        if any(k in desc for k in ("arduino", "ch340", "cp210", "usb serial")):
            return port.device
    return None


def connect_arduino():
    """Open serial connection to Arduino. Called once at startup."""
    global arduino, arduino_status
    port = os.getenv("ARDUINO_PORT") or find_arduino_port()
    if not port:
        arduino_status = "disconnected"
        return False
    try:
        arduino = serial.Serial(port, 57600, timeout=30)
        time.sleep(2)                       # Wait for Arduino reset
        arduino.flushInput()
        arduino_status = "connected"
        # Start background reader thread
        t = threading.Thread(target=_serial_reader, daemon=True)
        t.start()
        return True
    except serial.SerialException as e:
        arduino_status = f"error: {e}"
        arduino = None
        return False


def _serial_reader():
    """Background thread: reads lines from Arduino and stores last result."""
    global pending_result, arduino_status
    while arduino and arduino.is_open:
        try:
            line = arduino.readline().decode("utf-8", errors="ignore").strip()
            if line:
                pending_result = line
        except serial.SerialException:
            arduino_status = "disconnected"
            break


def send_command(cmd: str, timeout: int = 30) -> str:
    """
    Send a command to Arduino and wait for OK/ERROR response.
    Returns the response line, or raises RuntimeError on timeout/disconnect.
    """
    global pending_result
    if not arduino or not arduino.is_open:
        raise RuntimeError("Arduino not connected")

    with serial_lock:
        pending_result = None
        arduino.write((cmd + "\n").encode("utf-8"))
        arduino.flush()

    # Poll for response (non-STATUS lines)
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = pending_result
        if resp and not resp.startswith("STATUS:"):
            return resp
        time.sleep(0.1)

    raise RuntimeError("Timeout waiting for Arduino response")


def next_fingerprint_id() -> int:
    """
    Return the lowest fingerprint ID (1–127) not yet in the database.
    Raises RuntimeError if all slots are used.
    """
    rows = db_query("SELECT fingerprint_id FROM voters ORDER BY fingerprint_id")
    used = {r["fingerprint_id"] for r in rows}
    for i in range(1, 128):
        if i not in used:
            return i
    raise RuntimeError("All 127 fingerprint slots are occupied")


# ============================================================
# Application startup
# ============================================================
with app.app_context():
    connect_arduino()


# ============================================================
# Page routes
# ============================================================
@app.route("/")
def index():
    return render_template("index.html", arduino_status=arduino_status)


@app.route("/register")
def register_page():
    return render_template("register.html")


@app.route("/vote")
def vote_page():
    candidates = db_query("SELECT candidate_name FROM votes ORDER BY id")
    return render_template("vote.html", candidates=candidates)


# ============================================================
# API — Registration
# ============================================================
@app.route("/api/scan/enroll", methods=["POST"])
def api_scan_enroll():
    """
    Step 1 of registration: instruct Arduino to enroll a fingerprint.
    Assigns the next available ID, returns it to the client on success.
    """
    try:
        fp_id = next_fingerprint_id()
        response = send_command(f"ENROLL:{fp_id}", timeout=60)

        if response.startswith("OK:"):
            confirmed_id = int(response.split(":")[1])
            # Store in session so /api/register can use it
            session["pending_fp_id"] = confirmed_id
            return jsonify({"success": True, "fingerprint_id": confirmed_id})
        else:
            msg = response.replace("ERROR:", "")
            return jsonify({"success": False, "error": msg}), 400

    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/register", methods=["POST"])
def api_register():
    """
    Step 2 of registration: save voter to database.
    Requires fingerprint to have been scanned first (session token).
    """
    data = request.get_json(force=True)
    name           = (data.get("name") or "").strip()
    aadhar_number  = (data.get("aadhar_number") or "").strip()
    fp_id          = session.get("pending_fp_id")

    # ── Validation ───────────────────────────────────────────
    errors = {}
    if not name:
        errors["name"] = "Name is required."
    if not re.fullmatch(r"\d{12}", aadhar_number):
        errors["aadhar_number"] = "Aadhar must be exactly 12 digits."
    if fp_id is None:
        errors["fingerprint"] = "Fingerprint scan not completed."
    if errors:
        return jsonify({"success": False, "errors": errors}), 422

    # ── Check duplicate Aadhar ───────────────────────────────
    existing = db_query(
        "SELECT id FROM voters WHERE aadhar_number = %s", (aadhar_number,)
    )
    if existing:
        return jsonify({
            "success": False,
            "error": "This Aadhar is already registered."
        }), 409

    # ── Insert voter ─────────────────────────────────────────
    try:
        db_query(
            "INSERT INTO voters (name, aadhar_number, fingerprint_id) VALUES (%s, %s, %s)",
            (name, aadhar_number, fp_id),
            fetch=False,
        )
        session.pop("pending_fp_id", None)
        return jsonify({"success": True, "message": "Registration Successful"})
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


# ============================================================
# API — Voting
# ============================================================
@app.route("/api/verify-aadhar", methods=["POST"])
def api_verify_aadhar():
    """Check that an Aadhar number is registered; store voter_id in session."""
    data = request.get_json(force=True)
    aadhar = (data.get("aadhar_number") or "").strip()

    rows = db_query(
        "SELECT id, name, fingerprint_id, has_voted FROM voters WHERE aadhar_number = %s",
        (aadhar,),
    )
    if not rows:
        return jsonify({"success": False, "error": "Aadhar number not registered."}), 404

    voter = rows[0]
    session["vote_voter_id"]      = voter["id"]
    session["vote_fingerprint_id"] = voter["fingerprint_id"]
    session["vote_has_voted"]      = voter["has_voted"]

    return jsonify({
        "success": True,
        "name":      voter["name"],
        "has_voted": bool(voter["has_voted"]),
    })


@app.route("/api/scan/verify", methods=["POST"])
def api_scan_verify():
    """
    Instruct Arduino to identify the placed fingerprint.
    Compare result against the expected fingerprint_id stored in session.
    """
    voter_id       = session.get("vote_voter_id")
    expected_fp_id = session.get("vote_fingerprint_id")

    if voter_id is None:
        return jsonify({"success": False, "error": "Aadhar not verified yet."}), 400

    try:
        response = send_command("VERIFY", timeout=30)
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    if response.startswith("OK:"):
        scanned_id = int(response.split(":")[1])
        if scanned_id != expected_fp_id:
            session.pop("vote_voter_id", None)
            return jsonify({"success": False, "error": "Fingerprint does not match."}), 403
        return jsonify({"success": True, "message": "Fingerprint verified."})
    else:
        msg = response.replace("ERROR:", "")
        return jsonify({"success": False, "error": msg}), 400


@app.route("/api/cast-vote", methods=["POST"])
def api_cast_vote():
    """Record the voter's choice; enforce one-vote-per-voter."""
    voter_id  = session.get("vote_voter_id")
    has_voted = session.get("vote_has_voted")

    if voter_id is None:
        return jsonify({"success": False, "error": "Session expired. Please start over."}), 400
    if has_voted:
        return jsonify({"success": False, "error": "You have already voted."}), 409

    data      = request.get_json(force=True)
    candidate = (data.get("candidate") or "").strip()

    # Validate candidate exists
    rows = db_query(
        "SELECT id FROM votes WHERE candidate_name = %s", (candidate,)
    )
    if not rows:
        return jsonify({"success": False, "error": "Invalid candidate."}), 400

    # ── Atomic update — vote count + has_voted in a transaction ──
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE votes SET vote_count = vote_count + 1 WHERE candidate_name = %s",
            (candidate,),
        )
        cur.execute(
            "UPDATE voters SET has_voted = TRUE WHERE id = %s",
            (voter_id,),
        )
        cur.execute(
            "INSERT INTO vote_audit (voter_id) VALUES (%s)",
            (voter_id,),
        )
        conn.commit()
    except MySQLError as exc:
        conn.rollback()
        return jsonify({"success": False, "error": f"Database error: {exc}"}), 500
    finally:
        conn.close()

    # Clear session
    session.pop("vote_voter_id",       None)
    session.pop("vote_fingerprint_id", None)
    session.pop("vote_has_voted",      None)

    return jsonify({"success": True, "message": "Vote Cast Successfully"})


# ============================================================
# API — Results & Status
# ============================================================
@app.route("/api/results")
def api_results():
    rows = db_query("SELECT candidate_name, vote_count FROM votes ORDER BY vote_count DESC")
    total = db_query("SELECT COUNT(*) AS c FROM voters WHERE has_voted = TRUE")[0]["c"]
    return jsonify({"candidates": rows, "total_votes": total})


@app.route("/api/status")
def api_status():
    return jsonify({
        "arduino": arduino_status,
        "port":    arduino.port if arduino and arduino.is_open else None,
    })


@app.route("/api/reconnect", methods=["POST"])
def api_reconnect():
    """Attempt to reconnect to Arduino (useful after hot-plug)."""
    ok = connect_arduino()
    return jsonify({"success": ok, "status": arduino_status})


# ============================================================
# Run
# ============================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
