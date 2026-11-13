import os, sqlite3, secrets
from contextlib import closing
from flask import Flask, request, session, jsonify, render_template
import chess

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "dev-insecure-key-change-me"
DB = "chess.db"

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with closing(db()) as c, c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, nick TEXT UNIQUE, token TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, level INTEGER, fen TEXT, solution TEXT);
        CREATE TABLE IF NOT EXISTS results(user_id INTEGER, task_id INTEGER, solved INTEGER,
                                           PRIMARY KEY(user_id,task_id));
        """)
        if c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0:
            c.executemany("INSERT INTO tasks(level,fen,solution) VALUES(?,?,?)", [
                (1, "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4", "f3f7"),
                (1, "8/8/8/8/8/8/K7/7k w - - 0 1", "a2b2"),
                (2, "6k1/5ppp/8/8/8/8/1Q3PPP/4R1K1 w - - 0 1", "e1e8"),
            ])

def parse_move(board, raw):
    raw = (raw or "").strip()
    for parser in (board.parse_uci, board.parse_san):
        try:
            m = parser(raw)
            if m in board.legal_moves:
                return m
        except ValueError:
            continue
    return None

# Главная страница с интерфейсом
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["POST"])
def register():
    d = request.get_json(silent=True) or {}
    nick = (d.get("nick") or "").strip()
    if not nick:
        return jsonify(error="Введите ник"), 400
    token = secrets.token_urlsafe(24)
    try:
        with closing(db()) as c, c:
            cur = c.execute("INSERT INTO users(nick,token) VALUES(?,?)", (nick, token))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="Этот ник уже занят"), 409
    session["uid"] = uid
    return jsonify(token=token, nick=nick)

@app.route("/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    with closing(db()) as c:
        row = c.execute("SELECT id, nick FROM users WHERE token=?", (d.get("token"),)).fetchone()
    if not row:
        return jsonify(error="Неверный токен"), 401
    session["uid"] = row["id"]
    return jsonify(ok=True, nick=row["nick"])

@app.route("/tasks/<int:level>")
def tasks(level):
    with closing(db()) as c:
        rows = c.execute("SELECT id,fen FROM tasks WHERE level=?", (level,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/solve", methods=["POST"])
def solve():
    if not session.get("uid"):
        return jsonify(error="Сначала войдите по нику"), 401
    d = request.get_json(silent=True) or {}
    with closing(db()) as c:
        task = c.execute("SELECT id,fen,solution FROM tasks WHERE id=?", (d.get("task_id"),)).fetchone()
    if not task:
        return jsonify(error="Задача не найдена"), 404
    board = chess.Board(task["fen"])
    move = parse_move(board, d.get("move"))
    correct = bool(move) and move.uci() == task["solution"]
    with closing(db()) as c, c:
        c.execute("INSERT INTO results(user_id,task_id,solved) VALUES(?,?,?) "
                  "ON CONFLICT(user_id,task_id) DO UPDATE SET solved=MAX(solved,excluded.solved)",
                  (session["uid"], task["id"], int(correct)))
    return jsonify(ok=correct)

@app.route("/leaderboard")
def leaderboard():
    with closing(db()) as c:
        rows = c.execute("""SELECT u.nick, COALESCE(SUM(r.solved),0) pts FROM users u
                            LEFT JOIN results r ON r.user_id=u.id
                            GROUP BY u.id ORDER BY pts DESC LIMIT 50""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/admin/task", methods=["POST"])
def add_task():
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not admin_token or request.headers.get("X-Admin-Token") != admin_token:
        return jsonify(error="forbidden"), 403
    d = request.get_json(silent=True) or {}
    if not (d.get("fen") and d.get("solution")):
        return jsonify(error="fen and solution required"), 400
    try:
        board = chess.Board(d["fen"])
    except ValueError:
        return jsonify(error="invalid fen"), 400
    move = parse_move(board, d["solution"])
    if not move:
        return jsonify(error="invalid solution"), 400
    solution_uci = move.uci()
    with closing(db()) as c, c:
        c.execute("INSERT INTO tasks(level,fen,solution) VALUES(?,?,?)",
                  (d.get("level", 1), d["fen"], solution_uci))
    return jsonify(ok=True, solution=solution_uci)

init_db()

if __name__ == "__main__":
    app.run(debug=True)
