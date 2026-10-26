# Use Python base image from DockerHub
FROM python:3.11

# The base image's pip vendors a certifi that trusts the compromised eMudhra CAs (fixed in pip 26.2.2).
# The hash pin protects the download, which that old pip still performs.
RUN python -m pip install --no-cache-dir \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
RUN pip install bittensor==8.2.0 "certifi>=2026.10.19"

WORKDIR /app

RUN apt-get update && \
    apt-get install -y ca-certificates wget curl && \
    wget https://github.com/prometheus/prometheus/releases/download/v2.55.0/prometheus-2.55.0.linux-amd64.tar.gz && \
    echo "7a6b6d5ea003e8d59def294392c64e28338da627bf760cf268e788d6a8832a23  prometheus-2.55.0.linux-amd64.tar.gz" | sha256sum -c - && \
    tar xvzf prometheus-*.tar.gz && \
    mkdir /etc/prometheus && \
    mv prometheus-2.55.0.linux-amd64/prometheus /bin/ && \
    mv prometheus-2.55.0.linux-amd64/promtool /bin/ && \
    mv prometheus-2.55.0.linux-amd64/prometheus.yml /etc/prometheus/ && \
    rm -rf prometheus-*.tar.gz prometheus-2.55.0.linux-amd64 && \
    chown -R nobody:nogroup /etc/prometheus && \
    chown -R nobody:nogroup /etc/prometheus

RUN chown nobody: /etc/prometheus

COPY read_wallet_and_substitute_config.py /app/
RUN chown -R nobody: /app/

COPY --chmod=755 entrypoint.sh /
RUN chown nobody: /entrypoint.sh

RUN mkdir /nonexistent
RUN chown nobody: /nonexistent

RUN mkdir /wallets
RUN chown nobody: /wallets

#USER nobody

ENTRYPOINT ["/entrypoint.sh"]