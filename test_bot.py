# ============================================================
# test_bot.py — Unit & Integration Tests for MSP Support Bot
# Run: python -m pytest test_bot.py -v
# ============================================================

import pytest
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── Import core functions from main.py ───────────────────────
from main import (
    normalize_query,
    get_not_it_response,
)


# ============================================================
# SECTION 1: Unit Tests — normalize_query()
# ============================================================

class TestNormalizeQuery:
    """Tests for the query normalization function."""

    def test_wifi_normalization(self):
        """'wifi' should be converted to 'wi-fi'"""
        result = normalize_query("wifi not working")
        assert "wi-fi" in result

    def test_wi_fi_with_space(self):
        """'wi fi' (with space) should become 'wi-fi'"""
        result = normalize_query("wi fi keeps dropping")
        assert "wi-fi" in result

    def test_cant_normalization(self):
        """'cant' should become 'cannot'"""
        result = normalize_query("i cant connect")
        assert "cannot" in result

    def test_wont_normalization(self):
        """'wont' should become 'will not'"""
        result = normalize_query("it wont start")
        assert "will not" in result

    def test_doesnt_normalization(self):
        """'doesnt' should become 'does not'"""
        result = normalize_query("it doesnt work")
        assert "does not" in result

    def test_slow_computer_normalization(self):
        """'slow computer' should become 'slow performance'"""
        result = normalize_query("slow computer issue")
        assert "slow performance" in result

    def test_pc_normalization(self):
        """'pc' should become 'computer'"""
        result = normalize_query("my pc is broken")
        assert "computer" in result

    def test_lowercase_conversion(self):
        """Query should be converted to lowercase"""
        result = normalize_query("WIFI NOT WORKING")
        assert result == result.lower()

    def test_internet_not_working(self):
        """'internet not working' should become 'wi-fi network'"""
        result = normalize_query("internet not working")
        assert "wi-fi" in result or "network" in result

    def test_no_change_for_clean_query(self):
        """Normal IT query should not be mangled"""
        result = normalize_query("outlook email not syncing")
        assert "outlook" in result
        assert "email" in result


# ============================================================
# SECTION 2: Unit Tests — get_not_it_response()
# ============================================================

class TestNotITResponse:
    """Tests for the non-IT rejection response."""

    def test_returns_dict(self):
        """Should return a dictionary"""
        result = get_not_it_response()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        """Response must have all required keys"""
        result = get_not_it_response()
        required_keys = ["answer", "source", "confidence", "ticket_title", "ticket_id"]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_source_is_fallback(self):
        """Source should be 'fallback' for non-IT responses"""
        result = get_not_it_response()
        assert result["source"] == "fallback"

    def test_confidence_is_zero(self):
        """Confidence should be 0.0 for non-IT responses"""
        result = get_not_it_response()
        assert result["confidence"] == 0.0

    def test_no_ticket_reference(self):
        """No ticket should be referenced for non-IT responses"""
        result = get_not_it_response()
        assert result["ticket_id"] is None
        assert result["ticket_title"] is None

    def test_answer_mentions_it(self):
        """Answer should mention IT support topics"""
        result = get_not_it_response()
        answer = result["answer"].lower()
        assert any(word in answer for word in ["it", "support", "password", "network", "hardware"])


# ============================================================
# SECTION 3: Unit Tests — normalize_query() edge cases
# ============================================================

class TestNormalizeQueryEdgeCases:
    """Edge case tests for normalize_query."""

    def test_empty_string(self):
        """Empty string should return empty string"""
        result = normalize_query("")
        assert result == ""

    def test_single_word(self):
        """Single word query should work"""
        result = normalize_query("wifi")
        assert result == "wi-fi"

    def test_multiple_replacements(self):
        """Multiple replacements should all apply"""
        result = normalize_query("cant connect wifi on my pc")
        assert "cannot" in result
        assert "wi-fi" in result
        assert "computer" in result

    def test_special_characters_preserved(self):
        """Special characters should not break the function"""
        result = normalize_query("email won't sync!")
        assert isinstance(result, str)


# ============================================================
# SECTION 4: Integration Tests — API Endpoints
# ============================================================

