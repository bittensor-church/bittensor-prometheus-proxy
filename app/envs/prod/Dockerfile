ARG BASE_IMAGE=python:3.11-slim
# The base image's pip vendors a certifi that trusts the compromised eMudhra CAs (fixed in pip 26.2.2).
# The hash pin protects the download, which that old pip still performs.
ARG PIP_REQUIREMENT="pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"


FROM $BASE_IMAGE AS base-image
LABEL builder=true
ARG PIP_REQUIREMENT

WORKDIR /root/src/

RUN python3 -m pip install --no-cache-dir "$PIP_REQUIREMENT"
RUN pip3 install --no-cache-dir pdm==2.29.2

COPY pyproject.toml pdm.lock ./
RUN pdm lock --check

RUN apt-get update && apt-get install -y ca-certificates git

RUN pdm config python.use_venv false \
    && pdm export \
      --prod \
      --format requirements \
      --without-hashes \
      --output /tmp/requirements.txt \
    && rm -rf .venv __pypackages__ \
    && python3 -m venv /opt/runtime \
    && /opt/runtime/bin/pip3 install --no-cache-dir "$PIP_REQUIREMENT" \
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
ARG PIP_REQUIREMENT

RUN apt-get update \
  && apt-get install -y ca-certificates wget \
  && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir "$PIP_REQUIREMENT"

WORKDIR /root/src/
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/runtime/bin:$PATH

COPY --from=base-image /opt/runtime/ /opt/runtime/
COPY --from=base-image /root/src/ /root/src/




EXPOSE 8000

CMD ["./entrypoint.sh"]
