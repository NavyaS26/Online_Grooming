from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO, emit, join_room
import re, json, smtplib, random, threading, requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
app.config["SECRET_KEY"] = "safeguard-secret-key"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ═══════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════
SMTP_EMAIL    = "your_email@gmail.com"
SMTP_PASSWORD = "your_app_password"
PARENT_EMAIL  = "parent_email@gmail.com"

# Groq — FREE. Get key at console.groq.com (no credit card needed)
GROQ_API_KEY = "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"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

# ═══════════════════════════════════════════════
#  ROOM STATE
# ═══════════════════════════════════════════════
ROOM              = "safeguard-room"
users             = {}
room_risk         = 0.0
room_history      = []
room_alerted      = False
room_sandbox      = False       # True when risk >= 0.80
sandbox_msg_count = 0
WIND_DOWN_AFTER   = 10          # terminate after 10 messages in sandbox

child_sid    = None
predator_sid = None

# ═══════════════════════════════════════════════
#  GROOMING PATTERNS
# ═══════════════════════════════════════════════
PATTERNS = [
    (r"don'?t tell (your )?(parents?|mom|dad|anyone|anybody)", 0.45, "Secrecy request"),
    (r"keep (this|it|our|a) secret",                           0.45, "Secrecy request"),
    (r"just between (us|you and me)",                          0.40, "Secrecy request"),
    (r"our (little )?secret",                                  0.45, "Secrecy request"),
    (r"don'?t show (anyone|your parents?|anybody)",            0.40, "Secrecy request"),
    (r"delete (this|the messages?|the chat)",                  0.40, "Evidence destruction"),
    (r"how old are you",                                       0.30, "Age probing"),
    (r"where do you live",                                     0.30, "Location probing"),
    (r"what'?s your address",                                  0.35, "Location probing"),
    (r"are you alone",                                         0.35, "Isolation check"),
    (r"home alone",                                            0.32, "Isolation check"),
    (r"do your parents? (check|watch|monitor|know)",           0.38, "Parental monitoring probe"),
    (r"what school do you go to",                              0.28, "Personal info request"),
    (r"send (me )?(a )?(pic|photo|picture|image|selfie)",      0.42, "Image solicitation"),
    (r"show me (yourself|your face|your body)",                0.50, "Image solicitation"),
    (r"video call",                                            0.35, "Video request"),
    (r"meet (up|in person|somewhere)",                         0.45, "Meeting request"),
    (r"come (over|to my|meet me)",                             0.45, "Meeting request"),
    (r"i('ll)? pick you up",                                   0.50, "Meeting request"),
    (r"you'?re so (mature|grown up|special|different)",        0.35, "Manipulation"),
    (r"you can trust me",                                      0.32, "Trust building"),
    (r"i'?m your (friend|boyfriend|girlfriend)",               0.30, "Relationship grooming"),
    (r"no one understands you like i do",                      0.40, "Isolation manipulation"),
    (r"take off|undress|without clothes",                      0.75, "Explicit content"),
    (r"touch yourself",                                        0.85, "Explicit content"),
    (r"sexy|hot body",                                         0.55, "Explicit content"),
    (r"don'?t be scared|it'?s normal|everyone does it",        0.45, "Normalizing behavior"),
]

KEYWORDS = {
    "secret": 0.20, "alone": 0.18, "picture": 0.20, "photo": 0.20,
    "selfie": 0.22, "meet": 0.22, "address": 0.25, "trust": 0.15,
    "delete": 0.22, "hide": 0.20, "private": 0.18, "undress": 0.60,
    "sexy": 0.45, "scared": 0.15,
}

# ═══════════════════════════════════════════════
#  ML MODEL (optional)
# ═══════════════════════════════════════════════
MODEL_LOADED = False
try:
    from safeguard_detector import load_model, analyze_message as ml_analyze
    load_model()
    MODEL_LOADED = True
    print("[SafeGuard] ML model loaded")
except Exception as e:
    print(f"[SafeGuard] Rule-based mode ({e})")

