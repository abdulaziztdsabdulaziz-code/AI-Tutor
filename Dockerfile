FROM python:3.11-slim

# Install system dependencies including Stockfish engine
RUN apt-get update && apt-get install -y --no-install-recommends \
    stockfish \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    STOCKFISH_PATH=/usr/games/stockfish \
    HOST=0.0.0.0 \
    PORT=8080

EXPOSE 8080

# Launch webhook server
CMD ["python", "-m", "bot.main"]
