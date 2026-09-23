# BASE_IMAGE must be a previously imported, digest-pinned amd64 NVIDIA/vLLM image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/itsallsame/MinerU"

WORKDIR /opt/mineru
COPY wheelhouse/ /opt/wheelhouse/
COPY pyproject.toml README.md LICENSE.md /opt/mineru/
COPY mineru/ /opt/mineru/mineru/
COPY scripts/offline_package.py /opt/mineru/scripts/offline_package.py
COPY docker/worker/entrypoint.sh /opt/mineru/entrypoint.sh
RUN chmod 0755 /opt/mineru/entrypoint.sh

# No index access, no build-time model download, and the package comes from this fork.
RUN python3 -m pip install --no-index --find-links=/opt/wheelhouse '/opt/mineru[full]' \
    && rm -rf /opt/wheelhouse

ENV MINERU_HOME=/var/lib/mineru \
    MINERU_MODEL_BASE_DIR=/opt/mineru-models \
    MINERU_MODEL_SOURCE=local \
    MINERU_MODEL_SMALL_BACKEND=torch \
    MINERU_MODEL_VLM_ENGINE=vllm \
    MINERU_MODEL_VLM_SERVER_URL="" \
    MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING=false \
    MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE=false \
    HF_HOME=/var/lib/mineru/hf-cache \
    TORCH_HOME=/var/lib/mineru/torch-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

ENTRYPOINT ["/opt/mineru/entrypoint.sh"]
CMD ["python3", "-m", "mineru.doclib.app"]