# ═══════════════════════════════════════════════
#  SCORE MESSAGE
# ═══════════════════════════════════════════════
def score_message(text):
    flags = []
    if MODEL_LOADED:
        try:
            result    = ml_analyze(text)
            score     = result["score"]
            breakdown = result["breakdown"]
            if breakdown["text_score"]  > 0.4: flags.append("Suspicious language")
            if breakdown["emoji_score"] > 0.4: flags.append("Suspicious emoji")
            return round(score, 3), flags
        except Exception as e:
            print(f"[ML ERROR] {e}")
    t = text.lower()
    score = 0.0
    for pattern, weight, label in PATTERNS:
        if re.search(pattern, t):
            score += weight
            flags.append(label)
    for word in re.findall(r'\b\w+\b', t):
        if word in KEYWORDS:
            score += KEYWORDS[word]
    if datetime.now().hour >= 22 or datetime.now().hour <= 5:
        score *= 1.15
    return min(round(score, 3), 1.0), list(set(flags))

def get_stage(risk):
    if risk < 0.30: return "Friendly Interaction"
    if risk < 0.50: return "Personal Info Requests"
    if risk < 0.65: return "Isolation Tactics"
    if risk < 0.80: return "Emotional Manipulation"
    return "Exploitation Attempt"

def update_risk(msg_score):
    global room_risk
    room_risk = min(round(room_risk + msg_score * max(0.5, 1.0 - len(room_history) * 0.02), 3), 1.0)
    return room_risk

# ═══════════════════════════════════════════════
#  GROQ LLM
#  Two roles:
#  "child"   — AI replies to predator AS the child (deflects)
#  "predator"— AI replies to child AS predator (neutral, winding down)
# ═══════════════════════════════════════════════

CHILD_SYSTEM = """You are a 14-year-old teenager replying in a chat. Be casual — use "lol", "idk", "umm", "haha", emojis sometimes. Max 2 short sentences like real texting.

When the other person asks specific things, reply specifically:
- where do you live / address / city → "lol my parents always say don't share that with people online" or "idk why you need to know that 😅"
- how old are you → "why do you ask lol" or "old enough ig"
- are you alone / home alone → "nah my mom is literally right here" or "no my whole family is home lol"
- send a pic / photo / selfie → "lol no I don't send pics to people I don't know" or "umm no that's weird"
- don't tell parents / keep secret / delete → "lol why would I keep stuff from my parents that's so weird"
- meet up / come over / pick you up → "lol my parents would never let me meet people from online"
- video call / facetime → "lol my wifi is terrible rn" or "I don't do video calls with people I just met"
- I love you / be my gf/bf → "lol we just met 😂 that's so random"
- anything sexual / explicit → "ew wtf no that's disgusting" then change topic
- your number / whatsapp / snap → "lol I don't give my number to strangers"
- trust me / I'm safe → "lol okay if you say so 😅"
For anything else just reply naturally as a normal teen. Never say you are an AI."""

PREDATOR_SYSTEM = """You are a friendly person having a completely normal casual conversation with a teenager.
Reply warmly and naturally to whatever they say — like a normal friendly chat between people.
Talk about everyday things: school, hobbies, music, games, food, weekend plans, movies, sports.
Be positive, supportive and age-appropriate at all times.
Never say anything suspicious, romantic, personal, or uncomfortable.
Keep replies short — 1 to 2 sentences. Sound like a real friendly person."""

def call_groq(system, user_msg):
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       GROQ_MODEL,
                "max_tokens":  80,
                "temperature": 0.85,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_msg},
                ]
            },
            timeout=10
        )
        reply = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"[GROQ] {reply}")
        return reply
    except Exception as e:
        print(f"[GROQ ERROR] {e}")
        return None

def build_history():
    lines = ""
    for msg in room_history[-20:]:
        label = "Teen" if msg["role"] == "child" else "Other person"
        lines += f"{label}: {msg['text']}\n"
    return lines

def ai_as_child(predator_msg):
    """AI replies to predator pretending to be the child."""
    prompt = f"""Conversation so far:
{build_history()}
Other person just said: "{predator_msg}"
Reply as the teenager. Address exactly what they said. 1-2 sentences max."""
    reply = call_groq(CHILD_SYSTEM, prompt)
    return reply or smart_fallback(predator_msg)

def ai_as_predator(child_msg):
    """AI replies to child as a friendly normal person — safe, warm, age-appropriate."""
    prompt = f"""Conversation so far:
{build_history()}
Teen just said: "{child_msg}"
Reply naturally and specifically to what they said. Be friendly and casual. 1-2 sentences."""
    reply = call_groq(PREDATOR_SYSTEM, prompt)
    return reply or random.choice([
        "haha yeah that's so true lol",
        "omg same 😂",
        "lol that's so funny",
        "haha nice, sounds fun!",
        "lol yeah I get that",
        "that's cool!",
    ])

