let username = "";
let challengeTimer = null;
let secondsElapsed = 0;

const startBtn         = document.getElementById("startBtn");
const challengeText    = document.getElementById("challengeText");
const codeEditor       = document.getElementById("codeEditor");
const submitBtn        = document.getElementById("submitBtn");
const hintBtn          = document.getElementById("hintBtn");
const feedback         = document.getElementById("feedback");
const retryBtn         = document.getElementById("retryBtn");
const challengeSection = document.getElementById("challengeSection");
const solutionSection  = document.getElementById("solutionSection");
const levelInfo        = document.getElementById("levelInfo");
const timerDisplay     = document.getElementById("timerDisplay");
const hintBox          = document.getElementById("hintBox");
const aiGenBadge       = document.getElementById("aiGenBadge");
const statsBox         = document.getElementById("statsBox");


function startTimer() {
    secondsElapsed = 0;
    clearInterval(challengeTimer);
    timerDisplay.innerText = "⏱ Time: 0s";
    challengeTimer = setInterval(() => {
        secondsElapsed++;
        timerDisplay.innerText = `⏱ Time: ${secondsElapsed}s`;
    }, 1000);
}

function stopTimer() {
    clearInterval(challengeTimer);
}


startBtn.onclick = async () => {
    username = document.getElementById("username").value.trim();
    if (!username) { alert("Please enter your name."); return; }
    await getChallenge();
    await updateLeaderboard();
};

async function getChallenge() {
    const res  = await fetch('/generate_challenge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: username })
    });
    const data = await res.json();

    challengeText.innerText  = data.challenge;
    levelInfo.innerText      = data.message;
    feedback.innerText       = "";
    codeEditor.value         = "";
    hintBox.innerText        = "";
    statsBox.innerText       = "";
    solutionSection.style.display  = "none";
    challengeSection.style.display = "block";

    aiGenBadge.style.display = data.ai_generated ? "inline-block" : "none";
    startTimer();
}

submitBtn.onclick = async () => {
    stopTimer();

    const res  = await fetch('/submit_solution', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: username, code: codeEditor.value })
    });
    const data = await res.json();

    if (data.correct) {
        feedback.innerText = data.feedback;
        statsBox.innerText =
            `⏱ ${data.time_taken}s  |  💡 Hints: ${data.hints_used}  |  ` +
            `+${data.points_earned} pts  |  Total: ${data.score} pts`;

        solutionSection.style.display = "none";
        await updateLeaderboard();

        if (data.level_changed) {
            levelInfo.innerText = data.level_message;
            setTimeout(getChallenge, 3000);
        } else {
            setTimeout(getChallenge, 2000);
        }
    } else {
        let msg = data.feedback + "\n" + data.error;

        // Check if a demotion just happened (wrong_streak reset to 0 means demotion fired)
        const justDemoted = data.wrong_streak === 0 && data.level;

        if (justDemoted) {
            const lvl = data.level.charAt(0).toUpperCase() + data.level.slice(1);
            msg += `\n\n⬇ Level dropped to: ${lvl}! Loading a new question...`;
            levelInfo.innerText = `Current level: ${lvl}`;
        } else if (data.wrong_streak !== undefined && data.wrong_streak > 0) {
            msg += `\n\n⚠ Wrong streak: ${data.wrong_streak}/3  (3 in a row = level drop)`;
        }

        msg += "\n\nAI Feedback:\n" + (data.ai_feedback || "No AI feedback received.");
        feedback.innerText = msg;

        document.getElementById("justification").innerText = "💡 Hint: " + data.hint;
        solutionSection.style.display = "block";

        // If demoted, auto-load a fresh question at the new lower level after 3 seconds
        if (justDemoted) {
            solutionSection.style.display = "none";
            setTimeout(getChallenge, 3000);
        }
    }
};

hintBtn.onclick = async () => {
    const res  = await fetch('/request_hint', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: username })
    });
    const data = await res.json();
    if (data.hint) {
        hintBox.innerText =
            `💡 Hint: ${data.hint}  ` +
            `(${data.hints_used_this_challenge} hint${data.hints_used_this_challenge > 1 ? 's' : ''} used — costs 2 pts each)`;
    }
};

retryBtn.onclick = () => {
    solutionSection.style.display = "none";
    feedback.innerText = "";
    codeEditor.value   = "";
    hintBox.innerText  = "";
    statsBox.innerText = "";
    startTimer();
};

async function updateLeaderboard() {
    const res  = await fetch('/leaderboard');
    const data = await res.json();
    const list = document.getElementById("leaderboardList");
    list.innerHTML = "";
    data.forEach(([name, score], i) => {
        const li    = document.createElement("li");
        const medal = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : `${i + 1}.`;
        li.innerText = `${medal} ${name}: ${score} pts`;
        list.appendChild(li);
    });
}
