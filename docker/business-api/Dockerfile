# A local, digest-pinned Linux amd64 base image; no model files enter this image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG SOURCE_REVISION
ARG BASE_IMAGE_ID
ARG WEB_ASSET_MANIFEST_SHA256
RUN test -n "${SOURCE_REVISION}" && test -n "${BASE_IMAGE_ID}" && test -n "${WEB_ASSET_MANIFEST_SHA256}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/itsallsame/MinerU"
LABEL org.opencontainers.image.base.id="${BASE_IMAGE_ID}"
LABEL io.mineru.business.web.manifest.sha256="${WEB_ASSET_MANIFEST_SHA256}"

WORKDIR /opt/mineru
COPY wheelhouse/ /opt/wheelhouse/
COPY pyproject.toml README.md LICENSE.md /opt/mineru/
RUN python3 -m pip install --break-system-packages --no-index --find-links=/opt/wheelhouse \
        --require-hashes -r /opt/wheelhouse/requirements.lock \
    && rm -rf /opt/wheelhouse
COPY mineru/ /opt/mineru/mineru/
COPY business-web/dist/ /opt/mineru/business-web/dist/
RUN WEB_EXPECTED="${WEB_ASSET_MANIFEST_SHA256}" python3 -c 'import hashlib, json, os; from pathlib import Path; root = Path("/opt/mineru/business-web/dist"); p = root / "asset-manifest.json"; data = json.loads(p.read_text()); assert hashlib.sha256(p.read_bytes()).hexdigest() == os.environ["WEB_EXPECTED"], "Business Web manifest differs from build argument"; assert set(data) == {x.name for x in root.iterdir() if x.name != p.name}, "Business Web asset list differs"; assert all((root / name).is_file() and hashlib.sha256((root / name).read_bytes()).hexdigest() == digest for name, digest in data.items()), "Business Web asset hash differs"'
RUN python3 -m pip install --break-system-packages --no-index --no-deps --no-build-isolation /opt/mineru

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    MINERU_BUSINESS_WEB_ROOT=/opt/mineru/business-web/dist

CMD ["python3", "-m", "mineru.business.api.server"]
