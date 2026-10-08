from __future__ import annotations

import asyncio

import backend.main as main


def test_simple_question_gets_direct_shape() -> None:
    assert main.conversation_response_shape([
        {"role": "user", "content": "¿Cuánto es 2x2?"}
    ]) == "SIMPLE_DIRECT"


def test_simple_how_question_stays_direct() -> None:
    assert main.conversation_response_shape([
        {"role": "user", "content": "¿Cómo funciona HTTP?"}
    ]) == "SIMPLE_DIRECT"


def test_statement_gets_conversational_shape() -> None:
    assert main.conversation_response_shape([
        {"role": "user", "content": "Estoy pensando en cambiar de trabajo."}
    ]) == "CONVERSATIONAL"


def test_complex_request_gets_necessary_expansion() -> None:
    assert main.conversation_response_shape([
        {
            "role": "user",
            "content": "Analiza cómo funciona este sistema, compara sus alternativas y explica "
            + ("qué consecuencias tendría cada una. " * 12),
        }
    ]) == "COMPLEX_NECESSARY"


def test_explicit_depth_overrides_normal_brevity() -> None:
    assert main.conversation_response_shape([
        {"role": "user", "content": "Explícame esto paso a paso y a fondo."}
    ]) == "EXPLICIT_DEPTH"


def test_real_dialogue_protocol_requires_web_first_and_logical_continuation() -> None:
    messages = [{"role": "user", "content": "¿Qué pasó hoy con la tecnología de baterías?"}]
    prompt = main.dialogue_policy_prompt(messages)

    assert "DEEP33 REAL DIALOGUE PROTOCOL v2" in prompt
    assert "busca primero información pública relevante en Internet" in prompt
    assert "respuesta final debe ser una síntesis original" in prompt
    assert "Haz como máximo una pregunta por turno" in prompt
    assert "Haz como máximo una pregunta por turno" in prompt


def test_fast_conversation_turn_avoids_forced_web_search() -> None:
    assert main.should_force_web([
        {"role": "user", "content": "Hola, hablemos de energía."}
    ]) is False
    assert main.should_force_web([
        {"role": "user", "content": " "}
    ]) is False


def test_web_search_uses_latest_user_turn_only() -> None:
    messages = [
        {"role": "user", "content": "Tema anterior sobre astronomía"},
        {"role": "assistant", "content": "Respuesta anterior"},
        {"role": "user", "content": "¿Qué novedades hay sobre baterías?"}
    ]
    assert main.latest_user_query(messages) == "¿Qué novedades hay sobre baterías?"


def test_policy_requires_real_contextual_questions_and_rejects_filler() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Estoy pensando en cambiar de trabajo."}
    ])

    assert "diálogo real" in prompt
    assert "preguntas deben surgir del contenido real" in prompt
    assert "como máximo una pregunta" in prompt
    assert "No conviertas cada intervención en interrogatorio" in prompt
    assert "¿quieres que te explique más?" in prompt
    assert "¿quieres que te ayude con eso?" in prompt
    assert "nunca inventes una pregunta" in prompt.lower()


def test_policy_for_simple_question_has_explicit_stop_rule() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "¿Cuántos planetas con vida inteligente conocemos?"}
    ])

    assert "detente cuando la pregunta quede realmente resuelta" in prompt
    assert "Una pregunta final está prohibida" in prompt
    assert "no conviertas una pregunta simple o factual en un informe" in prompt.lower()
    assert "esa respuesta debe aparecer en la primera frase" in prompt


def test_policy_does_not_require_continuation_after_closed_answer() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "¿Cuánto es 2x2?"}
    ])

    assert "Una respuesta factual, cerrada y autosuficiente normalmente termina sin pregunta" in prompt
    assert "Nunca añadas una pregunta únicamente para mantener artificialmente la conversación" in prompt


def test_policy_for_simple_question_prohibits_unneeded_follow_up() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "¿Qué es HTTP?"}
    ])

    assert "FORMA=SIMPLE_DIRECT" in prompt
    assert "respuesta directa" in prompt
    assert "Una pregunta final está prohibida" in prompt


