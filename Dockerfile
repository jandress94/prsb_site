# Use a slim Python image for a smaller footprint
FROM python:3.10-slim-bullseye

# Set up environment variables
ARG APP_HOME="/app"
ENV POETRY_VERSION=2.0.1
ARG SOURCE_DIRECTORY="prsb"

# Set the working directory
WORKDIR ${APP_HOME}

# Install system dependencies and Poetry in one step for better caching
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    pip install --no-cache-dir "poetry==$POETRY_VERSION" && \
    rm -rf /var/lib/apt/lists/*

# Copy and install dependencies first (better caching)
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.in-project true && \
    poetry install --no-root --only=main

# Copy application source code
COPY --chown=root:root ${SOURCE_DIRECTORY}/ ./

# Set up environment variables for virtualenv
ENV VIRTUAL_ENV=${APP_HOME}/.venv \
    PATH=${APP_HOME}/.venv/bin:$PATH

# Expose port
EXPOSE 8000

# Run Gunicorn as the default command
CMD ["gunicorn", "prsb.wsgi:application", "--bind", "0.0.0.0:8000"]
