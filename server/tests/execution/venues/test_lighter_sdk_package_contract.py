from __future__ import annotations

import importlib.metadata
import inspect


def test_official_lighter_sdk_is_exact_pinned_and_exposes_expected_signer_contract() -> None:
    import lighter

    assert importlib.metadata.version("lighter-sdk") == "1.1.2"
    parameters = inspect.signature(lighter.SignerClient.__init__).parameters
    assert {"url", "account_index", "api_private_keys", "chain_id"}.issubset(parameters)
