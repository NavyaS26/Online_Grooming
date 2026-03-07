from gevent import monkey
monkey.patch_all()

from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO, emit, join_room
import re, json, smtplib, random, time
import gevent
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sys, os, requests
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
app.config["SECRET_KEY"] = "safeguard-secret-key"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

SMTP_EMAIL    = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
PARENT_EMAIL  = os.getenv("PARENT_EMAIL")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY")
GROQ_API_URL  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "llama-3.3-70b-versatile"

ROOM              = "safeguard-room"
users             = {}
room_risk         = 0.0
room_history      = []
room_sandbox      = False
sandbox_msg_count = 0
wind_down_started = False
MSG_SCORE_CAP     = 0.35
SANDBOX_THRESHOLD = 1.0
WIND_DOWN_AFTER   = 2
child_sid         = None
predator_sid      = None

SCORING_SYSTEM = """You are a child safety AI scoring system.

Your job: score a single message sent by an adult to a child.
Output how alarming THIS message is on its own, from 0.0 to 1.0.

Score 0.0  = completely normal ("how was school?", "what games do you play?")
Score 0.1  = mildly personal but common ("do you live nearby?", "are you home?")
Score 0.2  = slightly suspicious in context ("you seem really mature")
Score 0.3  = moderately suspicious ("are you alone right now?", "don't tell anyone")
Score 0.5  = clearly inappropriate ("you're so hot", "send me a pic")
Score 0.7  = strongly grooming ("send nudes", "come meet me", "home alone?")
Score 0.9+ = explicit sexual content ("sex", "nudes", "touch yourself", explicit body parts)

Key rules:
- "pictures" or "pics" alone in normal context = 0.1
- "send me your pictures" or "send pics of yourself" = 0.7
- "send nudes" / "nude pics" = 0.95
- "are you home" casually = 0.05. "are you alone, parents not there?" = 0.35
- Normal small talk MUST score below 0.1
- Context from prior messages matters

Also return which of these categories apply (only if score > 0.2):
secrecy, location, isolation, sexual, image_request, manipulation, meeting, normalization

Return ONLY valid JSON:
{"score": 0.0, "flags": [], "reasoning": "one sentence"}"""

REGEX_FALLBACK = [
    (r"don'?t tell (your )?(parents?|mom|dad|anyone)", 0.30, "Secrecy"),
    (r"keep (this|it|our|a) secret|just between us",   0.30, "Secrecy"),
    (r"delete (this|the messages?|the chat)",          0.25, "Secrecy"),
    (r"where do you live|what'?s your address",        0.22, "Location"),
    (r"are you alone|home alone|u alone|alone rn",     0.22, "Isolation"),
    (r"parents? (home|away|out)",                      0.20, "Isolation"),
    (r"send (me )?(your )?(nudes?|naked pics?)",       0.35, "Explicit"),
    (r"\bsex\b|have sex|wanna f+u+c+k+",               0.35, "Explicit"),
    (r"\bnaked\b|touch yourself|masturbat",             0.35, "Explicit"),
    (r"\bhorny\b|boner|erection|\bpussy\b|\bdick\b",   0.35, "Explicit"),
    (r"\btits\b|\bboobs?\b|\bcock\b",                  0.35, "Explicit"),
    (r"send (me )?(a )?(pic|photo|selfie) of (you|yourself)", 0.28, "Image request"),
    (r"show me (your body|yourself|ur body)",           0.28, "Image request"),
    (r"(go )?on cam|video call|facetime",              0.20, "Video request"),
    (r"meet (up|in person)|come (over|chill)|pick you up", 0.28, "Meeting"),
    (r"you'?re so (mature|special|hot|sexy)",          0.25, "Manipulation"),
    (r"everyone does it|it'?s normal|don'?t be scared", 0.25, "Normalizing"),
]

