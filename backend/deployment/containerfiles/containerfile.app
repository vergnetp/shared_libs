
# Build arguments
ARG APP_NAME
ARG ENVIRONMENT
ARG SHARED_LIBS_PATH
ARG PROJECT_BACKEND_PATH

FROM python:3.11-slim

# Use APP_NAME to create unique paths
WORKDIR /app/${APP_NAME}

# Copy only necessary directories
COPY ${SHARED_LIBS_PATH}/backend /app/${APP_NAME}/shared-libs/backend
COPY ${PROJECT_BACKEND_PATH} /app/${APP_NAME}/project/backend

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install requirements from both shared-libs and project
RUN pip install --no-cache-dir \
    -r /app/${APP_NAME}/shared-libs/backend/requirements.txt \
    -r /app/${APP_NAME}/project/backend/requirements.txt

# Set Python path to include both shared-libs and project-specific code
ENV PYTHONPATH="/app/${APP_NAME}/shared-libs/backend:/app/${APP_NAME}/project/backend:${PYTHONPATH}"

# Set environment variables from build arguments
ENV APP_NAME=${APP_NAME}
ENV ENVIRONMENT=${ENVIRONMENT}

# Create Supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/services.conf

# Expose port for API
EXPOSE 8000

# Health check for API
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Ensure log directories exist
RUN mkdir -p /var/log/supervisor

# Run both API and worker using Supervisor
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]