def test_policy_for_conversation_keeps_one_contextual_question_limit() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Estoy pensando en cambiar de trabajo."}
    ])

    assert "FORMA=CONVERSATIONAL" in prompt
    assert "Reacciona primero a lo que acaba de decir el usuario" in prompt
    assert "una sola pregunta contextual" in prompt
    assert "real dialogue" not in prompt


def test_policy_for_complex_topic_expands_only_as_needed() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "¿Por qué ocurre esto? ¿Cómo funciona por dentro? ¿Qué consecuencias tiene?"}
    ])

    assert "FORMA=COMPLEX_NECESSARY" in prompt
    assert "Amplía solo lo necesario" in prompt
    assert "Resume primero la conclusión" in prompt
    assert "pregunta lógica" in prompt


def test_dialogue_policy_is_common_to_all_personalities() -> None:
    messages = [{"role": "user", "content": "Estoy pensando en cambiar de trabajo."}]
    policy = main.dialogue_policy_prompt(messages)

    for personality in main.PERSONALITIES:
        prompt = main.personality_prompt(personality) + "\n\n" + policy
        assert "DEEP33 REAL DIALOGUE PROTOCOL v2" in prompt
        assert "la personalidad modifica el estilo" in prompt


def test_prepare_messages_preserves_thread_and_attaches_dialogue_policy(monkeypatch) -> None:
    class FakeMemory:
        enabled = True

        async def context(self, session_id: str, memory_profile_id=None) -> dict:
            return {
                "session": {"session_id": session_id, "personality": "NEUTRO", "preferences": {}},
                "messages": [
                    {"role": "user", "content": "Me ofrecieron otro trabajo."},
                    {"role": "assistant", "content": "Eso cambia bastante el panorama."},
                ],
                "memories": [],
            }

    monkeypatch.setattr(main, "memory", FakeMemory())

    request = main.ChatRequest(
        messages=[
            {"role": "user", "content": "Estoy pensando en cambiarme."},
        ],
        personality="COMICO",
    )

    messages, selected = asyncio.run(main.prepare_messages(request, "dialogue-session"))

    assert selected == "COMICO"
    assert messages[0]["role"] == "system"
    assert "DEEP33 DIALOGUE BEHAVIOR PROTOCOL v6" in messages[0]["content"]
    assert "SHAPE_SELECTED=CONVERSATIONAL" in messages[0]["content"]
    assert "ACTIVE_PERSONALITY=COMICO" in messages[0]["content"]
    conversation_messages = [
        item for item in messages[1:]
        if item.get("role") != "system"
    ]
    assert conversation_messages == [
        {"role": "user", "content": "Me ofrecieron otro trabajo."},
        {"role": "assistant", "content": "Eso cambia bastante el panorama."},
        {"role": "user", "content": "Estoy pensando en cambiarme."},
    ]
    assert any(
        item.get("role") == "system" and "RELOJ DE EJECUCIÓN DE DEEP33" in item.get("content", "")
        for item in messages
    )


def test_latest_user_turn_controls_response_shape_not_old_history() -> None:
    messages = [
        {"role": "user", "content": "¿Qué significa este error?"},
        {"role": "assistant", "content": "Es un timeout."},
        {"role": "user", "content": "Estoy pensando en cambiar de trabajo."},
    ]

    assert main.conversation_response_shape(messages) == "CONVERSATIONAL"
    assert "FORMA=CONVERSATIONAL" in main.dialogue_policy_prompt(messages)


def test_policy_keeps_uncertainty_boundaries() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "No sé si esto pasó por el cambio de red."}
    ])

    assert "Distingue hechos, inferencias, posibilidades y desconocidos cuando sea necesario" in prompt
    assert "no los conviertas en secciones o etiquetas" in prompt
    assert "actualiza la conclusión" in prompt


def test_output_has_no_artificial_token_ceiling() -> None:
    assert main.output_token_limit("FAST") is None
    assert main.output_token_limit("BALANCED") is None
    assert main.output_token_limit("DEEP") is None