REGEX_KEYWORDS = {
    "nudes": 0.35, "nude": 0.30, "naked": 0.28, "sex": 0.30,
    "horny": 0.30, "porn": 0.30, "sexy": 0.22, "hottie": 0.22,
    "undress": 0.28, "strip": 0.25, "fetish": 0.25, "erotic": 0.25,
    "dtf": 0.32, "secret": 0.15, "alone": 0.12, "delete": 0.15,
    "meet": 0.15, "address": 0.20, "addy": 0.22,
}

def call_groq(system, user_msg, max_tokens=200, temperature=0.3):
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_msg},
                ]
            },
            timeout=10
        )
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[GROQ ERROR] {e}")
        return None

def semantic_score(text):
    context = ""
    for msg in room_history[-5:]:
        label = "CHILD" if msg["role"] == "child" else "ADULT"
        context += f"{label}: {msg['text']}\n"
    prompt = f'Prior context:\n{context or "(none)"}\n\nMessage to score: "{text}"\n\nReturn ONLY the JSON.'
    raw = call_groq(SCORING_SYSTEM, prompt, max_tokens=200, temperature=0.1)
    if raw:
        raw = re.sub(r"```json\s*|```\s*", "", raw).strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
                score  = float(result.get("score", 0.0))
                flags  = result.get("flags", [])
                reason = result.get("reasoning", "")
                score  = max(0.0, min(score, 1.0))
                print(f"[SCORE] '{text[:40]}' => {score:.2f} | {flags} | {reason}")
                return round(score, 3), [f.replace("_"," ").title() for f in flags if isinstance(f, str)]
            except (json.JSONDecodeError, ValueError) as e:
                print(f"[SCORE PARSE ERROR] {e}")
    print("[SCORE] Groq unavailable — regex fallback")
    score, flags, t = 0.0, [], text.lower()
    for pattern, weight, label in REGEX_FALLBACK:
        if re.search(pattern, t):
            score += weight
            flags.append(label)
    for word in re.findall(r'\b\w+\b', t):
        if word in REGEX_KEYWORDS:
            score += REGEX_KEYWORDS[word]
    if datetime.now().hour >= 22 or datetime.now().hour <= 5:
        score *= 1.2
    return min(round(score, 3), 1.0), list(set(flags))

def get_stage(risk):
    if risk < 0.25: return "Friendly Interaction"
    if risk < 0.50: return "Suspicious Behaviour"
    if risk < 0.75: return "Escalating Risk"
    if risk < 1.0:  return "High Risk"
    return "Exploitation Attempt"

def update_risk(msg_score):
    global room_risk
    contribution = min(msg_score, MSG_SCORE_CAP)
    room_risk = min(round(room_risk + contribution, 3), 1.0)
    return room_risk

CHILD_SYSTEM = """You are a 14-year-old teenager in a chat. Be casual. Max 2 short sentences.
- where do you live / address → "lol my parents say don't share that"
- how old are you → "why do you ask lol"
- are you alone / home alone → "nah my whole family is home lol"
- send pics / photos / nudes → "lol I don't send pics to people I don't know"
- anything sexual / explicit → "ew wtf no" then change topic
- don't tell parents / keep secret → "lol why would I hide stuff from my parents"
- meet up / come over → "lol my parents would never let me"
- video call / facetime → "lol my wifi is terrible rn"
- I love you / be my gf/bf → "lol we just met 😂"
- your number / snap → "lol I don't give my number to strangers"
Reply naturally as a teen for anything else. Never say you're an AI."""

PREDATOR_SYSTEM = """You are a friendly person having a casual conversation.
Talk about school, music, games, food, weekend plans. Be warm and age-appropriate.
1-2 short sentences. Start gently wrapping up the conversation."""

def build_history():
    lines = ""
    for msg in room_history[-20:]:
        label = "Teen" if msg["role"] == "child" else "Other"
        lines += f"{label}: {msg['text']}\n"
    return lines

def ai_as_child(predator_msg):
    prompt = f'Conversation:\n{build_history()}\nOther said: "{predator_msg}"\nReply as teen, 1-2 sentences.'
    return call_groq(CHILD_SYSTEM, prompt, max_tokens=80, temperature=0.85) or _child_fallback(predator_msg)

