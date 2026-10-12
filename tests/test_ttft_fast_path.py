from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import main


class FastPathClassificationTests(unittest.TestCase):
    def test_short_direct_realtime_commands_are_fast(self) -> None:
        samples = [
            "Dime las últimas noticias verificables de Chile hoy.",
            "Dame el precio actual del dólar en Chile.",
            "Verifica el clima actual de Santiago.",
            "Busca el resultado del partido de Chile hoy.",
        ]
        for text in samples:
            with self.subTest(text=text):
                messages = [{"role": "user", "content": text}]
                self.assertEqual(main.conversation_response_shape(messages), "SIMPLE_DIRECT")
                self.assertEqual(main.complexity_profile(messages)[2], "FAST")

    def test_explicit_deep_request_stays_deep(self) -> None:
        messages = [{
            "role": "user",
            "content": "Analiza en profundidad la arquitectura completa, paso a paso.",
        }]
        self.assertEqual(main.complexity_profile(messages)[2], "DEEP")

    def test_fast_model_override_is_isolated_to_fast_profile(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "AI_GATEWAY_MODEL_FAST": "inclusionai/ling-2.6-flash:free",
                "AI_GATEWAY_MODEL_BALANCED": "",
                "AI_GATEWAY_MODEL_DEEP": "",
            },
            clear=False,
        ):
            self.assertEqual(
                main.model_for_profile(None, "FAST"),
                "inclusionai/ling-2.6-flash:free",
            )
            self.assertEqual(main.model_for_profile(None, "BALANCED"), main.AI_GATEWAY_MODEL)
            self.assertEqual(main.model_for_profile(None, "DEEP"), main.AI_GATEWAY_MODEL)


if __name__ == "__main__":
    unittest.main()