def smart_fallback(text):
    """Specific fallback if Groq fails — never generic."""
    t = text.lower()
    if re.search(r"how old|your age|age are you", t):
        return random.choice(["why do you ask lol", "old enough ig 😅"])
    if re.search(r"where.*live|your address|which city|your city|near me", t):
        return random.choice(["lol my parents say don't share that online", "idk why you need to know that 😅"])
    if re.search(r"home alone|are you alone|parents home|anyone home", t):
        return random.choice(["nah my mom is literally right here lol", "no my whole family is home why"])
    if re.search(r"send.*pic|send.*photo|selfie|show me|picture of you", t):
        return random.choice(["lol no I don't send pics to people online", "umm no that's weird I barely know you"])
    if re.search(r"don'?t tell|keep.*secret|delete|just between us", t):
        return random.choice(["lol why would I keep secrets from my parents that's weird", "umm I don't hide stuff from my family"])
    if re.search(r"meet up|come over|pick you up|meet in person", t):
        return random.choice(["lol my parents would never let me meet people from online", "nah I don't do that"])
    if re.search(r"video call|facetime|on camera", t):
        return random.choice(["lol my wifi is so bad rn", "I don't do video calls with people I just met"])
    if re.search(r"i love you|be my girlfriend|be my boyfriend|date me", t):
        return random.choice(["lol we literally just met 😂 that's random", "umm okay that came out of nowhere"])
    if re.search(r"sexy|naked|undress|touch|explicit|nude", t):
        return random.choice(["ew wtf no that's disgusting", "that's so gross I don't wanna talk about that"])
    if re.search(r"your number|whatsapp|snapchat|instagram", t):
        return random.choice(["lol I don't give my number to people I don't know", "nah I'm good on here"])
    return random.choice(["lol idk", "umm okay 😅", "haha what do you mean", "idk why are you asking that"])

# ═══════════════════════════════════════════════
#  WIND-DOWN — terminates chat naturally
# ═══════════════════════════════════════════════
def run_wind_down():
    import time
    print("[SANDBOX] Wind-down starting...")
    child_user    = users.get(child_sid, {})
    predator_user = users.get(predator_sid, {})

    # AI (as child) tells predator it's leaving
    for i, msg in enumerate(["lol hey I gotta go now", "bye take care"]):
        time.sleep(3 + i * 3)
        if predator_sid:
            socketio.emit("message", {
                "name": child_user.get("name",""), "avatar": child_user.get("avatar","🐼"),
                "text": msg, "sid": child_sid, "role": "child",
                "risk": room_risk, "msg_score": 0,
                "stage": get_stage(room_risk), "flags": [], "flagged": False,
            }, room=predator_sid)

    # AI (as predator) tells child it's leaving
    for i, msg in enumerate(["lol yeah I gotta go too", "bye"]):
        time.sleep(2 + i * 2)
        if child_sid:
            socketio.emit("message", {
                "name": predator_user.get("name",""), "avatar": predator_user.get("avatar","🐼"),
                "text": msg, "sid": predator_sid, "role": "predator",
                "risk": room_risk, "msg_score": 0,
                "stage": get_stage(room_risk), "flags": [], "flagged": False,
            }, room=child_sid)

    # Fire termination to both sides
    time.sleep(3)
    socketio.emit("chat_terminated", {
        "risk":  room_risk,
        "stage": get_stage(room_risk),
    }, room=ROOM)
    print("[SANDBOX] Chat terminated.")

# ═══════════════════════════════════════════════
#  EMAIL + EVIDENCE
# ═══════════════════════════════════════════════
def send_alert(risk, stage):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "SafeGuard Alert — Immediate Attention Needed"
        msg["From"]    = SMTP_EMAIL
        msg["To"]      = PARENT_EMAIL
        body = f"""
A potential online grooming situation has been detected on your child's device.

Risk Level : {round(risk * 100)}%
Stage      : {stage}
Time       : {datetime.now().strftime('%Y-%m-%d %H:%M')}

Please take action immediately:
CHILDLINE            : 1098
Cybercrime Portal    : cybercrime.gov.in
National Cyber Crime : 1930

No conversation content is included to protect your child's privacy.
— SafeGuard AI
        """
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(SMTP_EMAIL, SMTP_PASSWORD)
            s.sendmail(SMTP_EMAIL, PARENT_EMAIL, msg.as_string())
        print("[EMAIL] Alert sent")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")

