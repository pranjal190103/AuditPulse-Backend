FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create application directories
RUN mkdir -p /app/data /app/media /app/staticfiles \
    && python manage.py collectstatic --noinput

EXPOSE 3538

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:3538", "--workers", "3", "--timeout", "120"]
