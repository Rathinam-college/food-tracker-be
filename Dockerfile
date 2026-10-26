# Use official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    lsb-release \
    wget \
    gnupg2 \
    && . /etc/os-release && echo "deb http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg \
    && apt-get update && apt-get install -y \
    postgresql-client \
    libpq-dev \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    libqpdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . /app/

# Expose port
EXPOSE 8000

# Start server
CMD ["sh", "-c", "python manage.py migrate --noinput && PYTHONPATH=. python scripts/setup_initial_data.py && gunicorn --bind 0.0.0.0:8000 --timeout 300 food_tracker_backend.wsgi:application"]
