import pytest

from automation_control.adapters.ebay import EbayNotConfigured, EbaySandboxReadAdapter


@pytest.mark.asyncio
async def test_ebay_defaults_to_sandbox_and_fails_closed_without_token():
    adapter = EbaySandboxReadAdapter()
    assert adapter.base_url == "https://api.sandbox.ebay.com"
    with pytest.raises(EbayNotConfigured):
        await adapter.get_listings()
    assert not hasattr(adapter, "update_listing")


def test_production_environment_targets_the_production_host():
    adapter = EbaySandboxReadAdapter(environment="production")
    assert adapter.base_url == "https://api.ebay.com"
    # still read-only regardless of environment
    assert not hasattr(adapter, "update_listing")
    assert not hasattr(adapter, "create_listing")

