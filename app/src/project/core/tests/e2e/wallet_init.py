from pathlib import Path

import bittensor

MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def main() -> None:
    keypair = bittensor.Keypair.create_from_mnemonic(MNEMONIC)
    wallet = bittensor.wallet(name="e2e", hotkey="default", path="/wallets")
    wallet.hotkey_file.set_keypair(keypair, encrypt=False, overwrite=True)
    Path("/wallets/ss58").write_text(keypair.ss58_address)
    print(keypair.ss58_address, flush=True)


if __name__ == "__main__":
    main()