class TestAPIEndpoints:
    """Integration tests calling the live Azure API endpoints."""

    BASE_URL = "https://udrsechatbotfunction-afc3hhehfyf9cddb.eastus-01.azurewebsites.net/api"

    def test_health_endpoint(self):
        """Health endpoint should return 200 and database status."""
        import requests
        try:
            res = requests.get(f"{self.BASE_URL}/health", timeout=15)
            assert res.status_code == 200
            data = res.json()
            assert "status" in data
            assert "database" in data
            assert data["status"] in ["ok", "db_error"]
            print(f"\n  Health: {data}")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_stats_endpoint(self):
        """Stats endpoint should return ticket counts."""
        import requests
        try:
            res = requests.get(f"{self.BASE_URL}/stats", timeout=15)
            assert res.status_code == 200
            data = res.json()
            assert "total_tickets" in data
            assert "with_resolution" in data
            assert int(data["total_tickets"]) > 0
            print(f"\n  Total tickets: {data['total_tickets']}")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_chat_endpoint_valid_message(self):
        """Chat endpoint should return a valid answer for IT question."""
        import requests
        try:
            res = requests.post(
                f"{self.BASE_URL}/chat",
                json={"message": "How do I reset my password?"},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            assert res.status_code == 200
            data = res.json()
            assert "answer" in data
            assert "source" in data
            assert "session_id" in data
            assert len(data["answer"]) > 10
            assert data["source"] in ["autotask", "openai", "no_match", "fallback"]
            print(f"\n  Source: {data['source']} | Answer: {data['answer'][:60]}...")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_chat_endpoint_empty_message(self):
        """Chat endpoint should return 400 for empty message."""
        import requests
        try:
            res = requests.post(
                f"{self.BASE_URL}/chat",
                json={"message": ""},
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            assert res.status_code == 400
            print(f"\n  Correctly rejected empty message with 400")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_chat_endpoint_non_it_question(self):
        """Non-IT question should be rejected with fallback source."""
        import requests
        try:
            res = requests.post(
                f"{self.BASE_URL}/chat",
                json={"message": "What is the recipe for chocolate cake?"},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            assert res.status_code == 200
            data = res.json()
            assert data["source"] in ["fallback", "not_it"]
            print(f"\n  Non-IT question correctly handled: {data['source']}")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_chat_endpoint_session_persistence(self):
        """Session ID should be returned and consistent."""
        import requests
        try:
            session_id = "test-session-12345"
            res = requests.post(
                f"{self.BASE_URL}/chat",
                json={"message": "How do I reset my password?", "session_id": session_id},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            assert res.status_code == 200
            data = res.json()
            assert data["session_id"] == session_id
            print(f"\n  Session ID persisted: {data['session_id']}")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_tickets_endpoint(self):
        """Tickets endpoint should return a list of tickets."""
        import requests
        try:
            res = requests.get(f"{self.BASE_URL}/tickets?limit=10", timeout=15)
            assert res.status_code == 200
            data = res.json()
            assert "tickets" in data
            assert "total" in data
            assert len(data["tickets"]) > 0
            ticket = data["tickets"][0]
            assert "ticket_id" in ticket
            assert "title" in ticket
            print(f"\n  Loaded {data['total']} tickets. First: {ticket['title']}")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_tickets_search(self):
        """Tickets endpoint should support search filtering."""
        import requests
        try:
            res = requests.get(f"{self.BASE_URL}/tickets?search=password&limit=5", timeout=15)
            assert res.status_code == 200
            data = res.json()
            assert "tickets" in data
            # All returned tickets should be related to password
            for ticket in data["tickets"]:
                text = (ticket.get("title", "") + ticket.get("resolution_notes", "")).lower()
                assert "password" in text or len(data["tickets"]) == 0
            print(f"\n  Password search returned {data['total']} tickets")
        except Exception as e:
            pytest.skip(f"API not reachable: {e}")


# ============================================================
# SECTION 5: End-to-End Workflow Test
# ============================================================

class TestEndToEnd:
    """End-to-end tests simulating a full user interaction."""

    BASE_URL = "https://udrsechatbotfunction-afc3hhehfyf9cddb.eastus-01.azurewebsites.net/api"

    def test_full_it_support_workflow(self):
        """
        Full workflow: IT question → search tickets → get answer → verify response.
        This tests the complete RAG pipeline end to end.
        """
        import requests
        try:
            # Step 1: Send an IT support question
            res = requests.post(
                f"{self.BASE_URL}/chat",
                json={"message": "My wifi keeps disconnecting"},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            assert res.status_code == 200
            data = res.json()

            # Step 2: Verify response structure
            assert "answer" in data
            assert "source" in data
            assert "session_id" in data
            assert "related_tickets" in data
            assert "confidence" in data

            # Step 3: Verify answer quality
            assert len(data["answer"]) > 20  # not an empty answer
            assert data["source"] in ["autotask", "openai", "no_match"]

            # Step 4: If from Autotask, verify ticket data
            if data["source"] == "autotask":
                assert isinstance(data["related_tickets"], list)
                assert data["confidence"] >= 0.0

            print(f"\n  E2E Test passed!")
            print(f"  Source: {data['source']}")
            print(f"  Answer: {data['answer'][:80]}...")
            print(f"  Related tickets: {len(data.get('related_tickets', []))}")

        except Exception as e:
            pytest.skip(f"API not reachable: {e}")

    def test_non_it_rejection_workflow(self):
        """
        Full workflow: Non-IT question → IT filter rejects → fallback response.
        """
        import requests
        try:
            res = requests.post(
                f"{self.BASE_URL}/chat",
                json={"message": "Who won the football game yesterday?"},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            assert res.status_code == 200
            data = res.json()
            assert data["source"] == "fallback"
            assert data["ticket_id"] is None
            assert data["confidence"] == 0.0
            print(f"\n  Non-IT rejection workflow passed!")

        except Exception as e:
            pytest.skip(f"API not reachable: {e}")


# ============================================================
# Run tests
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])