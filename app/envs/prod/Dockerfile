ARG BASE_IMAGE=python:3.11-slim


FROM $BASE_IMAGE AS base-image
LABEL builder=true

WORKDIR /root/src/

#RUN pip3 install --no-cache-dir 'pdm>=2.12,<3'

#COPY pyproject.toml pdm.lock ./
COPY pyproject.toml ./
#RUN pdm lock --check

RUN apt-get update && apt-get install -y git


#RUN pdm config python.use_venv False && pdm sync --prod --group :all
RUN pip3 install tomlkit
RUN python -c "import tomlkit, pathlib, subprocess; subprocess.check_call(['pip', 'install', '--user', '--no-cache-dir', *tomlkit.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']])"

#RUN mkdir -p /opt/ && mv __pypackages__/3.11/ /opt/pypackages/
ENV PATH=/opt/pypackages/bin:$PATH
ENV PYTHONPATH=/opt/pypackages/lib:$PYTHONPATH

COPY ./app/src/ ./app/envs/prod/entrypoint.sh ./app/envs/prod/gunicorn.conf.py ./app/envs/prod/celery-entrypoint.sh ./app/envs/prod/prometheus-cleanup.sh /root/src/

RUN python3 -m compileall -b -f -q /root/
RUN CENTRAL_PROMETHEUS_PROXY_URL=localhost BITTENSOR_NETUID=1 BITTENSOR_WALLET_NAME=a BITTENSOR_WALLET_HOTKEY_NAME=b ENV=prod ENV_FILL_MISSING_VALUES=1 SECRET_KEY=dummy python3 manage.py collectstatic --no-input --clear


FROM $BASE_IMAGE AS secondary-image
LABEL builder=false

RUN apt-get update \
  && apt-get install -y wget \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /root/src/
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/pypackages/bin:$PATH
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/opt/pypackages/lib:$PYTHONPATH

COPY --from=base-image /root/ /root/




EXPOSE 8000

CMD ["./entrypoint.sh"]
