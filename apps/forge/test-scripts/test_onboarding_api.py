import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fastapi.testclient import TestClient
from main import app
from db.repository import ForgeRepository

client = TestClient(app)


def test_onboarding_api_full_flow():
    print("=" * 80)
    print("TESTING ONBOARDING API (WEBSITE & ACCOUNTS DATABASE PERSISTENCE)")
    print("=" * 80)

    test_url = "https://onboard-api-verify.com"
    website_id = None

    try:
        # 1. Test POST /api/onboarding with website and two accounts
        print("\n[STEP 1] Testing POST /api/onboarding with website & 2 accounts...")
        payload = {
            "url": test_url,
            "is_active": True,
            "accounts": [
                {
                    "username": "admin@onboard-api-verify.com",
                    "password": "AdminSecurePassword!2026",
                    "role": "admin",
                    "credentials": {"pin": "9988", "mfa": False},
                    "is_active": True,
                },
                {
                    "username": "buyer@onboard-api-verify.com",
                    "password": "BuyerPassword#2026",
                    "role": "user",
                    "credentials": {"tier": "gold"},
                    "is_active": True,
                },
            ],
        }

        resp = client.post("/api/onboarding/", json=payload)
        print(f"  Status Code: {resp.status_code}")
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

        data = resp.json()
        print(f"  Response Message : {data['message']}")
        print(f"  Website ID       : {data['website']['id']}")
        print(f"  Website Domain   : {data['website']['domain']}")
        print(f"  Accounts Saved   : {len(data['accounts'])}")

        website_id = data["website"]["id"]
        assert data["status"] == "success"
        assert data["website"]["domain"] == "onboard-api-verify.com"
        assert len(data["accounts"]) == 2

        # 2. Verify persistence directly in PostgreSQL via ForgeRepository
        print("\n[STEP 2] Verifying PostgreSQL persistence via ForgeRepository...")
        db_website = ForgeRepository.get_website_by_id(website_id)
        assert db_website is not None, "Website was not found in PostgreSQL!"
        assert db_website["url"] == test_url

        db_accounts = ForgeRepository.list_accounts_for_website(website_id)
        assert len(db_accounts) == 2, f"Expected 2 accounts in DB, found {len(db_accounts)}"
        usernames = [a["username"] for a in db_accounts]
        assert "admin@onboard-api-verify.com" in usernames
        assert "buyer@onboard-api-verify.com" in usernames
        print(f"  [PASS] Verified in PostgreSQL: Website ID={website_id} with accounts: {usernames}")

        # 3. Test GET /api/onboarding/{website_id}
        print(f"\n[STEP 3] Testing GET /api/onboarding/{website_id}...")
        get_resp = client.get(f"/api/onboarding/{website_id}")
        assert get_resp.status_code == 200, f"Expected 200, got {get_resp.status_code}"
        details = get_resp.json()
        assert details["website"]["id"] == website_id
        assert details["account_count"] == 2
        print(f"  [PASS] Retrieved details: {details['website']['url']} with {details['account_count']} accounts.")

        # 4. Test POST /api/onboarding/{website_id}/accounts (Add 3rd account)
        print(f"\n[STEP 4] Testing POST /api/onboarding/{website_id}/accounts (Add manager)...")
        new_account_payload = {
            "username": "manager@onboard-api-verify.com",
            "password": "ManagerPassword$2026",
            "role": "manager",
            "credentials": {"department": "operations"},
            "is_active": True,
        }
        acc_resp = client.post(f"/api/onboarding/{website_id}/accounts", json=new_account_payload)
        assert acc_resp.status_code == 201, f"Expected 201, got {acc_resp.status_code}: {acc_resp.text}"
        new_acc = acc_resp.json()
        assert new_acc["username"] == "manager@onboard-api-verify.com"
        assert new_acc["website_id"] == website_id
        print(f"  [PASS] Added 3rd account: ID={new_acc['id']} (role={new_acc['role']})")

        # 5. Test GET /api/onboarding/{website_id}/accounts
        print(f"\n[STEP 5] Testing GET /api/onboarding/{website_id}/accounts...")
        list_acc_resp = client.get(f"/api/onboarding/{website_id}/accounts")
        assert list_acc_resp.status_code == 200
        accounts_list = list_acc_resp.json()
        assert len(accounts_list) == 3, f"Expected 3 accounts, got {len(accounts_list)}"
        print(f"  [PASS] Retrieved all {len(accounts_list)} accounts via API endpoint.")

    finally:
        # 6. Cleanup
        if website_id:
            print(f"\n[STEP 6] Cleaning up test website ID={website_id}...")
            ForgeRepository.delete_website(website_id)
            print("  [PASS] Cleaned up test data.")

    print("\n" + "=" * 80)
    print("SUCCESS: ALL ONBOARDING API TESTS PASSED!")
    print("=" * 80)


if __name__ == "__main__":
    test_onboarding_api_full_flow()
