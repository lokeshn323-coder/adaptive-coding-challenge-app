from flask import Flask, request, jsonify, send_from_directory
from flask import render_template
import json
import os
import random
import time
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def gpt_feedback(question, user_code, error_message):
    """Return AI feedback when a solution is wrong."""
    if not os.getenv("OPENAI_API_KEY"):
        return "AI feedback unavailable (API key not configured)."
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a programming tutor."},
                {"role": "user", "content": f"""
Problem:
{question}

Student code:
{user_code}

Error:
{error_message}

Explain briefly:
1. Why the solution is incorrect
2. What concept is misunderstood
3. How to fix it (do NOT give full code)
"""}
            ],
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("GPT ERROR:", e)
        return "AI feedback temporarily unavailable."


def gpt_generate_challenge(level, used_topics=None):
    """Use GPT to dynamically generate a coding challenge for the given level.
    used_topics: list of function names already generated for this user at this
    level — passed into the prompt so GPT avoids repeating them.
    """
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        difficulty_desc = {
            "beginner": (
                "very simple — suitable for a complete Python beginner. "
                "Use ONLY: basic arithmetic (+, -, *, /), simple if/else, single variables. "
                "Do NOT use lists, loops, recursion, strings, or any data structures."
            ),
            "intermediate": (
                "moderate — suitable for a student who knows basic Python syntax. "
                "Use ONLY: string methods, list operations, simple for/while loops, basic if/else. "
                "Do NOT use recursion, nested loops, nested data structures, or complex algorithms."
            ),
            "hard": (
                "challenging — suitable for a student comfortable with Python. "
                "Use: recursion, nested data structures, algorithmic thinking, or dictionary operations."
            )
        }[level]

        # Build the avoid-topics instruction only if there are previous topics
        avoid_instruction = ""
        if used_topics:
            topics_str = ", ".join(used_topics)
            avoid_instruction = (
                f"\nIMPORTANT: Do NOT generate any of these topics already used: {topics_str}. "
                "You MUST choose a completely different programming concept."
            )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You are a coding challenge generator. "
                    "Always respond with valid JSON only, no markdown."
                )},
                {"role": "user", "content": f"""Generate a Python coding challenge that is {difficulty_desc}.{avoid_instruction}

Return ONLY a valid JSON object with EXACTLY these fields, no extra text:
{{
  "question": "Write a function name(...) that ...",
  "function": "name",
  "num_args": <integer number of arguments>,
  "tests": [[<input>, <expected>], [<input>, <expected>]],
  "hint": "One short hint."
}}

Rules:
- num_args: integer count of the function parameters (e.g. 1 or 2).
- tests: exactly 2 test cases.
  - If num_args == 1: input is a single value e.g. [5, 25] or ["hello", True].
  - If num_args == 2: input is a list of 2 values e.g. [[3,4], 7].
  - If num_args == 3: input is a list of 3 values e.g. [[1,2,3], 6].
- No imports required inside the student function.
"""}
            ],
            max_tokens=400
        )

        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        challenge = json.loads(raw)

        for key in ("question", "function", "tests", "hint"):
            if key not in challenge:
                raise ValueError(f"Missing key: {key}")

        if "num_args" in challenge and isinstance(challenge["num_args"], int) and challenge["num_args"] > 0:
            pass  
        else:
            import re
            fn = challenge["function"]
            m = re.search(rf"{fn}\(([^)]*)\)", challenge["question"])
            if m:
                args_str = m.group(1).strip()
                if args_str == "":
                    challenge["num_args"] = 0
                else:
                    challenge["num_args"] = len(args_str.split(","))
            else:
                challenge["num_args"] = 1 

        print(f"[CHALLENGE] fn={challenge['function']} num_args={challenge['num_args']} tests={challenge['tests']}")
        return challenge

    except Exception as e:
        print("GPT GENERATE ERROR:", e)
        return None


app = Flask(__name__, static_url_path='', static_folder='static')

