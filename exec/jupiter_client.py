import asyncio
import aiohttp
import json
import logging
from typing import Optional, Dict, Any
import base64


class JupiterClient:
    """Ultra-fast Jupiter API client optimized for memecoin execution"""
    
    def __init__(self, api_url: str = "https://quote-api.jup.ag/v6"):
        self.api_url = api_url.rstrip('/')
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Connection pooling for speed
        self.connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=50,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )
        
        # Timeout optimized for memecoin speed
        self.timeout = aiohttp.ClientTimeout(
            total=10.0,      # 10s total timeout
            connect=2.0,     # 2s to connect
            sock_read=5.0    # 5s to read response
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=self.timeout,
                headers={
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'User-Agent': 'MemecoinExecutor/1.0'
                }
            )
        return self.session
    
    async def close(self):
        """Clean shutdown"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def get_quote(
        self, 
        input_mint: str,
        output_mint: str, 
        amount: int,
        slippage_bps: int = 150,
        only_direct: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Get swap quote from Jupiter"""
        
        try:
            session = await self._get_session()
            
            params = {
                'inputMint': input_mint,
                'outputMint': output_mint,
                'amount': str(amount),
                'slippageBps': str(slippage_bps),
                'onlyDirectRoutes': 'true' if only_direct else 'false',
                'asLegacyTransaction': 'false'
            }
            
            url = f"{self.api_url}/quote"
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    # Jupiter v6 returns { "data": [ { inAmount, outAmount, priceImpactPct, ... } ] }
                    routes = data.get('data') if isinstance(data, dict) else None
                    if isinstance(routes, list) and routes:
                        return routes[0]
                    logging.warning("Quote response missing routes")
                    return None
                else:
                    error_text = await response.text()
                    logging.error(f"Quote failed: {response.status} - {error_text}")
                    return None
                    
        except asyncio.TimeoutError:
            logging.error("Quote request timed out")
            return None
        except Exception as e:
            logging.error(f"Quote request failed: {e}")
            return None
    
    async def get_swap_transaction(
        self,
        quote: Dict[str, Any],
        user_public_key: str,
        priority_fee_lamports: int = 1000
    ) -> Optional[str]:
        """Get swap transaction from Jupiter quote"""
        
        try:
            session = await self._get_session()
            
            swap_request = {
                'quoteResponse': quote,
                'userPublicKey': user_public_key,
                'prioritizationFeeLamports': priority_fee_lamports,
                'asLegacyTransaction': False,
                'dynamicComputeUnitLimit': True,
                'dynamicSlippage': {
                    'maxBps': 300  # Max 3% slippage protection
                }
            }
            
            url = f"{self.api_url}/swap"
            
            async with session.post(url, json=swap_request) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('swapTransaction')
                else:
                    error_text = await response.text()
                    logging.error(f"Swap transaction failed: {response.status} - {error_text}")
                    return None
                    
        except Exception as e:
            logging.error(f"Swap transaction request failed: {e}")
            return None
    
    async def get_price(self, mint_address: str) -> Optional[float]:
        """Get current price for a token (for position monitoring)"""
        
        try:
            session = await self._get_session()
            
            # Try price API v2 with mints parameter
            url = f"https://api.jup.ag/price/v2"
            params = {'mints': mint_address}
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    price_data = data.get('data', {}).get(mint_address)
                    if price_data and 'price' in price_data:
                        return float(price_data['price'])
                # Fallback to ids parameter (works for SOL symbol and some tokens)
            params = {'ids': 'SOL' if mint_address == 'So11111111111111111111111111111111111111112' else mint_address}
            async with session.get(url, params=params) as response2:
                if response2.status == 200:
                    data2 = await response2.json()
                    key = params['ids']
                    price_data2 = data2.get('data', {}).get(key)
                    if price_data2 and 'price' in price_data2:
                        return float(price_data2['price'])
            return None
                    
        except Exception as e:
            logging.debug(f"Price fetch failed for {mint_address}: {e}")
            return None
    
    def calculate_impact_and_amounts(self, quote: Dict[str, Any]) -> tuple[float, float, float]:
        """Extract key info from quote for decision making"""
        
        try:
            in_amount = float(quote['inAmount'])
            out_amount = float(quote['outAmount']) 
            price_impact = float(quote.get('priceImpactPct', 0))
            
            return in_amount, out_amount, price_impact
            
        except (KeyError, ValueError, TypeError) as e:
            logging.error(f"Failed to parse quote amounts: {e}")
            return 0.0, 0.0, 100.0  # Return bad values to reject trade
    
    def validate_quote_for_memecoin(
        self, 
        quote: Dict[str, Any], 
        max_impact_pct: float = 2.5
    ) -> tuple[bool, str]:
        """Validate quote is acceptable for memecoin trading"""
        
        try:
            in_amount, out_amount, price_impact = self.calculate_impact_and_amounts(quote)
            
            # Check if we got any output tokens
            if out_amount <= 0:
                return False, "No output tokens"
            
            # Check price impact
            if price_impact > max_impact_pct:
                return False, f"Price impact too high: {price_impact:.2f}%"
            
            # Check for reasonable exchange rate (basic sanity check)
            if in_amount <= 0:
                return False, "Invalid input amount"
            
            exchange_rate = out_amount / in_amount
            if exchange_rate <= 0 or exchange_rate > 1e12:  # Sanity bounds
                return False, f"Unreasonable exchange rate: {exchange_rate}"
            
            return True, "Quote OK"
            
        except Exception as e:
            return False, f"Quote validation error: {e}"


class PriceMonitor:
    """Fast price monitoring for positions"""
    
    def __init__(self, jupiter_client: JupiterClient):
        self.jupiter = jupiter_client
        self._price_cache: Dict[str, tuple[float, float]] = {}  # mint -> (price, timestamp)
        self._cache_ttl = 5.0  # 5 second cache
    
    async def get_current_price(self, mint_address: str, use_cache: bool = True) -> Optional[float]:
        """Get current price with optional caching"""
        
        import time
        now = time.time()
        
        # Check cache first
        if use_cache and mint_address in self._price_cache:
            cached_price, cached_time = self._price_cache[mint_address]
            if now - cached_time < self._cache_ttl:
                return cached_price
        
        # Fetch fresh price
        price = await self.jupiter.get_price(mint_address)
        
        # Update cache
        if price is not None:
            self._price_cache[mint_address] = (price, now)
        
        return price
    
    def clear_cache(self):
        """Clear price cache"""
        self._price_cache.clear()
