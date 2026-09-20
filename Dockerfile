FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN python -c "import tomllib; p=tomllib.load(open('pyproject.toml', 'rb')); print('\n'.join(p['project']['dependencies'] + p['build-system']['requires']))" > /tmp/runtime-requirements.txt \
    && pip install --no-cache-dir -c requirements.lock -r /tmp/runtime-requirements.txt \
    && rm /tmp/runtime-requirements.txt \
    && useradd --create-home appuser
COPY src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
RUN mkdir -p /app/intake-data && chown appuser:appuser /app/intake-data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "bank_project.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
