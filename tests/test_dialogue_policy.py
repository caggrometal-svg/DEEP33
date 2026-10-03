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


def test_policy_requires_real_contextual_questions_and_rejects_filler() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Estoy pensando en cambiar de trabajo."}
    ])

    assert "interlocutor activo" in prompt
    assert "preguntas deben surgir del contenido real" in prompt
    assert "como máximo una pregunta" in prompt
    assert "No conviertas cada intervención en interrogatorio" in prompt
    assert "¿quieres que te explique más?" in prompt
    assert "¿quieres que te ayude con eso?" in prompt
    assert "No uses preguntas de cierre" not in prompt


def test_policy_for_simple_question_prohibits_unneeded_follow_up() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "¿Qué es HTTP?"}
    ])

    assert "FORMA=SIMPLE_DIRECT" in prompt
    assert "No añadas contexto irrelevante" in prompt
    assert "si la respuesta ya resuelve el turno" in prompt


def test_policy_for_conversation_keeps_one_contextual_question_limit() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "Estoy pensando en cambiar de trabajo."}
    ])

    assert "FORMA=CONVERSATIONAL" in prompt
    assert "Reacciona primero a lo que acaba de decir el usuario" in prompt
    assert "una sola pregunta" in prompt
    assert "no debes terminar cada turno con una pregunta" in prompt


def test_policy_for_complex_topic_expands_only_as_needed() -> None:
    prompt = main.dialogue_policy_prompt([
        {"role": "user", "content": "¿Por qué ocurre esto? ¿Cómo funciona por dentro? ¿Qué consecuencias tiene?"}
    ])

    assert "FORMA=COMPLEX_NECESSARY" in prompt
    assert "Amplía solo lo necesario" in prompt
    assert "evita descargar información" in prompt


def test_dialogue_policy_is_common_to_all_personalities() -> None:
    messages = [{"role": "user", "content": "Estoy pensando en cambiar de trabajo."}]
    policy = main.dialogue_policy_prompt(messages)

    for personality in main.PERSONALITIES:
        prompt = main.personality_prompt(personality) + "\n\n" + policy
        assert "interlocutor activo" in prompt
        assert "la personalidad solo modifica el estilo" in prompt


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
    assert "DEEP33 DIALOGUE BEHAVIOR PROTOCOL v1" in messages[0]["content"]
    assert "SHAPE_SELECTED=CONVERSATIONAL" in messages[0]["content"]
    assert "ACTIVE_PERSONALITY=COMICO" in messages[0]["content"]
    assert messages[1:] == [
        {"role": "user", "content": "Me ofrecieron otro trabajo."},
        {"role": "assistant", "content": "Eso cambia bastante el panorama."},
        {"role": "user", "content": "Estoy pensando en cambiarme."},
    ]


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

    assert "HECHO, INFERENCIA, HIPÓTESIS y DESCONOCIDO" in prompt
    assert "sin inventar seguridad" in prompt
    assert "actualiza la conclusión" in prompt
