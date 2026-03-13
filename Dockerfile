FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source files
COPY bot.py .
COPY health_check.py .

# Expose health check port
EXPOSE 8000

# Run bot
CMD ["python", "bot.py"]
