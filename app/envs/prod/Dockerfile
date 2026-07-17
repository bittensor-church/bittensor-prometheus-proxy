ARG BASE_IMAGE=python:3.11-slim


FROM $BASE_IMAGE AS base-image
LABEL builder=true

WORKDIR /root/src/

RUN pip3 install --no-cache-dir 'pdm>=2.12,<3'

COPY pyproject.toml pdm.lock ./
RUN pdm lock --check

RUN apt-get update && apt-get install -y git

RUN pdm config python.use_venv false \
    && pdm export \
      --prod \
      --format requirements \
      --without-hashes \
      --output /tmp/requirements.txt \
    && rm -rf .venv __pypackages__ \
    && python3 -m venv /opt/runtime \
    && /opt/runtime/bin/pip3 install \
      --no-cache-dir \
      --requirement /tmp/requirements.txt

ENV PATH=/opt/runtime/bin:$PATH

COPY ./app/src/ ./app/envs/prod/entrypoint.sh ./app/envs/prod/gunicorn.conf.py ./app/envs/prod/celery-entrypoint.sh ./app/envs/prod/prometheus-cleanup.sh /root/src/

RUN python3 -m compileall -b -f -q /root/
RUN python3 -m pip check
RUN python3 -c "import async_substrate_interface; import bittensor"
RUN CENTRAL_PROMETHEUS_PROXY_URL=localhost BITTENSOR_NETUID=1 BITTENSOR_WALLET_NAME=a BITTENSOR_WALLET_HOTKEY_NAME=b ENV=prod ENV_FILL_MISSING_VALUES=1 SECRET_KEY=dummy python3 manage.py collectstatic --no-input --clear


FROM $BASE_IMAGE AS secondary-image
LABEL builder=false

RUN apt-get update \
  && apt-get install -y wget \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /root/src/
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/runtime/bin:$PATH

COPY --from=base-image /opt/runtime/ /opt/runtime/
COPY --from=base-image /root/src/ /root/src/




EXPOSE 8000

CMD ["./entrypoint.sh"]
