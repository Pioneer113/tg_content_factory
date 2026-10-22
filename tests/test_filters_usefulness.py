"""Детерминированный контент-рейтинг «полезное vs вода» (сценарий 4, 22.10.26).

Прод-путь без LLM: classify_post / score_channel / тир-правила.
Пороги черновые — их калибрует разметка LLM-судьи; тесты фиксируют механику,
не конкретные пороги (пороги меняются калибровкой — читаются из criteria.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from tg_agent.filters.criteria import (
    USEFULNESS_MIN_POSTS,
    USEFULNESS_TIER_USEFUL_OWN,
    USEFULNESS_TIER_USEFUL_WATER,
    USEFULNESS_TIER_WATER_REWRITE,
    USEFULNESS_TIER_WATER_SCORE,
)
from tg_agent.filters.usefulness import classify_post, score_channel


def _filler_channel(n: int = 40) -> list[tuple[str, bool]]:
    stamp = "В современном мире нейросети играют важную роль. "
    return [(stamp * 5, False) for _ in range(n)]


# --- classify_post ----------------------------------------------------------


def test_stamp_post_is_watery():
    _, watery = classify_post(
        "Не секрет, что ИИ меняет мир. Стоит отметить, что это важно."
    )
    assert watery


def test_concrete_post_is_not_watery():
    _, watery = classify_post(
        "Claude 5.5 стоит $3/млн токенов. Контекст 200k. Вот бенчмарк: 89.3%."
    )
    assert not watery


def test_forward_is_rewrite():
    authorship, _ = classify_post("я переписал этот пост", is_forward=True)
    assert authorship == "rewrite"


def test_bare_link_is_rewrite():
    authorship, _ = classify_post("https://example.com/news/123 — читайте тут")
    assert authorship == "rewrite"


def test_bare_link_hint_is_case_insensitive():
    authorship, _ = classify_post("WWW.EXAMPLE.COM/T.ME/SHORT")
    assert authorship == "rewrite"


def test_own_markers_beat_retell_markers():
    # Личное наблюдение + пересказное слово: первый сигнал — собственный опыт.
    authorship, _ = classify_post(
        "OpenAI announced a new model. Я проверил на своих задачах — мой опыт другой."
    )
    assert authorship == "own"


def test_retell_markers_use_canonical_key():
    # Ключи классификации = канонический AUTHORSHIP_KEYS (общий с судьёй).
    authorship, _ = classify_post("По данным инсайдеров, стало известно о слиянии.")
    assert authorship == "quality_retell"


def test_no_signals_is_unclassified():
    authorship, _ = classify_post("Нейросети обучаются на данных и решают задачи.")
    assert authorship is None


def test_empty_text_is_not_watery():
    authorship, watery = classify_post("")
    assert authorship is None
    assert not watery


# --- score_channel ----------------------------------------------------------


def test_score_channel_aggregates_shares():
    posts = [
        # 2 own (классифицированы), 1 quality_retell, 1 unclassified → знаменатель 3.
        ("Я протестировал новый релиз, мой опыт: работает быстрее.", False),
        ("Мне удалось собрать пайплайн за вечер.", False),
        ("По данным компании, стало известно о релизе.", False),
        ("Нейросети решают широкий круг задач.", False),
    ]
    score = score_channel(posts)
    assert score.n_posts == 4
    assert score.n_classified == 3
    assert abs(score.own_share - 200 / 3) < 0.1
    assert abs(score.quality_retell_share - 100 / 3) < 0.1
    assert score.rewrite_share == 0.0
    assert score.water_score == 0.0


def test_water_score_is_share_of_stamped_posts():
    posts = _filler_channel(n=40)
    posts.append(("Конкретика с цифрами и фактами, без штампов. " * 10, False))
    score = score_channel(posts)
    assert abs(score.water_score - 100 * 40 / 41) < 0.1


def test_forward_only_channel_is_water():
    score = score_channel([("ANY", True)] * 40)
    assert score.tier == "water"
    assert score.rewrite_share == 100.0


def test_to_dict_rounds_and_carries_tier():
    score = score_channel(_filler_channel(n=USEFULNESS_MIN_POSTS))
    d = score.to_dict()
    assert d["tier"] == "water"
    assert d["water_score"] == round(score.water_score, 1)


# --- tier rules (черновые пороги; границы читаются из criteria.py) -----------


def test_tier_no_data_below_min_sample():
    score = score_channel(_filler_channel(n=USEFULNESS_MIN_POSTS - 1))
    assert score.tier == "no_data"


def test_tier_water_on_high_water_score():
    score = score_channel(_filler_channel(n=USEFULNESS_MIN_POSTS))
    assert score.water_score >= USEFULNESS_TIER_WATER_SCORE
    assert score.tier == "water"


def test_tier_water_on_rewrite_share():
    posts = [(f"https://example.com/{i}", False) for i in range(USEFULNESS_MIN_POSTS)]
    score = score_channel(posts)
    assert score.rewrite_share >= USEFULNESS_TIER_WATER_REWRITE
    assert score.tier == "water"


def test_tier_useful_needs_low_water_and_own_share():
    # Вода бьёт авторскость: 100% own-постов, но со штампами → water.
    stamped_own = "Я попробовал новый агент; стоит отметить, мой опыт положительный."
    score = score_channel([(stamped_own, False)] * USEFULNESS_MIN_POSTS)
    assert score.water_score >= USEFULNESS_TIER_WATER_SCORE
    assert score.own_share >= USEFULNESS_TIER_USEFUL_OWN
    assert score.tier == "water"

    # Чистые own + quality_retell без штампов: own ≥ порога → useful.
    own_post = "Мой опыт: бенчмарк 89.3%, цена $3/млн, вот мои выводы."
    retell_post = "По данным отчёта, релиз состоялся, контекст вырос вдвое."
    n_own = 12
    posts = [(own_post, False)] * n_own
    posts += [(retell_post, False)] * (USEFULNESS_MIN_POSTS - n_own)
    score = score_channel(posts)
    assert score.water_score <= USEFULNESS_TIER_USEFUL_WATER
    assert score.own_share >= USEFULNESS_TIER_USEFUL_OWN
    assert score.tier == "useful"


def test_tier_mixed_middle():
    posts = [("По данным отчёта, релиз состоялся.", False)] * USEFULNESS_MIN_POSTS
    assert score_channel(posts).tier == "mixed"


def test_empty_channel_is_no_data():
    score = score_channel([])
    assert score.tier == "no_data"
    assert score.to_dict()["water_score"] == 0.0


# --- SQL-сэмпл (integration, :memory:) --------------------------------------


async def test_fetch_recent_post_samples(db):
    """Сэмпл: свежие (по message_id) сначала, форвард-флаг на месте."""
    now = datetime.now(tz=timezone.utc).isoformat()
    # Сэмпл channels-driven: каналы, чьи посты не внесены в channels, не видны.
    for cid in (-100, -200):
        await db.execute_write(
            "INSERT OR IGNORE INTO channels (channel_id) VALUES (?)", (cid,)
        )
    for i in range(3):
        await db.execute_write(
            "INSERT INTO messages (channel_id, message_id, text, date, message_kind) "
            "VALUES (?, ?, ?, ?, 'regular')",
            (-100, i, f"post {i}", now),
        )
    await db.execute_write(
        "INSERT INTO messages (channel_id, message_id, text, date, message_kind, "
        "forward_from_channel_id) VALUES (?, ?, ?, ?, 'regular', 42)",
        (-200, 1, "fwd", now),
    )
    # Пустой текст и нерегулярный kind в сэмпл не попадают (тот же предикат,
    # что у сэмпла судьи).
    await db.execute_write(
        "INSERT INTO messages (channel_id, message_id, text, date, message_kind) "
        "VALUES (?, ?, ?, ?, 'service')",
        (-100, 4, "pinned message", now),
    )

    samples = await db.filter_repo.fetch_recent_post_samples(sample_size=10)
    assert set(samples) == {-100, -200}
    assert [t for t, _ in samples[-100]] == ["post 2", "post 1", "post 0"]
    assert samples[-200] == [("fwd", True)]

    single = await db.filter_repo.fetch_recent_post_samples(
        channel_id=-200, sample_size=10
    )
    assert set(single) == {-200}
