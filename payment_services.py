import aiohttp
from dotenv import load_dotenv
import os
from typing import Optional, List, Tuple, Union, Dict, Set
import logging
import requests
import asyncio

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
CRYPTOPAY_TOKEN = os.getenv("CRYPTOPAY_TOKEN")
CRYPTOPAY_BASE = "https://testnet-pay.crypt.bot"
PORTMONE_TOKEN = os.getenv("PORTMONE_TOKEN")

async def create_crypto_invoice(amount: float, desc: str, payload: str) -> Tuple[
    Optional[dict], Optional[int], Optional[str]]:
    """Создает инвойс через CryptoPay API."""
    headers = {"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN, "Content-Type": "application/json"}
    body = {
        "currency_type": "fiat",
        "fiat": "RUB",
        "amount": f"{amount:.2f}",
        "accepted_assets": "USDT",
        "description": desc,
        "payload": payload
    }

    async with aiohttp.ClientSession() as session:  # Создаем сессию
        for endpoint in ("/api/createInvoice", "/api/create_invoice"):
            try:
                async with session.post(CRYPTOPAY_BASE + endpoint, json=body, headers=headers, timeout=10) as r:
                    if r.status == 200:
                        j = await r.json()
                        return j, j['result']['invoice_id'], j['result']['bot_invoice_url']
            except Exception as e:
                logger.error(f"Crypto invoice error: {e}")
        return None, None, None


async def check_crypto_invoice_status(invoice_id: int) -> Optional[str]:
    """
    Асинхронно проверяет статус инвойса через CryptoPay API.
    Использует run_in_executor, чтобы aiohttp не блокировал основной цикл бота.
    """
    headers = {"Crypto-Pay-API-Token": CRYPTOPAY_TOKEN}
    
    try:
        # Получаем текущий цикл событий
        loop = asyncio.get_running_loop()
        
        # Запускаем синхронный запрос в отдельном потоке
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{CRYPTOPAY_BASE}/api/getInvoices",
                json={"invoice_ids": [int(invoice_id)]},
                headers=headers,
                timeout=10
            )
        )
        
        if response.status_code != 200:
            logger.error(f"CryptoPay API Error: Status {response.status_code}, Body: {response.text}")
            return None
        
        data = response.json()
        
        # Безопасное извлечение данных
        if (data.get('ok') and
                'result' in data and
                'items' in data['result'] and
                len(data['result']['items']) > 0):
            return data['result']['items'][0]['status']
        
        return None
    
    except Exception as e:
        logger.error(f"Crypto status check exception: {e}")
        return None