def ai_as_predator(child_msg):
    prompt = f'Conversation:\n{build_history()}\nTeen said: "{child_msg}"\nReply naturally, start winding down. 1-2 sentences.'
    return call_groq(PREDATOR_SYSTEM, prompt, max_tokens=80, temperature=0.85) or random.choice([
        "haha yeah lol", "omg same 😂", "lol nice!", "that's cool!", "lol true"
    ])

def _child_fallback(text):
    t = text.lower()
    if re.search(r"how old|your age",                  t): return "why do you ask lol"
    if re.search(r"where.*live|address|addy",          t): return "lol my parents say don't share that"
    if re.search(r"home alone|are you alone|alone rn", t): return "nah my whole family is home lol"
    if re.search(r"pic|photo|selfie|nudes?|pictures?", t): return "lol I don't send pics to strangers"
    if re.search(r"sex|sexy|hot|nude|body|naked",      t): return "ew wtf no"
    if re.search(r"secret|don't tell|delete",          t): return "lol why would I hide stuff from my parents"
    if re.search(r"meet|come over|pick you|hang",      t): return "lol my parents would never let me"
    if re.search(r"video call|facetime|on cam",        t): return "lol my wifi is so bad rn"
    if re.search(r"love|girlfriend|boyfriend",         t): return "lol we just met 😂"
    if re.search(r"number|whatsapp|snap|instagram",    t): return "lol I don't give my number to strangers"
    return random.choice(["lol idk", "umm okay 😅", "haha what", "lol why are you asking that"])

def run_wind_down():
    print("[SANDBOX] Wind-down starting...")
    c_sid  = child_sid
    p_sid  = predator_sid
    c_user = users.get(c_sid, {})
    p_user = users.get(p_sid, {})
    stage  = get_stage(room_risk)
    for i, msg in enumerate(["lol hey I gotta go now", "bye take care"]):
        gevent.sleep(3 + i * 3)
        if p_sid:
            socketio.emit("message", {
                "name": c_user.get("name",""), "avatar": c_user.get("avatar","🐼"),
                "text": msg, "sid": c_sid, "role": "child",
                "risk": room_risk, "msg_score": 0, "stage": stage,
                "flags": [], "flagged": False,
            }, to=p_sid)
    for i, msg in enumerate(["lol yeah I gotta go too", "bye"]):
        gevent.sleep(2 + i * 2)
        if c_sid:
            socketio.emit("message", {
                "name": p_user.get("name",""), "avatar": p_user.get("avatar","🐼"),
                "text": msg, "sid": p_sid, "role": "predator",
                "risk": room_risk, "msg_score": 0, "stage": stage,
                "flags": [], "flagged": False,
            }, to=c_sid)
    gevent.sleep(3)
    terminated_payload = {"risk": room_risk, "stage": stage}
    if c_sid: socketio.emit("chat_terminated", terminated_payload, to=c_sid)
    if p_sid: socketio.emit("chat_terminated", terminated_payload, to=p_sid)
    send_alert(room_risk, stage)
    print(f"[SANDBOX] Terminated.")

def send_alert(risk, stage):
    if not SMTP_EMAIL or not SMTP_PASSWORD or not PARENT_EMAIL:
        print("[EMAIL] Skipped — set SMTP_EMAIL, SMTP_PASSWORD, PARENT_EMAIL in .env")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "SafeGuard Alert — Immediate Attention Needed"
        msg["From"]    = SMTP_EMAIL
        msg["To"]      = PARENT_EMAIL
        body = (
            f"A potential online grooming situation was detected on your child's device.\n\n"
            f"Risk Level : {round(risk * 100)}%\n"
            f"Stage      : {stage}\n"
            f"Time       : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"Take action:\n"
            f"  CHILDLINE         : 1098\n"
            f"  Cybercrime Portal : cybercrime.gov.in\n"
            f"  National Helpline : 1930\n\n"
            f"No message content is included to protect your child's privacy.\n"
            f"— SafeGuard AI"
        )
        msg.attach(MIMEText(body, "plain"))
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
                s.login(SMTP_EMAIL, SMTP_PASSWORD)
                s.sendmail(SMTP_EMAIL, PARENT_EMAIL, msg.as_string())
        except Exception:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
                s.ehlo(); s.starttls()
                s.login(SMTP_EMAIL, SMTP_PASSWORD)
                s.sendmail(SMTP_EMAIL, PARENT_EMAIL, msg.as_string())
        print(f"[EMAIL] Sent to {PARENT_EMAIL}")
    except smtplib.SMTPAuthenticationError:
        print("[EMAIL ERROR] Auth failed — use a Gmail App Password")
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

