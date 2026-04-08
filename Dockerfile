FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache efficiency)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create reports output dir
RUN mkdir -p /app/reports

# Default: run the API server
# Override at runtime with: docker compose run agent python -m agent.main ...
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]