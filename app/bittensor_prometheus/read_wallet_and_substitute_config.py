import os
import pathlib

import bittensor

BITTENSOR_WALLET_NAME = os.environ.get("BITTENSOR_WALLET_NAME")
BITTENSOR_WALLET_HOTKEY_NAME = os.environ.get("BITTENSOR_WALLET_HOTKEY_NAME")


def get_wallet() -> bittensor.wallet:
    wallet = bittensor.wallet(
        name=BITTENSOR_WALLET_NAME,
        hotkey=BITTENSOR_WALLET_HOTKEY_NAME,
        path="/wallets",
    )
    wallet.hotkey_file.get_keypair()  # this raises errors if the keys are inaccessible
    return wallet


def read_and_substitute_config(hotkey: str):
    tmpl = pathlib.Path("/etc/prometheus/prometheus.yml.template").read_text()
    pathlib.Path("/etc/prometheus/prometheus.yml").write_text(tmpl.format(hotkey=hotkey))


def main():
    if not BITTENSOR_WALLET_NAME or not BITTENSOR_WALLET_HOTKEY_NAME:
        raise RuntimeError("You must set BITTENSOR_WALLET_NAME and BITTENSOR_WALLET_HOTKEY_NAME env vars")
    wallet = get_wallet()
    read_and_substitute_config(wallet.hotkey.ss58_address)


if __name__ == "__main__":
    main()
