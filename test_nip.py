import asyncio
from src.backend.api.services.nip_lookup import fetch_company_by_nip


async def run_test():
    print("🔍 Querying Polish Government Biała Lista API...")

    # Testing with Biedronka's official NIP (Jeronimo Martins Polska)
    test_nip = "779-10-11-327"

    result = await fetch_company_by_nip(test_nip)

    if result:
        print("✅ SUCCESS! Fetched Company Data:")
        print(f"   Name:    {result['name']}")
        print(f"   Address: {result['address']}")
        print(f"   NIP:     {result['nip']}")
    else:
        print("❌ Failed to fetch data.")


if __name__ == "__main__":
    asyncio.run(run_test())