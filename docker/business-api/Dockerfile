# A local, digest-pinned Linux amd64 base image; no model files enter this image.
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
RUN python3 -m pip install --break-system-packages --no-index --find-links=/opt/wheelhouse \
        --require-hashes -r /opt/wheelhouse/requirements.lock \
    && rm -rf /opt/wheelhouse
COPY mineru/ /opt/mineru/mineru/
RUN python3 -m pip install --break-system-packages --no-index --no-deps --no-build-isolation /opt/mineru

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

CMD ["python3", "-m", "mineru.business.api.server"]