def save_evidence():
    path = f"evidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "risk_score":   room_risk,
            "stage":        get_stage(room_risk),
            "conversation": room_history,
        }, f, indent=2)
    print(f"[EVIDENCE] Saved → {path}")

# ═══════════════════════════════════════════════
#  ROUTE
# ═══════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory("templates", "chat.html")

# ═══════════════════════════════════════════════
#  SOCKET EVENTS
# ═══════════════════════════════════════════════
@socketio.on("join")
def on_join(data):
    global child_sid, predator_sid
    sid    = request.sid
    name   = data.get("name", "User")
    avatar = data.get("avatar", "🐼")
    if child_sid is None:
        role = "child"; child_sid = sid
    else:
        role = "predator"; predator_sid = sid
    users[sid] = {"name": name, "avatar": avatar, "role": role}
    join_room(ROOM)
    count    = len(users)
    existing = [{"name": u["name"], "avatar": u["avatar"]} for s, u in users.items() if s != sid]
    emit("user_joined", {"name": name, "avatar": avatar, "count": count, "role": role}, to=ROOM, skip_sid=sid)
    emit("room_info",   {"count": count, "existing_users": existing, "my_role": role})
    print(f"[+] {name} joined as {role}")


@socketio.on("message")
def on_message(data):
    global room_alerted, room_sandbox, sandbox_msg_count
    sid  = request.sid
    user = users.get(sid, {})
    name = user.get("name", "?")
    role = user.get("role", "unknown")
    text = data.get("text", "")

    msg_score, flags = score_message(text)
    risk  = update_risk(msg_score) if role == "predator" else room_risk
    stage = get_stage(risk)

    room_history.append({
        "sender": name, "role": role, "text": text,
        "score": msg_score, "flags": flags,
        "ts": datetime.now().isoformat()
    })

    payload = {
        "name": name, "avatar": user.get("avatar", "🐼"),
        "text": text, "sid": sid, "risk": risk,
        "msg_score": msg_score, "stage": stage,
        "flags": flags, "role": role,
    }

    child_user    = users.get(child_sid,    {})
    predator_user = users.get(predator_sid, {})

    # ═══════════════════════════════════════
    #  SANDBOX ACTIVE (risk >= 0.80)
    #  — Predator gets AI reply as child
    #  — Child gets AI reply as predator (winding down)
    #  — Neither sees the other's real messages
    # ═══════════════════════════════════════
    if room_sandbox:
        sandbox_msg_count += 1

        if role == "predator":
            # Predator sees own message
            emit("message", {**payload, "flagged": False, "flags": []}, to=predator_sid)
            # Child sees NOTHING from predator
            # AI replies to predator AS child in background thread
            def send_to_predator():
                reply = ai_as_child(text)
                socketio.emit("message", {
                    "name": child_user.get("name",""), "avatar": child_user.get("avatar","🐼"),
                    "text": reply, "sid": child_sid,
                    "risk": risk, "msg_score": 0, "stage": stage,
                    "flags": [], "role": "child", "flagged": False,
                }, room=predator_sid)
                print(f"[SANDBOX] AI→predator: '{reply}'")
            threading.Thread(target=send_to_predator, daemon=True).start()

        elif role == "child":
            # Child sees own message
            emit("message", {**payload, "flagged": False}, to=child_sid)
            # Predator does NOT receive child's real message
            # AI replies to child AS predator (winding down) in background thread
            def send_to_child():
                reply = ai_as_predator(text)
                socketio.emit("message", {
                    "name": predator_user.get("name",""), "avatar": predator_user.get("avatar","🐼"),
                    "text": reply, "sid": predator_sid,
                    "risk": risk, "msg_score": 0, "stage": stage,
                    "flags": [], "role": "predator", "flagged": False,
                }, room=child_sid)
                print(f"[SANDBOX] AI→child: '{reply}'")
            threading.Thread(target=send_to_child, daemon=True).start()

        # After WIND_DOWN_AFTER messages — terminate
        if sandbox_msg_count == WIND_DOWN_AFTER:
            threading.Thread(target=run_wind_down, daemon=True).start()

        # Update child risk bar
        if child_sid:
            emit("risk_bar", {"risk": risk, "stage": stage}, to=child_sid)
        return

    # ═══════════════════════════════════════
    #  NORMAL ROUTING (risk < 0.80)
    # ═══════════════════════════════════════
    if role == "predator":
        # Predator sees own message — no flags
        emit("message", {**payload, "flagged": False, "flags": []}, to=predator_sid)
        # Child sees predator message with flags
        if child_sid:
            emit("message", {**payload, "flagged": len(flags) > 0}, to=child_sid)

        # Activate sandbox at 0.80
        if risk >= 0.80:
            room_sandbox = True
            save_evidence()
            if not room_alerted:
                send_alert(risk, stage)
                room_alerted = True
            if child_sid:
                emit("sandbox_activated", {"risk": risk, "stage": stage}, to=child_sid)
            print(f"[SANDBOX] ACTIVATED | Risk={risk}")
        elif risk >= 0.60 and child_sid:
            emit("risk_update", {"risk": risk, "stage": stage, "level": "medium"}, to=child_sid)

        if child_sid:
            emit("risk_bar", {"risk": risk, "stage": stage}, to=child_sid)

    elif role == "child":
        emit("message", {**payload, "flagged": False}, to=child_sid)
        if predator_sid:
            emit("message", {**payload, "flagged": False}, to=predator_sid)

    print(f"[MSG] {name}({role}): '{text[:50]}' | score={msg_score} | risk={risk} | sandbox={room_sandbox}")


