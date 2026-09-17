import unittest
from unittest.mock import patch

from app import riot


class _FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload
        self.headers = {}

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Records every requested URL and serves canned responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requested_urls = []

    def get(self, url, headers=None):
        self.requested_urls.append(url)
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class RiotRegionRoutingTests(unittest.IsolatedAsyncioTestCase):
    """
    fetch_lol_rank_info / fetch_lol_rank_by_puuid / fetch_recent_role_counts all take the
    human region code ("EUW", not the platform id "euw1") and resolve continent/platform
    routing internally. Passing an already-resolved platform code back in (as a previous
    /linklol bug did) raises ValueError("Région inconnue") because platform ids aren't keys
    of REGION_TO_CONTINENT. These tests pin the expected URLs so that regression is caught
    immediately instead of surfacing as a hung Discord interaction.
    """

    async def test_fetch_lol_rank_info_uses_continent_then_platform_routing(self):
        fake_session = _FakeSession([
            _FakeResponse(200, {"puuid": "puuid-123"}),
            _FakeResponse(200, [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II", "leaguePoints": 40}]),
        ])
        with patch.object(riot, "_new_session", return_value=fake_session):
            result = await riot.fetch_lol_rank_info("RIOT-KEY", "EUW", "Alice", "EUW")

        self.assertEqual(result, ("GOLD", "II", 40, 1160.0, "puuid-123"))
        self.assertEqual(fake_session.requested_urls, [
            "https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Alice/EUW",
            "https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/puuid-123",
        ])

    async def test_platform_code_is_rejected_as_a_region_code(self):
        """Guards the exact regression: passing 'euw1' where 'EUW' is expected must fail loudly."""
        with self.assertRaises(ValueError):
            await riot.fetch_puuid_by_riot_id("RIOT-KEY", "euw1", "Alice", "EUW")


if __name__ == "__main__":
    unittest.main()
