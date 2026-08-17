FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y \
        libmagic-dev \
        git \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install them
COPY ./app/requirements /requirements
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /requirements/local.txt --upgrade

WORKDIR /app/app