DATA_FILE = "users.json"

def load_users():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_users():
    with open(DATA_FILE, "w") as f:
        json.dump(user_profiles, f, indent=2)

user_profiles         = load_users()
leaderboard           = {}
active_challenges     = {}  
challenge_start_times = {}   
hint_counts           = {}
# Tracks which fallback challenge indices each user has already seen per level
# Format: { "username": { "beginner": [0, 2], "intermediate": [1], "hard": [] } }
used_challenges       = {}
# Tracks GPT-generated function names per user per level to avoid repeats
# Format: { "username": { "beginner": ["add", "square"], "intermediate": [] } }
gpt_used_topics       = {}




CHALLENGES_FILE = "challenges.json"

def load_fallback_challenges():
    """
    Load fallback challenges from challenges.json.
    This keeps challenge data separate from application logic so admins
    can add or edit questions without modifying app.py.
    Falls back to an empty dict if the file is missing or corrupt.
    """
    if os.path.exists(CHALLENGES_FILE):
        try:
            with open(CHALLENGES_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARNING] Could not load {CHALLENGES_FILE}: {e}")
    return {"beginner": [], "intermediate": [], "hard": []}

FALLBACK_CHALLENGES = load_fallback_challenges()



LEVELS = ["beginner", "intermediate", "hard"]

def next_level(level, up=True):
    i = LEVELS.index(level)
    if up and i < len(LEVELS) - 1:
        return LEVELS[i + 1]
    if not up and i > 0:
        return LEVELS[i - 1]
    return level

def generate_challenge(level, user=None):
    """
    Try GPT first — passes the user's previously seen function names so GPT
    avoids repeating the same topic. Records each new GPT function name used.
    If GPT fails, pick a fallback challenge the user has NOT seen yet at this level.
    Once all fallbacks at this level are exhausted, reset and reshuffle so they
    cycle again rather than repeating in the same order.
    """
    # Build the list of GPT topics this user has already seen at this level
    used_topics = []
    if user:
        if user not in gpt_used_topics:
            gpt_used_topics[user] = {"beginner": [], "intermediate": [], "hard": []}
        used_topics = gpt_used_topics[user].get(level, [])

    challenge = gpt_generate_challenge(level, used_topics)
    if challenge:
        # Record this function name so it won't be repeated next time
        if user:
            fn = challenge.get("function", "")
            if fn and fn not in gpt_used_topics[user][level]:
                gpt_used_topics[user][level].append(fn)
            # Keep the history to a sensible size — drop oldest if over 20
            if len(gpt_used_topics[user][level]) > 20:
                gpt_used_topics[user][level] = gpt_used_topics[user][level][-20:]
        return challenge, True

    # --- Fallback: pick an unseen question for this user at this level ---
    pool = FALLBACK_CHALLENGES.get(level, [])
    if not pool:
        return None, False

    # Initialise tracking for this user if needed
    if user not in used_challenges:
        used_challenges[user] = {"beginner": [], "intermediate": [], "hard": []}
    if level not in used_challenges[user]:
        used_challenges[user][level] = []

    seen_indices = used_challenges[user][level]

    # Find indices not yet seen
    all_indices  = list(range(len(pool)))
    unseen       = [i for i in all_indices if i not in seen_indices]

    # If all questions have been seen, reset — they will cycle again in fresh order
    if not unseen:
        used_challenges[user][level] = []
        unseen = all_indices
        print(f"[CHALLENGE] All fallbacks exhausted for {user} at {level} — resetting pool")

    # Pick a random unseen index
    chosen_index = random.choice(unseen)
    used_challenges[user][level].append(chosen_index)

    return pool[chosen_index], False


def call_func(func, inp, num_args=1):
    """
    Call func using num_args to decide whether to unpack inp.
    Only unpacks if num_args > 1 AND inp is a list of exactly that length.
    This prevents unpacking a list that is itself the single argument.
    """
    if num_args > 1 and isinstance(inp, list) and len(inp) == num_args:
        return func(*inp)
    return func(inp)



