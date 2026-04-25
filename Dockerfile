FROM python:3.12-slim

WORKDIR /app

# Optional: corporate CA certificates
# Drop .crt files into docker/certs/ before building
COPY docker/certs/ /tmp/extra-certs/
RUN if ls /tmp/extra-certs/*.crt 1>/dev/null 2>&1; then \
        cp /tmp/extra-certs/*.crt /usr/local/share/ca-certificates/ && \
        update-ca-certificates; \
    fi && rm -rf /tmp/extra-certs
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# Install CPU PyTorch first (large layer, changes rarely)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install the project + Alembic sync driver
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir . psycopg2-binary

# Embedding model — pre-downloaded on host via: python docker/download_model.py
COPY docker/models/ /app/models/
ENV EMBED_MODEL_NAME=/app/models

# Copy remaining project files
COPY alembic_v3/ alembic_v3/
COPY alembic.ini .
COPY docs/ docs/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8765

ENTRYPOINT ["/entrypoint.sh"]