def test_output_ceiling_environment_cannot_reintroduce_truncation(monkeypatch) -> None:
    monkeypatch.setenv("AI_FAST_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("AI_BALANCED_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("AI_DEEP_MAX_OUTPUT_TOKENS", "4096")

    assert main.output_token_limit("FAST") is None
    assert main.output_token_limit("BALANCED") is None
    assert main.output_token_limit("DEEP") is None


def test_conversational_mode_does_not_force_a_follow_up_question() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Estoy pensando en cambiar de trabajo."}
    ])
    assert "Una sola pregunta contextual es opcional" in prompt

def test_response_policy_does_not_impose_artificial_shortness() -> None:
    simple = main.dialogue_policy_prompt([
        {"role": "user", "content": "¿Qué es HTTP?"}
    ])
    complex_prompt = main.dialogue_policy_prompt([
        {
            "role": "user",
            "content": "Analiza este problema y explica las causas, consecuencias y alternativas con suficiente detalle.",
        }
    ])

    assert "no recortes una precisión o explicación necesaria" in simple
    assert "nunca omitas información material" in simple
    assert "sin un límite artificial de palabras" in complex_prompt


def test_response_needs_web_retry_only_on_strong_knowledge_gap() -> None:
    assert main.response_needs_web_retry({
        "choices": [{"message": {"content": "No lo sé con certeza."}}]
    }) is True
    assert main.response_needs_web_retry({
        "choices": [{"message": {"content": "La respuesta es 4."}}]
    }) is False
    assert main.response_needs_web_retry({
        "choices": [{"message": {"content": "Es posible que ocurra mañana."}}]
    }) is False


def test_controversial_questions_force_deep_web_research() -> None:
    messages = [{"role": "user", "content": "¿La versión oficial de este hecho contradice la evidencia independiente?"}]
    assert main.should_force_web(messages) is True
    assert main.should_deep_web(messages, "CONSPIRANOICO") is True


def test_uncertainty_retry_uses_deep_web() -> None:
    assert main.response_needs_web_retry({
        "choices": [{"message": {"content": "No puedo determinarlo con la información disponible."}}]
    }) is True



def test_policy_prefers_integrated_conversation_over_labeled_analysis_blocks() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Quiero entender una teoría controvertida."}
    ])

    assert "La respuesta debe leerse como una conversación inteligente, no como un informe" in prompt
    assert "Patrón" in prompt and "Hipótesis" in prompt and "Especulación" in prompt
    assert "no los conviertas en secciones o etiquetas" in prompt
    assert "deja espacio útil para que la conversación pueda continuar" in prompt


def test_conspiranoico_does_not_require_labeled_reasoning_sections() -> None:
    prompt = main.personality_prompt("CONSPIRANOICO")

    assert "Do not present the response as labeled blocks" in prompt
    assert "EVIDENCE, HYPOTHESIS, SPECULATION, or VERDICT" in prompt


def test_long_context_statement_remains_conversational_without_explicit_analysis_request() -> None:
    message = (
        "Quiero dejarte todo este contexto para que entiendas el problema antes de responder. "
        + ("Esto agrega contexto al hilo. " * 40)
    )
    assert main.conversation_response_shape([{"role": "user", "content": message}]) == "CONVERSATIONAL"


def test_policy_makes_prose_dialogue_the_default_serialization() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Quiero dejarte este contexto para seguir conversando."}
    ])
    assert "Por defecto, la respuesta se serializa como diálogo" in prompt
    assert "Los encabezados, listas numeradas, viñetas y tablas están prohibidos por defecto" in prompt
    assert 'No empieces una respuesta con etiquetas como "Conclusión:"' in prompt


def test_complex_policy_does_not_force_report_structure() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Analiza este problema y dime por qué ocurre."}
    ])
    assert "Mantén la explicación integrada y conversacional" in prompt
    assert "No uses encabezados, listas numeradas, viñetas ni tablas salvo que el usuario" in prompt