def _record_correct(user):
    user_profiles[user]["correct_streak"] += 1
    user_profiles[user]["wrong_streak"]   = 0


def _record_wrong(user):
    user_profiles[user]["wrong_streak"]   += 1
    user_profiles[user]["correct_streak"] = 0

    if user_profiles[user]["wrong_streak"] >= 3:
        old_level = user_profiles[user]["level"]
        new_level = next_level(old_level, up=False)
        user_profiles[user]["level"]        = new_level
        user_profiles[user]["wrong_streak"] = 0
        # Reset seen questions at the new level so user gets fresh questions
        if user in used_challenges:
            used_challenges[user][new_level] = []
        if user in gpt_used_topics:
            gpt_used_topics[user][new_level] = []
        if new_level != old_level:
            print(f"[DEMOTION] {user}: {old_level} → {new_level}")


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate_challenge', methods=['POST'])
def generate():
    user = request.json['user_id']

    if user not in user_profiles:
        user_profiles[user] = {
            "level": "beginner",
            "correct_streak": 0,
            "wrong_streak": 0,
            "total_time_seconds": 0,
            "hints_used": 0
        }
    save_users()

    challenge, ai_generated = generate_challenge(user_profiles[user]["level"], user)
    active_challenges[user] = challenge

    challenge_start_times[user] = time.time()
    hint_counts[user] = 0

    return jsonify({
        "challenge":    challenge["question"],
        "level":        user_profiles[user]["level"],
        "message":      f"Current level: {user_profiles[user]['level'].capitalize()}",
        "ai_generated": ai_generated
    })


@app.route('/request_hint', methods=['POST'])
def request_hint():
    """Track hint requests and return the hint text."""
    user = request.json.get('user_id')
    if not user or user not in active_challenges:
        return jsonify({"error": "No active challenge"}), 400

    hint_counts[user] = hint_counts.get(user, 0) + 1
    user_profiles[user]["hints_used"] = user_profiles[user].get("hints_used", 0) + 1
    save_users()

    hint = active_challenges[user].get("hint", "No hint available.")
    return jsonify({
        "hint": hint,
        "hints_used_this_challenge": hint_counts[user]
    })