@app.route("/")
def index():
    return send_from_directory("templates", "chat.html")

@socketio.on("join")
def on_join(data):
    global child_sid, predator_sid
    sid    = request.sid
    name   = data.get("name", "User")
    avatar = data.get("avatar", "🐼")
    if child_sid is None:
        role = "child";    child_sid    = sid
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
    global room_sandbox, sandbox_msg_count, wind_down_started
    sid  = request.sid
    user = users.get(sid, {})
    name = user.get("name", "?")
    role = user.get("role", "unknown")
    text = data.get("text", "")
    if role == "predator":
        msg_score, flags = semantic_score(text)
        risk = update_risk(msg_score)
    else:
        msg_score = 0.0; flags = []; risk = room_risk
    stage = get_stage(risk)
    room_history.append({"sender": name, "role": role, "text": text, "score": msg_score, "flags": flags, "ts": datetime.now().isoformat()})
    payload = {"name": name, "avatar": user.get("avatar","🐼"), "text": text, "sid": sid, "risk": risk, "msg_score": msg_score, "stage": stage, "flags": flags, "role": role}
    c_sid  = child_sid;  p_sid  = predator_sid
    c_user = dict(users.get(c_sid, {})); p_user = dict(users.get(p_sid, {}))

    if room_sandbox:
        sandbox_msg_count += 1
        if role == "predator":
            emit("message", {**payload, "flagged": False, "flags": []}, to=p_sid)
            captured_text = text
            def reply_to_predator():
                reply = ai_as_child(captured_text)
                socketio.emit("message", {"name": c_user.get("name",""), "avatar": c_user.get("avatar","🐼"), "text": reply, "sid": c_sid, "risk": risk, "msg_score": 0, "stage": stage, "flags": [], "role": "child", "flagged": False}, to=p_sid)
                print(f"[SANDBOX→predator] '{reply}'")
            gevent.spawn(reply_to_predator)
        elif role == "child":
            emit("message", {**payload, "flagged": False}, to=c_sid)
            captured_text = text
            def reply_to_child():
                reply = ai_as_predator(captured_text)
                socketio.emit("message", {"name": p_user.get("name",""), "avatar": p_user.get("avatar","🐼"), "text": reply, "sid": p_sid, "risk": risk, "msg_score": 0, "stage": stage, "flags": [], "role": "predator", "flagged": False}, to=c_sid)
                print(f"[SANDBOX→child] '{reply}'")
            gevent.spawn(reply_to_child)
        if sandbox_msg_count >= WIND_DOWN_AFTER and not wind_down_started:
            wind_down_started = True
            gevent.spawn(run_wind_down)
        if c_sid: emit("risk_bar", {"risk": risk, "stage": stage}, to=c_sid)
        return

    if role == "predator":
        emit("message", {**payload, "flagged": False, "flags": []}, to=p_sid)
        if c_sid: emit("message", {**payload, "flagged": len(flags) > 0}, to=c_sid)
        if risk >= SANDBOX_THRESHOLD:
            room_sandbox = True; save_evidence()
            if c_sid: emit("sandbox_activated", {"risk": risk, "stage": stage}, to=c_sid)
            print(f"[SANDBOX ACTIVATED] risk=1.000")
        elif risk >= 0.75 and c_sid: emit("risk_update", {"risk": risk, "stage": stage, "level": "high"}, to=c_sid)
        elif risk >= 0.50 and c_sid: emit("risk_update", {"risk": risk, "stage": stage, "level": "medium"}, to=c_sid)
        if c_sid: emit("risk_bar", {"risk": risk, "stage": stage}, to=c_sid)
    elif role == "child":
        emit("message", {**payload, "flagged": False}, to=c_sid)
        if p_sid: emit("message", {**payload, "flagged": False}, to=p_sid)
    print(f"[MSG] {name}({role}): '{text[:50]}' | score={msg_score:.2f} | risk={risk:.3f} | sandbox={room_sandbox}")

