import httpx
import logging
from datetime import date
from typing import Optional, Dict

logger = logging.getLogger(__name__)

async def fetch_company_by_nip(nip: str) -> Optional[Dict[str, str]]:
    """
    Fetches company details from the Polish Ministry of Finance (Biała Lista) API.
    Requires no API key and returns clean JSON
    """
    if not nip:
        return None

    clean_nip = "".join(filter(str.isdigit, nip))

    if len(clean_nip) != 10:
        logger.warning(f"Invalid NIP format: {clean_nip}")
        return None

    today = date.today().strftime("%Y-%m-%d")
    url = f"https://wl-api.mf.gov.pl/api/search/nip/{clean_nip}?date={today}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)

            if response.status_code == 200:
                data = response.json()
                result = data.get("result", {}).get("subject", {})

                if result:
                    name = result.get("name")
                    address = result.get("workingAddress") or result.get("residenceAddress")

                    if name:
                        return {
                            "name": name,
                            "address": address,
                            "nip": clean_nip
                        }
            else:
                logger.warning(f"Biała Lista API returned status {response.status_code} for NIP {clean_nip}")
    except httpx.RequestError as e:
        logger.error(f"Network error querying Biała Lista API: {e}")

    return None