@app.route("/submit_solution", methods=["POST"])
def submit():
    data      = request.get_json()
    user      = data.get("user_id")
    code      = data.get("code", "")
    challenge = active_challenges.get(user)

    if not challenge:
        return jsonify({"error": "No active challenge. Please generate one first."}), 400

    func_name = challenge["function"]

    elapsed = 0
    if user in challenge_start_times:
        elapsed = round(time.time() - challenge_start_times[user])
        user_profiles[user]["total_time_seconds"] = (
            user_profiles[user].get("total_time_seconds", 0) + elapsed
        )

    hints_this_challenge = hint_counts.get(user, 0)

    local_env = {}
    try:
        exec(code, local_env, local_env)

        if func_name not in local_env:
            ai_feedback = gpt_feedback(
                challenge["question"], code,
                f"Function '{func_name}' is missing or incorrectly named."
            )
            _record_wrong(user)
            save_users()

            demoted = user_profiles[user]["wrong_streak"] == 0  
            return jsonify({
                "correct":      False,
                "feedback":     "Incorrect solution.",
                "error":        f"Function '{func_name}' not found.",
                "hint":         challenge["hint"],
                "ai_feedback":  ai_feedback,
                "time_taken":   elapsed,
                "hints_used":   hints_this_challenge,
                "wrong_streak": user_profiles[user]["wrong_streak"],
                "level":        user_profiles[user]["level"]
            })

        func = local_env[func_name]
        num_args = challenge.get("num_args", 1)

        for test in challenge["tests"]:
            inp, expected = test[0], test[1]
            try:
                actual = call_func(func, inp, num_args)
            except RecursionError:
                ai_feedback = gpt_feedback(
                    challenge["question"], code,
                    "Your function calls itself infinitely. Add a base case."
                )
                _record_wrong(user)
                save_users()
                return jsonify({
                    "correct":      False,
                    "feedback":     " Incorrect solution.",
                    "error":        "Maximum recursion depth exceeded.",
                    "hint":         challenge["hint"],
                    "ai_feedback":  ai_feedback,
                    "time_taken":   elapsed,
                    "hints_used":   hints_this_challenge,
                    "wrong_streak": user_profiles[user]["wrong_streak"],
                    "level":        user_profiles[user]["level"]
                })

            if actual != expected:
                ai_feedback = gpt_feedback(
                    challenge["question"], code,
                    f"For input {inp}, expected {expected} but got {actual}."
                )
                _record_wrong(user)
                save_users()
                return jsonify({
                    "correct":      False,
                    "feedback":     " Incorrect solution.",
                    "error":        f"For input {inp}, expected {expected} but got {actual}.",
                    "hint":         challenge["hint"],
                    "ai_feedback":  ai_feedback,
                    "time_taken":   elapsed,
                    "hints_used":   hints_this_challenge,
                    "wrong_streak": user_profiles[user]["wrong_streak"],
                    "level":        user_profiles[user]["level"]
                })

        
        base_points  = 10
        time_bonus   = max(0, 5 - elapsed // 30)    
        hint_penalty = hints_this_challenge * 2      # -2 points per hint
        points_earned = max(1, base_points + time_bonus - hint_penalty)

        leaderboard[user] = leaderboard.get(user, 0) + points_earned

        _record_correct(user)

        level_changed = False
        level_message = ""
        if user_profiles[user]["correct_streak"] >= 2:
            old_level = user_profiles[user]["level"]
            new_level = next_level(old_level, up=True)
            user_profiles[user]["level"]          = new_level
            user_profiles[user]["correct_streak"] = 0
            if new_level != old_level:
                level_changed = True
                level_message = f"🎉 Level up! Now at {new_level.capitalize()}"
                # Reset seen questions at the new level so user gets fresh questions
                if user in used_challenges:
                    used_challenges[user][new_level] = []
                if user in gpt_used_topics:
                    gpt_used_topics[user][new_level] = []

        save_users()
        return jsonify({
            "correct":       True,
            "feedback":      "✅ All test cases passed!",
            "score":         leaderboard[user],
            "points_earned": points_earned,
            "level":         user_profiles[user]["level"],
            "level_changed": level_changed,
            "level_message": level_message,
            "time_taken":    elapsed,
            "hints_used":    hints_this_challenge
        })

    except Exception as e:
        ai_feedback = gpt_feedback(challenge["question"], code, str(e))
        _record_wrong(user)
        save_users()
        return jsonify({
            "correct":      False,
            "feedback":     " Incorrect solution.",
            "error":        str(e),
            "hint":         challenge["hint"],
            "ai_feedback":  ai_feedback,
            "time_taken":   elapsed,
            "hints_used":   hints_this_challenge,
            "wrong_streak": user_profiles[user]["wrong_streak"],
            "level":        user_profiles[user]["level"]
        })



@app.route('/leaderboard')
def board():
    return jsonify(sorted(leaderboard.items(), key=lambda x: x[1], reverse=True))


@app.route('/user_stats/<user_id>')
def user_stats(user_id):
    if user_id not in user_profiles:
        return jsonify({"error": "User not found"}), 404
    p = user_profiles[user_id]
    return jsonify({
        "level":              p.get("level"),
        "correct_streak":     p.get("correct_streak", 0),
        "wrong_streak":       p.get("wrong_streak", 0),
        "total_time_seconds": p.get("total_time_seconds", 0),
        "hints_used":         p.get("hints_used", 0),
    })


if __name__ == '__main__':
    app.run(debug=True)
