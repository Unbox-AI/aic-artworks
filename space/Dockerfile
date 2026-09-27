FROM python:3.11-slim

# git: the behaviorgpt SDK is installed from GitHub.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Spaces run the container as uid 1000.
RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
ENV PATH=/home/user/app/.venv/bin:$PATH \
    UV_COMPILE_BYTECODE=1 \
    HF_HOME=/home/user/.cache/huggingface

COPY --chown=user pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY --chown=user app ./app

EXPOSE 8501
# The app fetches its data from the dataset on first start (ART_CATALOG_ID).
CMD ["streamlit", "run", "app/app.py", "--server.port", "8501", \
     "--server.address", "0.0.0.0", "--server.headless", "true", \
     "--browser.gatherUsageStats", "false"]
