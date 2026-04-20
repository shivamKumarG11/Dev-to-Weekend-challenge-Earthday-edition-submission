# TERRA-STATE: VOX ATLAS v2.0
# Multi-stage Dockerfile for optimized production build

# Stage 1: Dependency builder
FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .

# Install dependencies into a virtualenv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Production image
FROM python:3.11-slim

# Create a non-root user for security
RUN groupadd -r vox_runner && useradd -r -s /bin/false -g vox_runner vox_runner

WORKDIR /app

# Copy the venv from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application files
COPY ./core /app/core
COPY ./services /app/services
COPY ./static /app/static
COPY app.py /app/

# Environment Variables
ENV PYTHONUNBUFFERED=1

# Transfer ownership to non-root user
RUN chown -R vox_runner:vox_runner /app

USER vox_runner

EXPOSE 8000

# Start Uvicorn via runner
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
