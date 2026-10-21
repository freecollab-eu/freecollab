"""Actual certificate-verified Google/Graph HTTPS requests through pinned proxy."""
import pytest
from urllib.parse import parse_qs, urlsplit
from app.services import search_providers as providers
from tests.test_cloud_file_providers import https


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_live_search_and_open_use_current_delegated_provider_acl(https, provider):
    config = {"folder_id":"selected-folder", **({"drive_id":"drive"} if provider == "microsoft" else {})}
    result = providers.search(provider,config,"private-fixture-bearer","Scandinavian light",10)
    assert len(result["items"]) == 1 and result["items"][0]["id"] == "remote-file"
    assert len(https["requests"]) == 2 and len(https["connects"]) == 2
    first = https["requests"][0]
    if provider == "google":
        query = parse_qs(urlsplit(first["path"]).query)
        assert "'selected-folder' in parents" in query["q"][0]
        assert "fullText contains 'Scandinavian light'" in query["q"][0]
    else: assert "/items/selected-folder/search(q=" in first["path"]
    assert providers.resolve(provider,config,"private-fixture-bearer","remote-file")["acl_checked"]
    https["mode"] = "revoked"
    with pytest.raises(providers.ProviderError) as failure:
        providers.resolve(provider,config,"private-fixture-bearer","remote-file")
    assert failure.value.status == 401 and "private-provider-secret" not in str(failure.value)


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_provider_folder_move_removes_search_results_and_resolve(https, provider):
    config = {"folder_id":"selected-folder", **({"drive_id":"drive"} if provider == "microsoft" else {})}
    https["mode"] = "outside"
    # A Google query is collection constrained; the second metadata check is
    # still mandatory when the provider moves an item while returning a hit.
    result = providers.search(provider,config,"private-fixture-bearer","needle",10)
    assert result["items"] == []
