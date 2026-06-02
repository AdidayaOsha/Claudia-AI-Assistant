import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.brain import Brain, SYSTEM_PROMPT


CONFIG = {"llm": {"primary_model": "claude-sonnet-4-6", "context_window": 10, "max_retries": 3}}


class TestBrainThink:
    def _make_brain(self):
        with patch("anthropic.Anthropic"), patch("openai.OpenAI"):
            brain = Brain(CONFIG)
        return brain

    def test_think_returns_string(self):
        brain = self._make_brain()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="It is 9:00 AM in Jakarta.")]
        brain._anthropic.messages.create.return_value = mock_response
        result = brain.think("What time is it?", [])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_think_uses_context_window(self):
        brain = self._make_brain()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Sure.")]
        brain._anthropic.messages.create.return_value = mock_response
        context = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        brain.think("latest question", context)
        call_args = brain._anthropic.messages.create.call_args
        messages_sent = call_args.kwargs.get("messages", call_args[1].get("messages", []))
        assert len(messages_sent) <= CONFIG["llm"]["context_window"] + 1

    def test_fallback_to_openai_on_anthropic_failure(self):
        brain = self._make_brain()
        brain._anthropic.messages.create.side_effect = Exception("API error")
        mock_oai_response = MagicMock()
        mock_oai_response.choices = [MagicMock(message=MagicMock(content="Fallback reply"))]
        brain._openai.chat.completions.create.return_value = mock_oai_response
        result = brain.think("Hello", [])
        assert isinstance(result, str)
        assert brain._openai.chat.completions.create.called

    def test_local_fallback_on_all_failures(self):
        brain = self._make_brain()
        brain._anthropic.messages.create.side_effect = Exception("fail")
        brain._openai.chat.completions.create.side_effect = Exception("fail")
        result = brain.think("hello", [])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_system_prompt_present(self):
        assert "CLAUDIA" in SYSTEM_PROMPT
        assert "Boss" in SYSTEM_PROMPT
        assert "Jakarta" in SYSTEM_PROMPT

    def test_build_messages_appends_user(self):
        brain = self._make_brain()
        context = [{"role": "assistant", "content": "Hi"}]
        messages = brain._build_messages("test input", context)
        assert messages[-1] == {"role": "user", "content": "test input"}

    def test_think_stream_yields_strings(self):
        brain = self._make_brain()
        brain._anthropic.messages.stream.return_value.__enter__ = MagicMock(return_value=MagicMock(
            text_stream=iter(["Hello", " Boss"])
        ))
        brain._anthropic.messages.stream.return_value.__exit__ = MagicMock(return_value=False)
        chunks = list(brain.think_stream("Hi", []))
        assert len(chunks) > 0
        for c in chunks:
            assert isinstance(c, str)
