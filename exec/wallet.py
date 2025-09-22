import asyncio
import json
import logging
import base64
from typing import Optional, Dict, Any
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.hash import Hash
from solders.rpc.requests import SendVersionedTransaction
from solders.rpc.responses import SendTransactionResp
import aiohttp


class SolanaWallet:
    """High-performance Solana wallet for memecoin execution"""
    
    def __init__(self, private_key_base58: str, rpc_url: str, backup_rpc_url: Optional[str] = None, jito_bundle_url: Optional[str] = None):
        self.keypair = Keypair.from_base58_string(private_key_base58)
        self.public_key = str(self.keypair.pubkey())
        self.rpc_url = rpc_url
        self.backup_rpc_url = backup_rpc_url
        self.jito_bundle_url = jito_bundle_url
        
        # High-performance HTTP client
        self.connector = aiohttp.TCPConnector(
            limit=50,
            limit_per_host=25,
            keepalive_timeout=30
        )
        
        self.timeout = aiohttp.ClientTimeout(
            total=30.0,     # 30s total for transaction processing
            connect=3.0,    # 3s to connect
            sock_read=25.0  # 25s to read (transactions can be slow)
        )
        
        self.session: Optional[aiohttp.ClientSession] = None
        self._nonce_cache: Optional[int] = None
        self._nonce_last_update = 0.0
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=self.timeout,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'MemecoinExecutor/1.0'
                }
            )
        return self.session
    
    async def close(self):
        """Clean shutdown"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def _rpc_call(self, method: str, params: list, use_backup: bool = False) -> Optional[Dict[str, Any]]:
        """Make RPC call with automatic fallback"""
        
        rpc_url = self.backup_rpc_url if use_backup and self.backup_rpc_url else self.rpc_url
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        
        try:
            session = await self._get_session()
            
            async with session.post(rpc_url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'error' in data:
                        logging.error(f"RPC error: {data['error']}")
                        return None
                    return data.get('result')
                else:
                    logging.error(f"RPC HTTP error: {response.status}")
                    return None
                    
        except Exception as e:
            logging.error(f"RPC call failed ({method}): {e}")
            # Try backup if main failed and we haven't tried backup yet
            if not use_backup and self.backup_rpc_url:
                return await self._rpc_call(method, params, use_backup=True)
            return None
    
    async def get_balance(self) -> Optional[float]:
        """Get SOL balance in SOL (not lamports)"""
        
        result = await self._rpc_call("getBalance", [self.public_key])
        if result:
            lamports = result.get('value', 0)
            return lamports / 1e9  # Convert to SOL
        return None
    
    async def get_recent_blockhash(self) -> Optional[str]:
        """Get recent blockhash for transactions"""
        
        result = await self._rpc_call("getLatestBlockhash", [{"commitment": "finalized"}])
        if result:
            return result.get('value', {}).get('blockhash')
        return None
    
    async def send_signed_transaction(
        self,
        signed_b64: str,
        max_retries: int = 3,
        skip_preflight: bool = True
    ) -> Optional[str]:
        """Submit a pre-signed transaction and return signature"""
        try:
            # Best-effort submit to Jito bundle endpoint (optional)
            if self.jito_bundle_url:
                try:
                    asyncio.create_task(self._submit_jito_bundle(signed_b64))
                except Exception:
                    pass
            for attempt in range(max_retries):
                params = [
                    signed_b64,
                    {
                        "skipPreflight": skip_preflight,
                        "preflightCommitment": "processed",
                        "encoding": "base64",
                        "maxRetries": 0
                    }
                ]
                result = await self._rpc_call("sendTransaction", params, use_backup=(attempt > 0))
                if result:
                    signature = result
                    logging.info(f"✅ Transaction sent: {signature}")
                    return signature
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
            logging.error("❌ All transaction send attempts failed")
            return None
        except Exception as e:
            logging.error(f"❌ Transaction send error: {e}")
            return None

    async def send_transaction(
        self,
        swap_transaction_b64: str,
        skip_preflight: bool = True
    ) -> Optional[str]:
        """Sign a base64-encoded swap transaction and submit it.

        This helper mirrors the buy-path flow (sign then submit) for the sell path
        which receives an unsigned transaction from Jupiter.
        """
        try:
            tx = VersionedTransaction.from_bytes(base64.b64decode(swap_transaction_b64))
            signed = VersionedTransaction(tx.message, [self.keypair])
            signed_b64 = base64.b64encode(bytes(signed)).decode('utf-8')
            return await self.send_signed_transaction(
                signed_b64,
                skip_preflight=skip_preflight,
            )
        except Exception as e:
            logging.error(f"❌ send_transaction error: {e}")
            return None

    async def _submit_jito_bundle(self, signed_b64: str) -> None:
        """Best-effort submission of the signed transaction to Jito block engine.
        This is fire-and-forget and does not affect control flow.
        """
        try:
            if not self.jito_bundle_url:
                return
            session = await self._get_session()
            payload = {"transactions": [signed_b64]}
            async with session.post(self.jito_bundle_url, json=payload) as resp:
                # Consider any 2xx as success; ignore errors
                if 200 <= resp.status < 300:
                    logging.debug("Jito bundle submitted")
                else:
                    text = await resp.text()
                    logging.debug(f"Jito submission non-2xx: {resp.status} - {text}")
        except Exception as e:
            logging.debug(f"Jito submission failed: {e}")
    
    async def confirm_transaction(
        self, 
        signature: str, 
        timeout_seconds: float = 60.0,
        commitment: str = "confirmed"
    ) -> bool:
        """Wait for transaction confirmation"""
        
        import time
        start_time = time.time()
        
        while time.time() - start_time < timeout_seconds:
            try:
                # getSignatureStatuses expects [ [signatures], config? ]
                params = [[signature]]
                result = await self._rpc_call("getSignatureStatuses", params)
                
                if result and result.get('value'):
                    status_info = result['value'][0]
                    if status_info:
                        if status_info.get('confirmationStatus') in ['confirmed', 'finalized']:
                            if status_info.get('err') is None:
                                logging.info(f"✅ Transaction confirmed: {signature}")
                                return True
                            else:
                                logging.error(f"❌ Transaction failed: {status_info['err']}")
                                return False
                
                await asyncio.sleep(2.0)  # Check every 2 seconds
                
            except Exception as e:
                logging.error(f"Confirmation check error: {e}")
                await asyncio.sleep(2.0)
        
        logging.warning(f"⏰ Transaction confirmation timeout: {signature}")
        return False
    
    async def get_token_balance(self, mint_address: str) -> Optional[float]:
        """Get token balance for specific mint"""
        
        try:
            params = [
                self.public_key,
                {
                    "mint": mint_address,
                    "commitment": "processed"
                }
            ]
            
            result = await self._rpc_call("getTokenAccountsByOwner", params)
            
            if result and result.get('value'):
                accounts = result['value']
                if accounts:
                    # Get the first account (should only be one for each mint)
                    account_info = accounts[0]['account']['data']['parsed']['info']
                    token_amount = account_info['tokenAmount']
                    return float(token_amount['uiAmount'] or 0)
            
            return 0.0
            
        except Exception as e:
            logging.error(f"Token balance fetch error: {e}")
            return None
    
    def sol_to_lamports(self, sol_amount: float) -> int:
        """Convert SOL to lamports"""
        return int(sol_amount * 1e9)
    
    def lamports_to_sol(self, lamports: int) -> float:
        """Convert lamports to SOL"""
        return lamports / 1e9
