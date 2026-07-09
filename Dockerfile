# Stage 1: Build the React frontend
FROM node:24-alpine AS frontend-builder
WORKDIR /app/frontend
# Copy package files and install
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
# Copy the rest of the frontend source
COPY frontend/ ./
# Build the static files
RUN npm run build

# Stage 2: Build the Python backend
FROM python:3.10-slim AS backend
WORKDIR /app

# System dependencies for python packages (e.g. pyspark, neo4j, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-jre \
    && rm -rf /var/lib/apt/lists/*

# Install python requirements
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt -r requirements-server.txt

# Copy backend source
COPY . .

# Copy built frontend files from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Expose the port Cloud Run expects
EXPOSE 8080

# Run the server in prod mode on port 8080
ENV APP_MODE=user
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
