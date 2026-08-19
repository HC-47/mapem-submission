FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY contracts contracts
COPY scripts scripts
COPY tests tests
COPY submissions submissions

RUN useradd --create-home --uid 10001 validator \
    && chown -R validator:validator /app
USER validator

CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
