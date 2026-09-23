# BASE_IMAGE must be a previously imported, digest-pinned amd64 NVIDIA/vLLM image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG SOURCE_REVISION
ARG BASE_IMAGE_ID
RUN test -n "${SOURCE_REVISION}" && test -n "${BASE_IMAGE_ID}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/itsallsame/MinerU"
LABEL org.opencontainers.image.base.id="${BASE_IMAGE_ID}"

WORKDIR /opt/mineru
COPY wheelhouse/ /opt/wheelhouse/
COPY pyproject.toml README.md LICENSE.md /opt/mineru/
COPY mineru/ /opt/mineru/mineru/
COPY scripts/offline_package.py /opt/mineru/scripts/offline_package.py
COPY docker/worker/entrypoint.sh /opt/mineru/entrypoint.sh
RUN chmod 0755 /opt/mineru/entrypoint.sh

# Install the exact offline dependency closure before the fork source package.
# The wheelhouse and lock are produced together on a matching Linux amd64 Python.
RUN python3 -m pip install --break-system-packages --no-index --find-links=/opt/wheelhouse \
        --require-hashes -r /opt/wheelhouse/requirements.lock \
    && python3 -m pip install --break-system-packages --no-index --no-deps --no-build-isolation /opt/mineru \
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
