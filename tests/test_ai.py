from __future__ import annotations

import json
from datetime import date

import httpx

from bot.ai import DEFAULT_MODELS, AIClient, ChatAnalysis, HomeworkDraft


def _client(handler) -> AIClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AIClient("sk-test", http=http)


def _text_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_analyze_parses_homework_and_reply():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.update(body)
        payload = {
            "homework": [{"day": "2026-10-12", "subject": "Алгебра", "text": "§ 5 № 1-10"}],
            "reply": "На понедельник: Алгебра § 5 № 1-10",
        }
        return _text_response(json.dumps(payload, ensure_ascii=False))

    client = _client(handler)
    analysis = await client.analyze(
        context="12.10: Алгебра: —",
        text="задали на завтра алгебру, §5 1-10",
        today=date(2026, 10, 11),
        addressed=True,
    )
    assert isinstance(analysis, ChatAnalysis)
    assert analysis.homework == [HomeworkDraft(date(2026, 10, 12), "Алгебра", "§ 5 № 1-10")]
    assert analysis.reply == "На понедельник: Алгебра § 5 № 1-10"
    system = seen["messages"][0]["content"]
    assert "адресовано боту" in system
    assert "Сообщение: задали на завтра" in seen["messages"][1]["content"]


async def test_analyze_tolerates_fences_and_junk_around_json():
    content = 'Вот ответ:\n```json\n{"homework": [], "reply": null}\n```\nГотово!'

    client = _client(lambda request: _text_response(content))
    analysis = await client.analyze(
        context="", text="просто болтовня", today=date(2026, 10, 11), addressed=False
    )
    assert analysis is not None
    assert analysis.homework == [] and analysis.reply is None


async def test_analyze_skips_homework_without_day_or_text():
    payload = {
        "homework": [
            {"day": None, "subject": "Физика", "text": "№ 12"},
            {"day": "2026-10-13", "subject": "", "text": "§ 1"},
            {"day": "2026-10-13", "subject": "Химия", "text": ""},
            {"day": "2026-10-13", "subject": "Химия", "text": "лаб. № 3"},
        ],
        "reply": None,
    }
    client = _client(lambda request: _text_response(json.dumps(payload, ensure_ascii=False)))
    analysis = await client.analyze(
        context="", text="химия лаб 3", today=date(2026, 10, 12), addressed=False
    )
    assert analysis is not None
    assert [(d.day, d.subject) for d in analysis.homework] == [(date(2026, 10, 13), "Химия")]


async def test_analyze_returns_none_on_garbage():
    client = _client(lambda request: _text_response("не смог понять сообщение"))
    assert await client.analyze(context="", text="???", today=date(2026, 10, 11), addressed=True) is None


async def test_analyze_cascades_past_non_json_model():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == DEFAULT_MODELS[0]:
            return _text_response("модель запуталась и не ответила JSON")
        return _text_response('{"homework": [], "reply": "не нашёл данных"}')

    client = _client(handler)
    analysis = await client.analyze(
        context="", text="какое дз?", today=date(2026, 10, 11), addressed=True
    )
    assert analysis is not None and analysis.reply == "не нашёл данных"
    assert calls == DEFAULT_MODELS[:2], "не-JSON ответ должен уводить на следующую модель"


def test_extract_json_tolerates_doubled_braces_and_prose():
    # nemotron любит оборачивать схему в лишние скобки
    assert AIClient._extract_json('{{"homework": [], "reply": null}}') == {"homework": [], "reply": None}
    assert AIClient._extract_json('Вот:\n{"a": 1}\nготово') == {"a": 1}
    assert AIClient._extract_json("```json\n{\"b\": 2}\n```") == {"b": 2}
    assert AIClient._extract_json("никакого json") is None


async def test_analyze_retries_when_long_message_yields_nothing():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["messages"][-1]["content"])
        if len(calls) == 1:
            return _text_response('{"homework": [], "reply": null}')  # флакон бесплатного роутинга
        return _text_response(
            '{"homework": [{"day": "2026-10-12", "subject": "Физика", "text": "№ 42"}], "reply": null}'
        )

    client = _client(handler)
    analysis = await client.analyze(
        context="",
        text="в общем на завтра по физике надо решить номер 42 из учебника",
        today=date(2026, 10, 11),
        addressed=False,
    )
    assert analysis is not None
    assert analysis.homework and analysis.homework[0].subject == "Физика"
    assert len(calls) == 2 and "Проверь ещё раз" in calls[1]


