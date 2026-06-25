FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python packages using PyTorch CPU wheel index
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple

# Copy application files
COPY . .

# Ensure python logs are printed in real-time
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "application.py"]
