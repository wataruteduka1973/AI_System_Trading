# Runs the backend tests on Linux with the same locked dependency versions as CI
# (docker-compose.test.yml; docs/plans/cross-platform.md). Not a deployment image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /work

# The dependencies first, so editing the source does not reinstall them.
COPY requirements-lock.txt .
RUN pip install -r requirements-lock.txt

COPY . .
RUN pip install --no-deps -e .

CMD ["python", "-m", "pytest"]
