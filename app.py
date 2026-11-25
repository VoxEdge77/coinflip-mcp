"""API подбрасывания монетки."""

import secrets
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

app = Flask(__name__, static_folder=None)
app.json.ensure_ascii = False
app.json.sort_keys = False

# Счётчик живёт в памяти одного процесса и обнуляется при перезапуске.
# Блокировка не даёт двум потокам получить один и тот же номер.
flip_count = 0
flip_lock = threading.Lock()

ERROR_MESSAGES = {
    400: "Некорректный запрос",
    404: "Адрес не найден",
    405: "Метод не поддерживается",
    500: "Внутренняя ошибка сервера",
}


def error_response(status):
    message = ERROR_MESSAGES.get(status, "Ошибка запроса")
    return jsonify(error={"code": status, "message": message}), status


@app.get("/flip")
def flip():
    global flip_count

    # Flask сам добавляет HEAD ко всем GET-адресам: такой запрос не бросок.
    if request.method == "HEAD":
        return "", 200

    with flip_lock:
        flip_count += 1
        number = flip_count

    response = jsonify(
        result=secrets.choice(("heads", "tails")),
        flip_number=number,
        timestamp=datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/")
def index():
    return send_from_directory(app.root_path, "test.html")


@app.errorhandler(HTTPException)
def handle_http_error(error):
    response, status = error_response(error.code)
    # Для 405 стандарт требует перечислить разрешённые методы.
    allowed = getattr(error, "valid_methods", None)
    if allowed:
        response.headers["Allow"] = ", ".join(allowed)
    return response, status


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    # Подробности пишутся только в лог, клиенту они не отдаются.
    app.logger.exception("Необработанная ошибка")
    return error_response(500)
