#!/usr/bin/env python3
"""
ethereum_assistant.py

Improved demo:
- DumbEthereumAssistant: safe transaction logger
- deploy_simple_contract: robust deploy helper (EIP-1559-aware), optional on-the-fly compilation via solcx
- EthereumGasOptimizer: toy PyTorch model (dummy; NOT production)

NOT FINANCIAL OR OPERATIONAL ADVICE.
Do NOT hardcode private keys in scripts. Use environment variables or secure vaults.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional, Tuple, Union

import torch
import torch.nn as nn
from eth_account import Account

WEI_PER_GWEI = 1e9
from hexbytes import HexBytes
from web3 import Web3, exceptions
from web3.middleware import geth_poa_middleware

# Optional solidity compiler
try:
    from solcx import compile_standard, install_solc, set_solc_version
    SOLCX_AVAILABLE = True
except Exception:
    SOLCX_AVAILABLE = False

# -------------------------
# Simple PyTorch toy model (NOT for production)
# -------------------------
class EthereumGasOptimizer(nn.Module):
    """
    Toy model: input current gas (gwei) -> suggested priorityFee (gwei).
    This is a placeholder demonstrating how a model might be integrated.
    The output is meaningless until trained on realistic labeled data.
    """
    def __init__(self, hidden: int = 8):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(1, hidden),
            nn.ReLU(),
            nn.Linear(hidden, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Scale sigmoid to e.g. 0..50 gwei
        return self.net(x) * 50.0

# -------------------------
# Dumb Assistant: transaction logger (improved)
# -------------------------
class DumbEthereumAssistant:
    def __init__(self, w3: Web3):
        self.w3 = w3

    def log_transaction(self, tx_hash: Union[str, bytes, HexBytes], timeout: int = 120, poll_latency: float = 2.0) -> Tuple[bool, str]:
        """
        Waits for transaction receipt and returns (success, message).
        tx_hash may be hex string (0x...), bytes, or HexBytes.
        """
        if tx_hash is None:
            return False, "No tx_hash provided"

        try:
            if isinstance(tx_hash, str):
                tx_hash = HexBytes(tx_hash)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout, poll_latency=poll_latency)
        except exceptions.TimeExhausted:
            return False, f"Timeout waiting for tx {tx_hash!r}"
        except Exception as e:
            return False, f"Error fetching receipt for {tx_hash!r}: {e}"

        status = getattr(receipt, "status", None)
        gas_used = getattr(receipt, "gasUsed", None)
        if status == 1:
            return True, f"Success: Tx {tx_hash.hex()} confirmed. Gas used: {gas_used}"
        else:
            return False, f"Transaction failed or reverted. Tx: {tx_hash.hex()} Receipt: {receipt}"

# -------------------------
# Helpers: EIP-1559 fee calculation utils
# -------------------------
def prepare_eip1559_fees(w3: Web3, tip_gwei: Optional[float] = None, max_fee_multiplier: float = 2.0) -> Tuple[int, int]:
    """
    Returns (maxPriorityFeePerGas, maxFeePerGas) in wei.
    If node supports baseFeePerGas (i.e., London/EIP-1559), compute based on pending block.
    tip_gwei optionally provided (gwei).
    """
    tip = int((tip_gwei or 2.0) * WEI_PER_GWEI)  # default 2 gwei
    try:
        pending = w3.eth.get_block("pending")
        base_fee = pending.get("baseFeePerGas", None)
        if base_fee is None:
            raise RuntimeError("No baseFeePerGas")
        max_fee = int(base_fee * max_fee_multiplier) + tip
        return tip, max_fee
    except Exception:
        # Fallback: pre-EIP-1559 gasPrice approach
        gas_price = w3.eth.gas_price  # in wei
        return tip, int(gas_price + tip)

# -------------------------
# Contract compilation (optional)
# -------------------------
def compile_contract(source: str, solc_version: str = "0.8.17") -> Tuple[str, list]:
    """
    Compile a single-file Solidity source and return bytecode and ABI.
    Requires python-solc-x. If not available, raises RuntimeError.
    """
    if not SOLCX_AVAILABLE:
        raise RuntimeError("solcx not available. Install python-solc-x to compile on the fly or provide ABI/bytecode.")
    # ensure solc installed
    try:
        install_solc(solc_version)
        set_solc_version(solc_version)
    except Exception:
        # continue if already installed / configured; compile_standard will fail if not available
        pass

    input_json = {
        "language": "Solidity",
        "sources": {"Contract.sol": {"content": source}},
        "settings": {
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}}
        },
    }
    compiled = compile_standard(input_json, allow_paths=".")
    contracts = compiled.get("contracts", {}).get("Contract.sol", {})
    if not contracts:
        raise RuntimeError("Compilation failed or no contracts found")
    # take first contract
    name, artifact = next(iter(contracts.items()))
    abi = artifact["abi"]
    bytecode = artifact["evm"]["bytecode"]["object"]
    return bytecode, abi

# -------------------------
# Deployment helper
# -------------------------
def deploy_simple_contract(
    w3: Web3,
    private_key: str,
    from_address: str,
    abi: Optional[list] = None,
    bytecode: Optional[str] = None,
    source: Optional[str] = None,
    gas_limit: Optional[int] = None,
    tip_gwei: Optional[float] = None,
    dry_run: bool = False,
) -> HexBytes:
    """
    Deploy a simple contract. You may provide (abi+bytecode) or a solidity source string.
    Returns tx_hash (HexBytes) for the deployment transaction.

    NOTE: This function will sign with private_key. Do not call on mainnet with real keys unless you understand costs.
    """
    if source and (abi is None or bytecode is None):
        bytecode, abi = compile_contract(source)

    if abi is None or bytecode is None:
        raise ValueError("abi and bytecode required (or provide source to compile)")

    account = Account.from_key(private_key)
    if account.address.lower() != from_address.lower():
        logging.warning("Provided from_address does not match private key-derived address")

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    # prepare transaction parameters
    nonce = w3.eth.get_transaction_count(from_address)
    chain_id = w3.eth.chain_id

    # EIP-1559 aware fee parameters
    try:
        priority_fee, max_fee = prepare_eip1559_fees(w3, tip_gwei=tip_gwei)
        tx_params = {
            "from": from_address,
            "nonce": nonce,
            "chainId": chain_id,
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee,
            "value": 0,
            "type": 2,  # EIP-1559
        }
    except Exception:
        # fallback to legacy gasPrice
        gas_price = w3.eth.gas_price
        tx_params = {
            "from": from_address,
            "nonce": nonce,
            "chainId": chain_id,
            "gasPrice": gas_price,
            "value": 0,
            "type": 0,
        }

    # Build transaction
    built = contract.constructor().build_transaction(tx_params)

    # Estimate gas if not provided
    if gas_limit is None:
        try:
            estimate = w3.eth.estimate_gas(built)
            # add small buffer
            built["gas"] = int(estimate * 1.2)
        except Exception as e:
            logging.warning("Gas estimate failed: %s; using provided/default gas", e)
            built["gas"] = gas_limit or 2_000_000
    else:
        built["gas"] = gas_limit

    # Dry-run support
    if dry_run:
        logging.info("Dry-run deployment tx prepared: %s", built)
        return HexBytes('0x00')

    # Sign and send
    signed = Account.sign_transaction(built, private_key)
    try:
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    except Exception as e:
        logging.error("Failed to send transaction: %s", e)
        raise
    logging.info("Deployed contract tx_hash=%s", tx_hash.hex())
    return tx_hash

# -------------------------
# High-level run function
# -------------------------
def run_smart_ai(
    rpc_url: str,
    private_key: str,
    from_address: str,
    sol_source: Optional[str] = None,
    use_poa: bool = False,
    dry_run: bool = False,
):
    """
    Connects to RPC, runs toy optimizer, and attempts to deploy contract.
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    if use_poa:
        # for some testnets (e.g., BSC, some dev chains) add POA middleware
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)

    if not w3.is_connected():
        raise RuntimeError("Unable to connect to RPC at " + rpc_url)

    # Toy model: get current effective priority fee (gwei) and predict suggestion
    try:
        # Use web3 to get current priority fee (if available) in gwei
        try:
            current_tip = w3.eth.max_priority_fee / WEI_PER_GWEI  # may raise in older web3 versions
        except Exception:
            # fallback assume 2 gwei
            current_tip = 2.0
        model = EthereumGasOptimizer()
        model.eval()
        with torch.no_grad():
            inp = torch.tensor([[float(current_tip)]], dtype=torch.float32)
            suggested_tip = float(model(inp).item())
        logging.info("Current tip (gwei): %.3f  Suggested tip (toy model, gwei): %.3f", current_tip, suggested_tip)
    except Exception as e:
        logging.warning("Gas optimizer failed: %s", e)
        suggested_tip = None

    # Prepare a simple contract source if none provided
    if sol_source is None:
        sol_source = """
        // SPDX-License-Identifier: MIT
        pragma solidity ^0.8.0;
        contract SimpleAIStorage {
            string public data;
            event DataSet(address indexed setter, string data);
            function setData(string memory _data) public {
                data = _data;
                emit DataSet(msg.sender, _data);
            }
        }
        """

    # Deploy contract (note: will cost gas if on real chain)
    tx_hash = deploy_simple_contract(
        w3=w3,
        private_key=private_key,
        from_address=from_address,
        source=sol_source,
        tip_gwei=suggested_tip if suggested_tip is not None else 2.0,
        dry_run=dry_run,
    )

    # Wait for receipt
    assistant = DumbEthereumAssistant(w3)
    success, message = assistant.log_transaction(tx_hash, timeout=180)
    return success, message, tx_hash.hex()

