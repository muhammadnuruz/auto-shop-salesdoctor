# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY pyproject.toml ./
# Install third-party deps first (cached layer) by reading them from pyproject.
ARG INSTALL_DEV=false
RUN python - <<'PY' > /tmp/reqs.txt
import tomllib
p = tomllib.load(open("pyproject.toml", "rb"))["project"]
deps = list(p["dependencies"])
import os
if os.environ.get("INSTALL_DEV") == "true":
    deps += p["optional-dependencies"]["dev"]
print("\n".join(deps))
PY
RUN pip install -r /tmp/reqs.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 10001 app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
RUN chown app:app /app
COPY --chown=app:app . /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
