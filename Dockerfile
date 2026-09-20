# Official Python image mirror, pinned to the same 3.12 base as the migrated application.
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN python -c "import tomllib; p=tomllib.load(open('pyproject.toml', 'rb')); print('\n'.join(p['project']['dependencies'] + p['project']['optional-dependencies']['graphrag'] + p['build-system']['requires']))" > /tmp/runtime-requirements.txt \
    && pip install --no-cache-dir -c requirements.lock -r /tmp/runtime-requirements.txt \
    && rm /tmp/runtime-requirements.txt \
    && useradd --create-home appuser
COPY src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
RUN mkdir -p /app/intake-data && chown appuser:appuser /app/intake-data
USER appuser
RUN python -c "import bank_project.main, graphrag.api, lancedb, pyarrow"
EXPOSE 8000
CMD ["uvicorn", "bank_project.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