async def test_analyze_no_retry_for_short_banter():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _text_response('{"homework": [], "reply": null}')

    client = _client(handler)
    analysis = await client.analyze(
        context="", text="привет всем", today=date(2026, 10, 11), addressed=False
    )
    assert analysis is not None and analysis.homework == []
    assert len(calls) == 1, "короткую болтовню не тратим на повтор"


async def test_analyze_addressed_forces_reply_with_retry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["messages"][-1]["content"])
        if len(calls) == 1:
            return _text_response('{"homework": [], "reply": null}')  # модель промолчала
        return _text_response('{"homework": [], "reply": "Слушаю! Чем помочь?"}')

    client = _client(handler)
    analysis = await client.analyze(
        context="", text="что умеешь?", today=date(2026, 10, 11), addressed=True
    )
    assert analysis is not None and analysis.reply == "Слушаю! Чем помочь?"
    assert len(calls) == 2 and "Тебе обращаются" in calls[1]


async def test_analyze_addressed_prompt_demands_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _text_response('{"homework": [], "reply": "мяу, я на связи"}')

    client = _client(handler)
    await client.analyze(context="", text="мяу", today=date(2026, 10, 11), addressed=True)
    system = seen["messages"][0]["content"]
    assert "ОБЯЗАТЕЛЬНО заполни reply" in system


async def test_complete_disables_reasoning():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _text_response('{"homework": [], "reply": null}')

    client = _client(handler)
    await client.analyze(context="", text="просто текст", today=date(2026, 10, 11), addressed=False)
    assert seen["reasoning"] == {"enabled": False}, "thinking должен быть выключен, иначе JSON обрезается"


async def test_analyze_returns_none_on_http_error():
    client = _client(lambda request: httpx.Response(500, text="oops"))
    assert await client.analyze(context="", text="привет", today=date(2026, 10, 11), addressed=False) is None


async def test_model_fallback_on_429():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == DEFAULT_MODELS[0]:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return _text_response('{"homework": [], "reply": "ок"}')

    client = _client(handler)
    analysis = await client.analyze(
        context="", text="вопрос?", today=date(2026, 10, 11), addressed=True
    )
    assert analysis is not None and analysis.reply == "ок"
    assert calls == DEFAULT_MODELS[:2], "после 429 должна уйти следующая бесплатная модель"


async def test_solve_captcha_extracts_code_and_sends_image():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.update(body)
        return _text_response("Код: 4 8 2 1")

    client = _client(handler)
    code = await client.solve_captcha(b"PNGDATA")
    assert code == "4821"
    content = seen["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_solve_captcha_returns_none_when_all_models_fail():
    client = _client(lambda request: httpx.Response(429, json={}))
    assert await client.solve_captcha(b"IMG") is None


async def test_solve_captcha_rejects_safety_garbage():
    # openrouter/free иногда отвечает «User Safety: safe» вместо цифр
    client = _client(lambda request: _text_response("User Safety: safe"))
    assert await client.solve_captcha(b"IMG") is None
    assert client._models  # список моделей не пуст
    long_junk = _client(lambda request: _text_response("это точно не капча а длинная фраза"))
    assert await long_junk.solve_captcha(b"IMG") is None


async def test_zov_sends_roleplay_prompt_and_history():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _text_response("НУ ТИПО БАЗА 🖤")

    client = _client(handler)
    history = [{"role": "user", "content": "привет"}, {"role": "assistant", "content": "йоу"}]
    reply = await client.zov(user_text="как дела, Алина?", history=history)
    assert reply == "НУ ТИПО БАЗА 🖤"
    messages = seen["messages"]
    assert "Алины" in messages[0]["content"], "системный промт должен задавать роль"
    assert "ОГРАНИЧЕНИЯ" in messages[0]["content"]
    assert messages[1:3] == history
    assert messages[-1] == {"role": "user", "content": "как дела, Алина?"}
    assert seen["reasoning"] == {"enabled": False}
