FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for pandas/numpy
RUN apt-get update && apt-get install -y build-essential --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install prefect prefect-aws

COPY . .

# Ensure output directories exist
RUN mkdir -p attorney_profiles Firms_details logs

CMD ["python", "prefect_flow.py"]