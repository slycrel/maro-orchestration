FROM python:3.12-alpine

# Runtime deps: pyyaml for persona frontmatter parsing
RUN pip install --no-cache-dir pyyaml>=6.0

WORKDIR /app
COPY src/ /app/src/
COPY personas/ /app/personas/

# Workspace lives in a volume — never baked into the image
ENV POE_WORKSPACE=/data
ENV PYTHONPATH=/app/src

VOLUME /data

# Health check: verify Python + imports work
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD python3 -c "import sys; sys.path.insert(0,'/app/src'); import agent_loop; print('ok')" || exit 1

# Default: bootstrap status. Override CMD to run a specific service.
ENTRYPOINT ["python3", "/app/src/bootstrap.py"]
CMD ["status"]