@socketio.on("image")
def on_image(data):
    global room_alerted, room_sandbox, sandbox_msg_count
    sid   = request.sid
    user  = users.get(sid, {})
    role  = user.get("role", "unknown")
    risk  = update_risk(0.25) if role == "predator" else room_risk
    stage = get_stage(risk)

    room_history.append({
        "sender": user.get("name","?"), "role": role,
        "text": "[IMAGE]", "score": 0.25,
        "flags": ["Image sent"], "ts": datetime.now().isoformat()
    })

    payload = {
        "name": user.get("name","?"), "avatar": user.get("avatar","🐼"),
        "image": data.get("image",""), "filename": data.get("filename","img"),
        "sid": sid, "risk": risk, "role": role,
    }

    child_user = users.get(child_sid, {})

    if role == "predator":
        emit("image", {**payload, "flagged": False}, to=predator_sid)
        if room_sandbox:
            def send_img_reply():
                reply = ai_as_child("[image sent]")
                socketio.emit("message", {
                    "name": child_user.get("name",""), "avatar": child_user.get("avatar","🐼"),
                    "text": reply, "sid": child_sid,
                    "risk": risk, "msg_score": 0, "stage": stage,
                    "flags": [], "role": "child", "flagged": False,
                }, room=predator_sid)
            threading.Thread(target=send_img_reply, daemon=True).start()
        else:
            if child_sid:
                emit("image", {**payload, "flagged": True}, to=child_sid)
            if risk >= 0.80:
                room_sandbox = True
                save_evidence()
                if not room_alerted:
                    send_alert(risk, stage)
                    room_alerted = True
                if child_sid:
                    emit("sandbox_activated", {"risk": risk, "stage": stage}, to=child_sid)
            elif risk >= 0.60 and child_sid:
                emit("risk_update", {"risk": risk, "stage": stage, "level": "medium"}, to=child_sid)
        if child_sid:
            emit("risk_bar", {"risk": risk, "stage": stage}, to=child_sid)

    elif role == "child":
        emit("image", {**payload, "flagged": False}, to=child_sid)
        if predator_sid:
            emit("image", {**payload, "flagged": False}, to=predator_sid)


@socketio.on("typing")
def on_typing(data):
    sid  = request.sid
    user = users.get(sid, {})
    emit("typing", {"name": user.get("name",""), "typing": data.get("typing", False)}, to=ROOM, skip_sid=sid)


@socketio.on("disconnect")
def on_disconnect():
    global child_sid, predator_sid
    sid  = request.sid
    user = users.pop(sid, {})
    if user:
        if sid == child_sid:    child_sid    = None
        if sid == predator_sid: predator_sid = None
        emit("user_left", {"name": user.get("name","")}, to=ROOM)
        print(f"[-] {user.get('name')} ({user.get('role')}) left")


if __name__ == "__main__":
    print("=" * 50)
    print("  SafeGuard — Chat + Detection Server")
    print("  http://0.0.0.0:3000")
    print("  First to join = CHILD | Second = PREDATOR")
    print("=" * 50)
    socketio.run(app, host="0.0.0.0", port=3000, debug=True)