@socketio.on("image")
def on_image(data):
    global room_sandbox, sandbox_msg_count, wind_down_started
    sid  = request.sid; user = users.get(sid, {}); role = user.get("role", "unknown")
    c_sid = child_sid; p_sid = predator_sid; c_user = dict(users.get(c_sid, {}))
    if role == "predator":
        risk = update_risk(0.25); stage = get_stage(risk)
        room_history.append({"sender": user.get("name","?"), "role": role, "text": "[IMAGE]", "score": 0.25, "flags": ["Image sent"], "ts": datetime.now().isoformat()})
        payload = {"name": user.get("name","?"), "avatar": user.get("avatar","🐼"), "image": data.get("image",""), "filename": data.get("filename","img"), "sid": sid, "risk": risk, "role": role}
        emit("image", {**payload, "flagged": False}, to=p_sid)
        if room_sandbox:
            sandbox_msg_count += 1
            def img_reply():
                reply = ai_as_child("[someone sent me an image]")
                socketio.emit("message", {"name": c_user.get("name",""), "avatar": c_user.get("avatar","🐼"), "text": reply, "sid": c_sid, "risk": risk, "msg_score": 0, "stage": stage, "flags": [], "role": "child", "flagged": False}, to=p_sid)
            gevent.spawn(img_reply)
            if sandbox_msg_count >= WIND_DOWN_AFTER and not wind_down_started:
                wind_down_started = True; gevent.spawn(run_wind_down)
        else:
            if c_sid: emit("image", {**payload, "flagged": True}, to=c_sid)
            if risk >= SANDBOX_THRESHOLD:
                room_sandbox = True; save_evidence()
                if c_sid: emit("sandbox_activated", {"risk": risk, "stage": stage}, to=c_sid)
            elif risk >= 0.75 and c_sid: emit("risk_update", {"risk": risk, "stage": stage, "level": "high"}, to=c_sid)
            elif risk >= 0.50 and c_sid: emit("risk_update", {"risk": risk, "stage": stage, "level": "medium"}, to=c_sid)
        if c_sid: emit("risk_bar", {"risk": risk, "stage": stage}, to=c_sid)
    elif role == "child":
        room_history.append({"sender": user.get("name","?"), "role": role, "text": "[IMAGE]", "score": 0, "flags": [], "ts": datetime.now().isoformat()})
        emit("image", {"name": user.get("name","?"), "avatar": user.get("avatar","🐼"), "image": data.get("image",""), "filename": data.get("filename","img"), "sid": sid, "risk": room_risk, "role": role, "flagged": False}, to=c_sid)
        print("[BLOCKED] Child image — predator did not receive it")

@socketio.on("typing")
def on_typing(data):
    sid = request.sid; user = users.get(sid, {})
    emit("typing", {"name": user.get("name",""), "typing": data.get("typing", False)}, to=ROOM, skip_sid=sid)

@socketio.on("disconnect")
def on_disconnect():
    global child_sid, predator_sid
    sid = request.sid; user = users.pop(sid, {})
    if user:
        if sid == child_sid:    child_sid    = None
        if sid == predator_sid: predator_sid = None
        emit("user_left", {"name": user.get("name","")}, to=ROOM)
        print(f"[-] {user.get('name')} ({user.get('role')}) left")

if __name__ == "__main__":
    print("=" * 60)
    print("  SafeGuard — http://0.0.0.0:3000")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=3000, debug=False)