# The agent service's container.
FROM python:3.10-slim

WORKDIR /app

# Install the libraries first: Docker caches this step, so it only reruns when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 0.0.0.0 = accept connections from the other containers, not only from inside this one.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
