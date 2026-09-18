# Evolved by JARVIS on 2026-09-15 11:32 for: crypto price
import json
import re

SKILL = {'name': 'crypto_price', 'description': 'Retrieve the current price of a cryptocurrency in a specified fiat currency, including 24‑hour change.', 'triggers': [r"\b(?:bitcoin|btc|ethereum|\beth\b|dogecoin|doge|solana|\bsol\b|litecoin|cardano|xrp|ripple|crypto(?:currency)?)\b",
             r"\bprice\s+of\b", r"\bhow\s+much\s+is\s+(?:a\s+)?(?:bitcoin|btc|ethereum|eth|crypto)"],
'version': 2, 'origin': 'evolved'}


def _extract_terms(request: str):
    """
    Return a tuple (crypto_id, fiat_code) extracted from the request.
    crypto_id is the CoinGecko identifier (e.g. "bitcoin").
    fiat_code is a three‑letter currency code (e.g. "usd").
    """
    crypto_match = re.search(
        r"\b(bitcoin|ethereum|litecoin|dogecoin|ripple|btc|eth|ltc|doge|xrp)\b",
        request,
        re.IGNORECASE,
    )
    fiat_match = re.search(r"\b(usd|eur|gbp|jpy|cny|aud|cad|chf|inr|rub)\b", request, re.IGNORECASE)

    crypto = crypto_match.group(1).lower() if crypto_match else ""
    fiat = fiat_match.group(1).lower() if fiat_match else ""

    # Map common abbreviations to CoinGecko IDs
    alias = {"btc": "bitcoin", "eth": "ethereum", "ltc": "litecoin", "doge": "dogecoin", "xrp": "ripple"}
    crypto = alias.get(crypto, crypto)

    return crypto, fiat


def run(request, context):
    crypto, fiat = _extract_terms(request)

    if not crypto:
        return "I couldn't determine which cryptocurrency you want the price for."
    if not fiat:
        return "I couldn't determine which fiat currency you want the price in."

    url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={crypto}&vs_currencies={fiat}&include_24hr_change=true"
    )
    response_text = context["actions"].http_request(url)

    if not response_text:
        return f"Failed to retrieve price data for {crypto} in {fiat.upper()}."

    # Parse JSON response
    data = json.loads(response_text)  # let JSON errors propagate
    crypto_data = data.get(crypto, {})
    price = crypto_data.get(fiat)
    change_key = f"{fiat}_24h_change"
    change = crypto_data.get(change_key)

    if price is None:
        return f"Price information for {crypto} in {fiat.upper()} is not available."

    # Build human‑readable answer
    answer = f"The current price of {crypto.capitalize()} is {price} {fiat.upper()}."
    if isinstance(change, (int, float)):
        answer += f" 24‑hour change: {change:+.2f}%."
    else:
        answer += " 24‑hour change data is unavailable."

    return answer