# -------------------------
# CLI
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="Ethereum assistant demo (safe defaults).")
    parser.add_argument("--rpc", dest="rpc_url", default=os.environ.get("ETH_RPC_URL"), help="RPC URL or set ETH_RPC_URL")
    parser.add_argument("--private-key-file", dest="pk_file", help="Path to file containing hex private key (DO NOT commit).")
    parser.add_argument("--private-key", dest="private_key", default=os.environ.get("ETH_PRIVATE_KEY"), help="Private key (hex). Prefer private-key-file or env var.")
    parser.add_argument("--from-address", dest="from_address", default=os.environ.get("ETH_FROM_ADDRESS"), help="Deployer address (derived from private key if omitted).")
    parser.add_argument("--use-poa", action="store_true", help="Add POA middleware (for some testnets).")
    parser.add_argument("--dry-run", action="store_true", help="Prepare tx but do not broadcast.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if not args.rpc_url:
        logging.error("RPC URL is required (--rpc or ETH_RPC_URL)")
        sys.exit(1)

    private_key = args.private_key
    if args.pk_file:
        try:
            with open(args.pk_file, "r") as fh:
                private_key = fh.read().strip()
        except Exception as e:
            logging.error("Failed to read private key file: %s", e)
            sys.exit(1)

    if not private_key:
        logging.error("Private key required (env ETH_PRIVATE_KEY, --private-key, or --private-key-file). Aborting.")
        sys.exit(1)

    # normalize private key
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    account = Account.from_key(private_key)
    from_address = args.from_address or account.address

    # Warning
    logging.warning("You are about to deploy a contract using address %s on RPC %s", from_address, args.rpc_url)
    logging.warning("Ensure you are on a testnet / have reviewed costs. Private keys should not be committed.")

    try:
        success, message, tx_hex = run_smart_ai(
            rpc_url=args.rpc_url,
            private_key=private_key,
            from_address=from_address,
            use_poa=args.use_poa,
            dry_run=args.dry_run,
        )
        if success:
            logging.info("Deployment succeeded: %s", message)
            print("Deployment tx:", tx_hex)
        else:
            logging.error("Deployment failed or reverted: %s", message)
            print("Deployment tx:", tx_hex)
    except Exception as e:
        logging.exception("Error in run_smart_ai: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
