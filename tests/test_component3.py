"""Unit tests for Component 3 features: Incremental Pruning, Disengaged Self-Healing, Synthetic Tool Completion, and Image Endpoints."""

import unittest
from unittest.mock import MagicMock, patch
from server.router import router
from server.prompt import messages_to_prompt
from server.schemas import ChatMessage, ImageGenerationRequest, ImageEditRequest
from server.api import image_generations, image_edits


class TestComponent3Features(unittest.TestCase):

    def test_slice_delta_prompt_incremental_pruning(self):
        """Test last_assistant_index incremental pruning in server/router.py."""
        messages = [
            ChatMessage(role="system", content="You are a helpful AI."),
            ChatMessage(role="user", content="Hello, who are you?"),
            ChatMessage(role="assistant", content="I am Microsoft E5 Copilot."),
            ChatMessage(role="user", content="Can you write some code?"),
        ]
        # _slice_delta_prompt should keep only the delta turn after the last assistant message
        pruned_prompt = router._slice_delta_prompt(messages)
        self.assertEqual(pruned_prompt, "Can you write some code?")

    def test_synthetic_tool_call_completion(self):
        """Test that messages_to_prompt injects guidance when role: 'tool' is the final message."""
        messages = [
            {"role": "user", "content": "What is the weather?"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "get_weather", "arguments": '{"city": "Seattle"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"temp": 72, "unit": "F"}'}
        ]
        prompt_text = messages_to_prompt(messages)
        self.assertIn("[Assistant Tool Call Output]", prompt_text)
        self.assertIn('{"temp": 72, "unit": "F"}', prompt_text)
        self.assertIn("Please analyze the tool result above and formulate your final response to the user.", prompt_text)

    @patch("server.api.pool.acquire_session")
    def test_image_generations_endpoint(self, mock_acquire):
        """Test /v1/images/generations formatting with mock Copilot reply."""
        mock_session = MagicMock()
        mock_reply = MagicMock()
        mock_reply.images = [MagicMock(url="https://copilot.microsoft.com/image1.png")]
        mock_reply.text = "Here is your image."
        mock_session.client.chat.return_value = mock_reply
        mock_acquire.return_value.__enter__.return_value = mock_session

        req = ImageGenerationRequest(prompt="A futuristic neon city", response_format="url")
        res = image_generations(req)
        self.assertIn("data", res)
        self.assertEqual(len(res["data"]), 1)
        self.assertEqual(res["data"][0]["url"], "https://copilot.microsoft.com/image1.png")

    @patch("server.api.pool.acquire_session")
    def test_image_edits_endpoint_with_markdown_fallback(self, mock_acquire):
        """Test /v1/images/edits extracting images from markdown text when images array is empty."""
        mock_session = MagicMock()
        mock_reply = MagicMock()
        mock_reply.images = []
        mock_reply.text = "Here is your edited image: ![edited](https://copilot.microsoft.com/edited.png)"
        mock_session.client.chat.return_value = mock_reply
        mock_acquire.return_value.__enter__.return_value = mock_session

        req = ImageEditRequest(prompt="Add sunglasses", image="https://example.com/base.png", response_format="url")
        with patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.content = b"fakeimagebytes"
            res = image_edits(req)
        self.assertIn("data", res)
        self.assertEqual(len(res["data"]), 1)
        self.assertEqual(res["data"][0]["url"], "https://copilot.microsoft.com/edited.png")


if __name__ == "__main__":
    unittest.main()
