"""API подбрасывания монетки."""

import secrets
from datetime import timezone

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import db

app = Flask(__name__, static_folder=None)
app.json.ensure_ascii = False
app.json.sort_keys = False

ERROR_MESSAGES = {
    400: "Некорректный запрос",
    404: "Адрес не найден",
    405: "Метод не поддерживается",
    500: "Внутренняя ошибка сервера",
    503: "База данных недоступна",
}


def error_body(status):
    message = ERROR_MESSAGES.get(status, "Ошибка запроса")
    return {"code": status, "message": message}


def error_response(status):
    return jsonify(error=error_body(status)), status


def fresh_json(**data):
    """JSON-ответ, который нельзя брать из кэша."""
    response = jsonify(**data)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/flip")
def flip():
    # Flask сам добавляет HEAD ко всем GET-адресам: такой запрос не бросок.
    if request.method == "HEAD":
        return "", 200

    result = secrets.choice(("heads", "tails"))
    number, flipped_at = db.record_flip(result)

    return fresh_json(
        result=result,
        flip_number=number,
        timestamp=flipped_at.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )


@app.get("/stats")
def stats():
    return fresh_json(**db.get_stats())


@app.get("/health")
def health():
    try:
        db.check()
    except db.DatabaseUnavailable:
        response = fresh_json(
            status="error", database="unavailable", error=error_body(503)
        )
        return response, 503
    return fresh_json(status="ok", database="ok")


@app.get("/")
def index():
    return send_from_directory(app.root_path, "test.html")


@app.errorhandler(db.DatabaseUnavailable)
def handle_database_error(error):
    return error_response(503)


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
