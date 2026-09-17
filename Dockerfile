FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir -c requirements.lock . \
    && useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "bank_project.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
