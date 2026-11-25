FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Зависимости ставятся отдельным слоем, чтобы не переустанавливать их
# при каждом изменении кода.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Пользователь без прав root, под ним работает приложение.
RUN useradd --system --uid 10001 --no-create-home appuser

COPY app.py test.html ./

USER appuser

EXPOSE 5000

# Один процесс: счётчик бросков хранится в его памяти.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--no-control-socket", "--access-logfile", "-", "app:app"]
